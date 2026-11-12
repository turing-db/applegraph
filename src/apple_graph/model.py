"""Shared graph builder: node/edge records, timeline days, apps and people."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
UNIX_TO_APPLE = 978307200


def snapshot(source: Path, destination: Path) -> None:
    """SQLite backup includes committed WAL contents without modifying the source."""
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def system_day(apple_seconds) -> str | None:
    """Calendar day in this machine's local time zone for an Apple-epoch timestamp."""
    try:
        return (APPLE_EPOCH + timedelta(seconds=apple_seconds)).astimezone().strftime('%Y-%m-%d')
    except (OverflowError, ValueError, TypeError, OSError):
        return None


def apple_time(seconds) -> str | None:
    try:
        return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()
    except (OverflowError, ValueError, TypeError):
        return None


def local_day(seconds, offset=0) -> str | None:
    """Calendar day in the given UTC offset for an Apple-epoch timestamp."""
    try:
        return (APPLE_EPOCH + timedelta(seconds=seconds + (offset or 0))).strftime('%Y-%m-%d')
    except (OverflowError, ValueError, TypeError):
        return None


def split_days(start: float, end: float, offset=0) -> list[tuple[str, float, float]]:
    """Split an interval at local midnights so per-day totals never exceed the day."""
    shift = timedelta(seconds=offset or 0)
    pieces, cursor = [], start
    while True:
        local = APPLE_EPOCH + timedelta(seconds=cursor) + shift
        midnight = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - shift - APPLE_EPOCH).total_seconds()
        piece_end = min(end, midnight)
        pieces.append((local.strftime('%Y-%m-%d'), cursor, max(piece_end, cursor)))
        if end <= midnight:
            return pieces
        cursor = midnight


def union_seconds(intervals: list[tuple[float, float]]) -> float:
    """Total covered time, counting overlapping intervals once."""
    total, cursor = 0.0, None
    for start, end in sorted(intervals):
        if cursor is None or start > cursor:
            total += end - start
            cursor = end
        elif end > cursor:
            total += end - cursor
            cursor = end
    return total


PLACEHOLDER_NAME = re.compile(r'(Entity|Face cluster|Unknown contact|[a-z][a-z ]*) ?\d{0,40}')


def is_placeholder(name) -> bool:
    """True for generated display names such as ``person 1822…`` or ``Face cluster 12``."""
    return not isinstance(name, str) or not name.strip() or bool(PLACEHOLDER_NAME.fullmatch(name.strip()))


def normalize_handle(handle: str | None) -> str | None:
    """Canonical form for a phone number or email address."""
    if not isinstance(handle, str):
        return None
    text = handle.strip()
    if not text:
        return None
    for prefix in ('mailto:', 'tel:', 'sms:'):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    if '@' in text:
        return text.lower()
    digits = re.sub(r'\D', '', text)
    if digits and (text.startswith('+') or re.fullmatch(r'[\d\s().-]+', text)):
        if digits.startswith('00'):
            digits = digits[2:]
        return digits if len(digits) >= 7 else None
    return None


class Builder:
    """Accumulates TuringDB JSONL node and relationship records.

    Dense positive IDs avoid signed/unsigned issues with Apple's IDs. Nodes are
    keyed by tuples so every source can merge onto the same entity.
    """

    def __init__(self) -> None:
        self.nodes: dict[tuple, int] = {}
        self.node_records: list[dict] = []
        self.edge_records: list[dict] = []
        self.bundle_index: dict[str, tuple] = {}
        self.handle_index: dict[str, tuple] = {}
        self.identifier_index: dict[tuple[str, str], tuple] = {}
        self.suffix_index: dict[str, set[tuple]] = defaultdict(set)
        self.me_key: tuple | None = None
        self.stats: dict = {}

    # Nodes and edges -----------------------------------------------------------------
    def node(self, key: tuple, labels: list[str], **props) -> int:
        if key not in self.nodes:
            self.nodes[key] = len(self.nodes)
            self.node_records.append(dict(type='node', id=str(self.nodes[key]), labels=list(labels), properties=props))
        return self.nodes[key]

    def has(self, key: tuple) -> bool:
        return key in self.nodes

    def props(self, key_or_id) -> dict:
        node_id = key_or_id if isinstance(key_or_id, int) else self.nodes[key_or_id]
        return self.node_records[node_id]['properties']

    def labels(self, key_or_id) -> list[str]:
        node_id = key_or_id if isinstance(key_or_id, int) else self.nodes[key_or_id]
        return self.node_records[node_id]['labels']

    def edge(self, start: int, end: int, label: str, props: dict | None = None) -> None:
        record = dict(type='relationship', id=str(len(self.edge_records)), label=label,
                      start=dict(id=str(start)), end=dict(id=str(end)))
        if props:
            record['properties'] = props
        self.edge_records.append(record)

    def edge_counts(self) -> dict:
        return dict(sorted(Counter(record['label'] for record in self.edge_records).items()))

    def label_counts(self) -> Counter:
        return Counter(label for record in self.node_records for label in record['labels'])

    # Timeline ------------------------------------------------------------------------
    def day(self, date: str) -> int:
        key = ('day', date)
        if key not in self.nodes:
            when = datetime.strptime(date, '%Y-%m-%d')
            month = self.node(('month', date[:7]), ['Month'], name=date[:7], month=date[:7], year=int(date[:4]))
            node_id = self.node(key, ['Day'], name=date, date=date, weekday=when.strftime('%A'), month=date[:7])
            self.edge(node_id, month, 'IN_MONTH', dict(day_of_month=when.day))
        return self.nodes[key]

    # Apps ----------------------------------------------------------------------------
    def register_bundle(self, bundle: str, key: tuple) -> None:
        self.bundle_index.setdefault(bundle, key)

    def app(self, bundle: str, source: str = 'knowledgeC') -> int:
        if bundle in self.bundle_index:
            return self.nodes[self.bundle_index[bundle]]
        key = ('app', bundle)
        node_id = self.node(key, ['AppleEntity', 'Software'], name=bundle.rsplit('.', 1)[-1] or bundle,
                            bundle_id=bundle, apple_id=f'{source}:{bundle}', entity_type='software', source=source)
        self.bundle_index[bundle] = key
        return node_id

    # People --------------------------------------------------------------------------
    def register_handle(self, handle: str | None, key: tuple) -> None:
        canonical = normalize_handle(handle)
        if canonical is None:
            return
        self.handle_index.setdefault(canonical, key)
        if canonical.isdigit() and len(canonical) >= 9:
            self.suffix_index[canonical[-9:]].add(key)

    def lookup_handle(self, handle: str | None) -> tuple | None:
        canonical = normalize_handle(handle)
        if canonical is None:
            return None
        if canonical in self.handle_index:
            return self.handle_index[canonical]
        if canonical.isdigit() and len(canonical) >= 9:
            candidates = self.suffix_index.get(canonical[-9:], set())
            if len(candidates) == 1:
                return next(iter(candidates))
        return None

    def person(self, handle: str | None, name: str | None = None, source: str = 'unknown',
               labels: tuple[str, ...] = ('AppleEntity', 'Person'), **props) -> int:
        """Node for a person reached through a phone number or email, creating one if new."""
        key = self.lookup_handle(handle)
        if key is None:
            canonical = normalize_handle(handle) or (name or '').strip() or 'unknown'
            key = ('contact', source if not normalize_handle(handle) else 'handle', canonical)
            if key not in self.nodes:
                display = (name or '').strip() or (handle or '').strip() or 'Unknown contact'
                self.node(key, list(labels), name=display, apple_id=f'{source}:{canonical}', source=source,
                          entity_type='person' if 'Person' in labels else labels[-1].lower(), **props)
                if normalize_handle(handle):
                    self.props(key)['handle'] = normalize_handle(handle)
            self.register_handle(handle, key)
        node_props = self.props(key)
        if name and name.strip() and is_placeholder(node_props.get('name')):
            node_props['name'] = name.strip()
        return self.nodes[key]

    @property
    def me(self) -> int:
        if self.me_key is None:
            self.me_key = ('contact', 'me', 'me')
            self.node(self.me_key, ['AppleEntity', 'Person', 'Me'], name='Me', apple_id='me', entity_type='person', source='derived')
        return self.nodes[self.me_key]

    def set_me(self, key: tuple) -> None:
        self.me_key = key
        if 'Me' not in self.labels(key):
            self.labels(key).append('Me')

    # Places and groups ----------------------------------------------------------------
    def place(self, name: str, source: str) -> int:
        key = ('place', name.strip().lower())
        return self.node(key, ['AppleEntity', 'Place'], name=name.strip(), apple_id=f'{source}:{name.strip()}',
                         entity_type='place', source=source)

    def web_domain(self, host: str) -> int:
        return self.node(('web', host), ['WebDomain'], name=host, domain=host)

    # Manual names ----------------------------------------------------------------------
    def apply_name_overrides(self, overrides: dict) -> int:
        """Rename nodes from a mapping of apple_id, photos_person_uuid, handle or bundle_id to name."""
        wanted = {str(k).strip(): str(v).strip() for k, v in overrides.items() if str(v).strip()}
        renamed = 0
        for record in self.node_records:
            props = record['properties']
            for field in ('apple_id', 'photos_person_uuid', 'handle', 'bundle_id', 'contact_id'):
                value = props.get(field)
                if isinstance(value, str) and value in wanted and props.get('name') != wanted[value]:
                    if not is_placeholder(props.get('name')):
                        props['other_names'] = ' | '.join(filter(None, [props.get('other_names'), props.get('name')]))
                    props['name'] = wanted[value]
                    renamed += 1
                    break
        return renamed
