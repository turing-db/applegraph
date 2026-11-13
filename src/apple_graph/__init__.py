"""Read-only Apple knowledge graph snapshot and TuringDB importer."""
from __future__ import annotations

import argparse
import base64
import hashlib
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

from .biome import add_biome
from .contacts import add_contacts
from . import faces
from .mail import add_mail
from .model import APPLE_EPOCH, Builder, apple_time, local_day, snapshot, split_days, union_seconds
from .photos import add_photos
from .safari import add_safari

DEFAULT_SOURCE = Path.home() / 'Library/IntelligencePlatform'
DEFAULT_TARGET = Path(__file__).resolve().parents[2] / '.apple-graph'
DEFAULT_KNOWLEDGE = Path.home() / 'Library/Application Support/Knowledge/knowledgeC.db'
DEFAULT_PATHS = {
    'knowledge': DEFAULT_KNOWLEDGE,
    'safari': Path.home() / 'Library/Safari/History.db',
    'contacts': Path.home() / 'Library/Application Support/AddressBook/AddressBook-v22.abcddb',
    'mail': Path.home() / 'Library/Mail/V10/MailData/Envelope Index',
    'photos': Path.home() / 'Pictures/Photos Library.photoslibrary/database/Photos.sqlite',
    'biome': Path.home() / 'Library/Biome/streams/restricted',
}
ACTIVE_TABLES = ('stable_graph', 'event_graph')
TABLE_PRIORITY = {'stable_graph': 0, 'event_graph': 1}
FACT_COLUMNS = {'subject', 'predicate', 'relationshipId', 'relationshipPredicate', 'object'}
CLASS_PREDICATE = 'PS1'  # Apple's "is a" predicate.
# CoreDuet knowledge store columns used for the timeline layer; missing columns read as NULL.
KNOWLEDGE_COLUMNS = {
    'stream': 'o.ZSTREAMNAME', 'value': 'o.ZVALUESTRING', 'value_int': 'o.ZVALUEINTEGER', 'start': 'o.ZSTARTDATE',
    'end': 'o.ZENDDATE', 'offset': 'o.ZSECONDSFROMGMT', 'uuid': 'o.ZUUID', 'source_bundle': 's.ZBUNDLEID',
    'device': 's.ZDEVICEID', 'domain': 'm.Z_DKDIGITALHEALTHMETADATAKEY__WEBDOMAIN',
    'bt_name': 'm.Z_DKBLUETOOTHMETADATAKEY__NAME', 'bt_address': 'm.Z_DKBLUETOOTHMETADATAKEY__ADDRESS',
    'bt_type': 'm.Z_DKBLUETOOTHMETADATAKEY__DEVICETYPE', 'bt_product': 'm.Z_DKBLUETOOTHMETADATAKEY__PRODUCTID',
    'intent_class': 'm.Z_DKINTENTMETADATAKEY__INTENTCLASS', 'intent_verb': 'm.Z_DKINTENTMETADATAKEY__INTENTVERB',
    'direction': 'm.Z_DKINTENTMETADATAKEY__DIRECTION', 'by_siri': 'm.Z_DKINTENTMETADATAKEY__DONATEDBYSIRI',
    'notification_bundle': 'm.Z_DKNOTIFICATIONUSAGEMETADATAKEY__BUNDLEID',
}
INTERACTION_DIRECTIONS = {1: 'outgoing', 2: 'incoming'}
RESERVED_KEYS = {'apple_id', 'entity_type', 'other_names', 'relationship', 'active', 'table_name', 'database_name',
                 'row_json', 'row_sha256', 'sqlite_schema', 'row_count', 'code'}


def plain(label: str) -> list[str]:
    spaced = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', label)
    return re.sub(r'[^0-9A-Za-z]+', ' ', spaced).split()


def property_key(label: str) -> str:
    key = '_'.join(plain(label)).lower() or 'value'
    return f'fact_{key}' if key in RESERVED_KEYS else key


def edge_type(label: str) -> str:
    name = '_'.join(plain(label)).upper() or 'RELATED_TO'
    return name if name[0].isalpha() else f'P_{name}'


def node_label(label: str) -> str:
    words = plain(label)
    words = [w for w in words if w.lower() not in ('nm', 'relationship', 'type')] or words
    name = ''.join(w[:1].upper() + w[1:] for w in words) or 'Thing'
    return name if name[0].isalpha() else f'C{name}'


def text(value) -> str:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode('ascii')
    return '' if value is None else str(value)


def literal(value) -> str:
    """Render Apple's JSON calendar-date objects as readable timestamps."""
    value = text(value)
    if value.startswith('{') and '"day"' in value:
        try:
            parts = json.loads(value)
        except ValueError:
            return value
        if isinstance(parts, dict) and all(isinstance(parts.get(k), int) for k in ('year', 'month', 'day')):
            out = f"{parts['year']:04d}-{parts['month']:02d}-{parts['day']:02d}"
            if isinstance(parts.get('hour'), int):
                out += f" {parts['hour']:02d}:{parts.get('minute', 0):02d}"
            zone = (parts.get('timeZone') or {}).get('identifier')
            return f'{out} {zone}' if zone else out
    return value


def read_knowledge(db: sqlite3.Connection) -> tuple[list[dict], dict]:
    """Join CoreDuet events with their metadata and source, tolerating schema differences."""
    columns = {}
    for table in ('ZOBJECT', 'ZSTRUCTUREDMETADATA', 'ZSOURCE'):
        columns[table] = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
    alias = {'o': 'ZOBJECT', 'm': 'ZSTRUCTUREDMETADATA', 's': 'ZSOURCE'}
    selected = []
    for name, column in KNOWLEDGE_COLUMNS.items():
        prefix, field = column.split('.')
        selected.append(f'{column} AS {name}' if field in columns[alias[prefix]] else f'NULL AS {name}')
    if 'ZSTREAMNAME' not in columns['ZOBJECT']:
        return [], {}
    joins = ''
    if columns['ZSTRUCTUREDMETADATA'] and 'ZSTRUCTUREDMETADATA' in columns['ZOBJECT']:
        joins += ' LEFT JOIN ZSTRUCTUREDMETADATA m ON m.Z_PK = o.ZSTRUCTUREDMETADATA'
    if columns['ZSOURCE'] and 'ZSOURCE' in columns['ZOBJECT']:
        joins += ' LEFT JOIN ZSOURCE s ON s.Z_PK = o.ZSOURCE'
    rows = [dict(r) for r in db.execute(f"SELECT {', '.join(selected)} FROM ZOBJECT o{joins} ORDER BY o.ZSTARTDATE")]
    peers = {}
    try:
        peers = {r[0]: r[1] for r in db.execute('SELECT ZDEVICEID, ZMODEL FROM ZSYNCPEER') if r[0]}
    except sqlite3.Error:
        pass
    return rows, peers


def export_graph(source: Path, output: Path, entities_only: bool = False, knowledge: Path | None = None,
                 sources: dict[str, Path] | None = None, name_overrides: Path | None = None) -> dict:
    """Export every SQLite table plus a property-graph model of Apple's facts.

    ``sources`` maps optional extra sources (safari, contacts, mail, photos, biome) to
    their paths; missing files are skipped. ``name_overrides`` is an optional JSON file mapping an
    apple_id, Photos person UUID, handle or bundle id to the display name you want.
    """
    sources = {name: Path(path) for name, path in (sources or {}).items() if path is not None}
    output.parent.mkdir(parents=True, exist_ok=True)
    databases = sorted(source.glob('*.db'))
    if not (source / 'graph.db').is_file() or not (source / 'ontology.db').is_file():
        raise ValueError(f'No graph.db and ontology.db in {source}. This needs macOS 15 or later, and '
                         'Full Disk Access for the terminal app you run it from (System Settings > Privacy & '
                         'Security > Full Disk Access, then restart the terminal).')
    raw_tables = []
    rows = []
    with tempfile.TemporaryDirectory(prefix='apple-graph-') as tmp:
        tmp = Path(tmp)
        for path in databases:
            snapshot(path, tmp / path.name)
            with closing(sqlite3.connect(tmp / path.name)) as db:
                db.row_factory = sqlite3.Row
                tables = list(db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"))
                for table, schema in tables:
                    quoted = '"' + table.replace('"', '""') + '"'
                    contents = [dict(row) for row in db.execute(f'SELECT * FROM {quoted}')]
                    raw_tables.append((path.name, table, schema, contents))
                    columns = {r[1] for r in db.execute(f'PRAGMA table_info({quoted})')}
                    if path.name == 'graph.db' and '_fts' not in table and FACT_COLUMNS <= columns:
                        rows.extend((table, row) for row in contents)
        with closing(sqlite3.connect(tmp / 'ontology.db')) as ontology:
            predicates = dict(ontology.execute('SELECT id, label FROM predicate'))
            classes = dict(ontology.execute('SELECT id, label FROM class'))
        knowledge_rows, knowledge_peers = [], {}
        if knowledge is not None and knowledge.is_file():
            snapshot(knowledge, tmp / 'knowledgeC.db')
            with closing(sqlite3.connect(tmp / 'knowledgeC.db')) as kdb:
                kdb.row_factory = sqlite3.Row
                for table, schema in list(kdb.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")):
                    quoted = '"' + table.replace('"', '""') + '"'
                    raw_tables.append(('knowledgeC.db', table, schema, [dict(row) for row in kdb.execute(f'SELECT * FROM {quoted}')]))
                knowledge_rows, knowledge_peers = read_knowledge(kdb)

    builder = Builder()
    nodes, node_records, edge_records = builder.nodes, builder.node_records, builder.edge_records
    node, edge = builder.node, builder.edge

    def plabel(code) -> str:
        return predicates.get(code) or text(code)
    def clabel(code) -> str:
        return classes.get(code) or text(code)
    def fact_props(table: str, row: dict) -> dict:
        props = dict(table_name=table, active=table in ACTIVE_TABLES, predicate=text(row['predicate']),
                     raw_object=text(row['object']))
        if row['relationshipPredicate']:
            props['relationship_predicate'] = text(row['relationshipPredicate'])
        if isinstance(row.get('confidence'), (int, float)) and math.isfinite(row['confidence']):
            props['confidence'] = float(row['confidence'])
        if isinstance(row.get('sources'), int):
            props['sources'] = row['sources']
        if row.get('sourceDuplicates'):
            props['source_duplicates'] = text(row['sourceDuplicates'])
        stamp = apple_time(row.get('timestamp'))
        if stamp:
            props['timestamp_utc'] = stamp
        return props

    # Current facts come first, so their values win over expired duplicates.
    rows.sort(key=lambda item: TABLE_PRIORITY.get(item[0], len(TABLE_PRIORITY)))
    subjects = {str(row['subject']) for _, row in rows}
    top = defaultdict(list)
    groups = defaultdict(list)
    for table, row in rows:
        subject = str(row['subject'])
        if row['relationshipId']:
            groups[(subject, row['predicate'], str(row['relationshipId']))].append((table, row))
        else:
            top[subject].append((table, row))

    # Apple writes references as "md:<subject>" or "<identifier type>:<identifier id>", the
    # latter declared by an entity's identifier group. Most "same as" values name the
    # entity's own source record, so self-references stay properties rather than edges.
    identifiers: dict[tuple[str, str], str] = {}
    for (subject, code, _), facts in groups.items():
        if plabel(code) != 'identifier':
            continue
        values = {plabel(row['relationshipPredicate']): text(row['object']) for _, row in reversed(facts)}
        if values.get('identifier type') and values.get('identifier id'):
            identifiers.setdefault((values['identifier type'].lower(), values['identifier id']), subject)
    unresolved = Counter()
    def resolve(value) -> str | None:
        if not isinstance(value, str):
            return None
        if value in subjects:
            return value
        if value.startswith('md:'):
            if value[3:] in subjects:
                return value[3:]
            unresolved['md'] += 1
            return None
        prefix, sep, rest = value.partition(':')
        if sep and (prefix.lower(), rest) in identifiers:
            return identifiers[prefix.lower(), rest]
        if sep and any(kind == prefix.lower() for kind, _ in identifiers):
            unresolved[prefix] += 1
        return None

    def add_value(store: dict, label: str, value: str) -> None:
        values = store[property_key(label)]
        if value not in values:
            values.append(value)
    entity = {s: dict(props=defaultdict(list), types=[]) for s in subjects}
    pending_edges = []
    for subject, facts in top.items():
        for table, row in facts:
            code = row['relationshipPredicate'] or row['predicate']
            if code == CLASS_PREDICATE:
                if table in ACTIVE_TABLES and clabel(row['object']) not in entity[subject]['types']:
                    entity[subject]['types'].append(clabel(row['object']))
                continue
            target = resolve(row['object'])
            if target is not None and target != subject:
                pending_edges.append((('entity', subject), ('entity', target), edge_type(plabel(code)), fact_props(table, row)))
            elif table in ACTIVE_TABLES and text(row['object']).strip():
                add_value(entity[subject]['props'], plabel(code), literal(row['object']))
    group_edges = []
    flattened = 0
    for key, facts in groups.items():
        subject, code, rel_id = key
        kinds, literals, refs = [], [], []
        for table, row in facts:
            rcode = row['relationshipPredicate'] or code
            if rcode == CLASS_PREDICATE:
                if clabel(row['object']) not in kinds:
                    kinds.append(clabel(row['object']))
                continue
            target = resolve(row['object'])
            if target is not None and target != subject:
                refs.append((table, row, plabel(rcode), target))
            else:
                literals.append((table, row, plabel(rcode)))
        active = any(table in ACTIVE_TABLES for table, _ in facts)
        if refs:
            # Relationships that point at another entity become one edge per reference; the
            # group's attribute facts ride along as edge properties.
            props = defaultdict(list)
            for table, row, rlabel in literals:
                if text(row['object']).strip():
                    add_value(props, rlabel, literal(row['object']))
            label = re.sub(r'\s+relationship$', '', plabel(code))
            for table, row, rlabel, target in refs:
                edge_props = {key_: ' | '.join(values) for key_, values in props.items()}
                edge_props.update(fact_props(table, row))
                edge_props.update(via=rlabel, relationship_id=rel_id, relationship_active=active)
                if kinds:
                    edge_props['relationship_type'] = ' | '.join(kinds)
                group_edges.append((('entity', subject), ('entity', target), edge_type(label), edge_props))
        elif active:
            # Attribute-only groups (aliases, identifiers, contact details) flatten onto the entity.
            flattened += 1
            for table, row, rlabel in literals:
                if table in ACTIVE_TABLES and text(row['object']).strip():
                    add_value(entity[subject]['props'], rlabel, literal(row['object']))

    def joined(props: dict) -> dict:
        return {key: ' | '.join(values) for key, values in props.items()}
    def display_name(subject: str) -> str:
        info = entity[subject]
        props = info['props']
        if props.get('name'):
            return props['name'][0]
        personal = ' '.join(props[key][0] for key in ('first_name', 'family_name') if props.get(key))
        if personal:
            return personal
        for key in ('bundle_id', 'application_identifier', 'also_known_as', 'address_string', 'full_street_address',
                    'city', 'url', 'description'):
            if props.get(key):
                return props[key][0]
        return f"{info['types'][0] if info['types'] else 'Entity'} {subject}"
    names = {}
    for subject in sorted(subjects):
        info = entity[subject]
        props = joined(info['props'])
        names[subject] = props['name'] = display_name(subject)
        if len(info['props'].get('name', [])) > 1:
            props['other_names'] = ' | '.join(info['props']['name'][1:])
        props['apple_id'] = subject
        if info['types']:
            props['entity_type'] = ' | '.join(info['types'])
        node(('entity', subject), ['AppleEntity', *sorted({node_label(t) for t in info['types']})], **props)
    if not entities_only:
        for code, name in predicates.items():
            node(('predicate', code), ['ApplePredicate'], code=code, name=name or code)
        for code, name in classes.items():
            node(('class', code), ['AppleClass'], code=code, name=name or code)
    for start, end, label, props in (*pending_edges, *group_edges):
        edge(nodes[start], nodes[end], label, props)
    # Let the other sources find these entities by bundle id, Apple identifier, phone or email.
    for subject, info in entity.items():
        key = ('entity', subject)
        for bundle in info['props'].get('bundle_id', []):
            builder.register_bundle(bundle, key)
        for prop in ('phone_number', 'email_address'):
            for value in info['props'].get(prop, []):
                builder.register_handle(value, key)
        if info['props'].get('is_current_user', [''])[0] == '1':
            builder.set_me(key)
    for (kind, identifier), subject in identifiers.items():
        builder.identifier_index.setdefault((kind, identifier), ('entity', subject))
        if kind == 'phperson' and '/' in identifier:
            builder.identifier_index.setdefault((kind, identifier.split('/')[0]), ('entity', subject))

    # Timeline layer from the CoreDuet knowledge store: per-day aggregates hang off Day nodes.
    knowledge_stats = {}
    bundle_index = builder.bundle_index
    if knowledge_rows:
        day_node = builder.day
        app_node = builder.app
        def device_name(device_id) -> str:
            if not device_id:
                return 'This Mac'
            return knowledge_peers.get(device_id) or f'Device {device_id[:8]}'
        aggregate = defaultdict(list)  # (kind, item, day) -> [(start, end)] intervals
        details = {}
        screen_on = Counter()
        streams = Counter()
        interactions = 0
        for row in knowledge_rows:
            date = local_day(row['start'], row['offset'])
            if date is None or not isinstance(row['start'], (int, float)):
                continue
            streams[row['stream']] += 1
            end = row['end'] if isinstance(row['end'], (int, float)) else row['start']
            try:
                pieces = split_days(float(row['start']), max(float(end), float(row['start'])), row['offset'])
            except (OverflowError, ValueError):
                continue
            stream, value = row['stream'], row['value']
            item = None
            if stream == '/app/usage' and value:
                item = ('app', value)
            elif stream == '/app/webUsage' and row['domain']:
                item = ('web', row['domain'])
                details.setdefault(('browser', row['domain']), value)
            elif stream == '/app/mediaUsage' and value:
                item = ('media', value)
            elif stream == '/bluetooth/isConnected' and (row['bt_address'] or row['bt_name']):
                item = ('bluetooth', row['bt_address'] or row['bt_name'])
                details.setdefault(('bluetooth', item[1]), row)
            elif stream == '/notification/usage' and row['notification_bundle']:
                item = ('notification', row['notification_bundle'])
            elif stream == '/display/isBacklit':
                if row['value_int'] == 1:
                    for day, piece_start, piece_end in pieces:
                        screen_on[day] += piece_end - piece_start
            elif stream == '/app/intents':
                verb = row['intent_verb'] or row['intent_class'] or 'Interaction'
                local = APPLE_EPOCH + timedelta(seconds=float(row['start']) + (row['offset'] or 0))
                props = dict(verb=verb, intent_class=row['intent_class'] or '', app=value or '',
                             direction=INTERACTION_DIRECTIONS.get(row['direction'], 'unspecified'),
                             donated_by_siri=bool(row['by_siri']), timestamp_utc=apple_time(row['start']) or '',
                             local_time=local.strftime('%H:%M'), device=device_name(row['device']), uuid=row['uuid'] or '')
                edge(app_node(row['source_bundle'] or value or 'unknown'), day_node(date), edge_type(verb), props)
                interactions += 1
            if item is not None:
                for day, piece_start, piece_end in pieces:
                    aggregate[(*item, day)].append((piece_start, piece_end))
        edge_names = {'app': 'USED_ON', 'web': 'BROWSED_ON', 'media': 'PLAYED_MEDIA_ON', 'bluetooth': 'CONNECTED_ON',
                      'notification': 'NOTIFIED_ON'}
        totals = defaultdict(lambda: dict(seconds=0.0, sessions=0, days=0, first=None, last=None))
        buckets = {key: dict(seconds=union_seconds(intervals), sessions=len(intervals),
                             first=min(start for start, _ in intervals), last=max(end for _, end in intervals))
                   for key, intervals in aggregate.items()}
        day_totals = Counter()
        for (kind, item, date), bucket in buckets.items():
            day_totals[kind, date] += bucket['seconds']
        for (kind, item, date), bucket in sorted(buckets.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
            if kind in ('app', 'media', 'notification'):
                start = app_node(item)
            elif kind == 'web':
                start = builder.web_domain(item)
            else:
                info = details['bluetooth', item]
                start = node(('bluetooth', item), ['BluetoothDevice'], name=info['bt_name'] or item,
                             address=info['bt_address'] or '', device_type=int(info['bt_type'] or 0),
                             product_id=int(info['bt_product'] or 0))
            props = dict(first_start=apple_time(bucket['first']) or '', last_end=apple_time(bucket['last']) or '')
            if kind == 'notification':
                props['notifications'] = bucket['sessions']
            else:
                props.update(seconds=int(round(bucket['seconds'])), minutes=round(bucket['seconds'] / 60, 1),
                             sessions=bucket['sessions'],
                             share_of_day=round(bucket['seconds'] / day_totals[kind, date], 4) if day_totals[kind, date] else 0.0)
            if kind == 'app':
                props.update(device='This Mac', source='knowledgeC')
            else:
                props['source'] = 'knowledgeC'
            edge(start, day_node(date), edge_names[kind], props)
            total = totals[kind, item]
            total['seconds'] += bucket['seconds']
            total['sessions'] += bucket['sessions']
            total['days'] += 1
            total['first'] = min(total['first'] or bucket['first'], bucket['first'])
            total['last'] = max(total['last'] or bucket['last'], bucket['last'])
        for (kind, item), total in totals.items():
            if kind == 'app':
                props = node_records[app_node(item)]['properties']
                props['usage_seconds'] = int(round(total['seconds']))
                props['usage_sessions'] = total['sessions']
            elif kind == 'web':
                props = node_records[nodes['web', item]]['properties']
                props['browse_seconds'] = int(round(total['seconds']))
                props['visits'] = total['sessions']
                browser = details.get(('browser', item))
                if browser:
                    edge(app_node(browser), nodes['web', item], 'BROWSED',
                         dict(seconds=int(round(total['seconds'])), minutes=round(total['seconds'] / 60, 1),
                              visits=total['sessions'], days=total['days'],
                              first_seen=apple_time(total['first']) or '', last_seen=apple_time(total['last']) or '',
                              source='knowledgeC'))
        for date, seconds in screen_on.items():
            node_records[day_node(date)]['properties']['screen_on_seconds'] = int(round(seconds))
        # Calendar events from the Apple graph join the same timeline.
        scheduled = 0
        for subject, info in entity.items():
            if 'calendar event' not in info['types']:
                continue
            starts = list(dict.fromkeys(info['props'].get('start_time', [])))
            ends = list(dict.fromkeys(info['props'].get('end_time', [])))
            for index, value in enumerate(starts):
                if re.match(r'\d{4}-\d{2}-\d{2}', value):
                    props = dict(start_time=value, end_time=ends[index] if index < len(ends) else '',
                                 all_day=info['props'].get('is_all_day', ['0'])[0] == '1',
                                 occurrence=index + 1, occurrences=len(starts))
                    edge(nodes['entity', subject], day_node(value[:10]), 'SCHEDULED_ON', props)
                    scheduled += 1
        knowledge_stats = dict(events=sum(streams.values()), streams=dict(sorted(streams.items())),
                               days=sum(1 for key in nodes if key[0] == 'day'),
                               apps_matched=sum(1 for b in {i for k, i in totals if k == 'app'} if bundle_index.get(b, ('',))[0] == 'entity'),
                               apps_added=sum(1 for key in nodes if key[0] == 'app'),
                               web_domains=sum(1 for key in nodes if key[0] == 'web'),
                               bluetooth_devices=sum(1 for key in nodes if key[0] == 'bluetooth'),
                               interactions=interactions,
                               scheduled_events=scheduled)
    # Further personal data stores, each aggregated per day onto the same people, apps and places.
    source_stats = {}
    if 'contacts' in sources and sources['contacts'].is_file():
        source_stats['contacts'] = add_contacts(builder, sources['contacts'])
    if 'safari' in sources and sources['safari'].is_file():
        source_stats['safari'] = add_safari(builder, sources['safari'])
    if 'mail' in sources and sources['mail'].is_file():
        my_addresses = set()
        if builder.me_key is not None:
            for prop in ('email_address', 'emails'):
                for value in str(builder.props(builder.me_key).get(prop, '')).split('|'):
                    address = value.split(':', 1)[-1].strip()
                    if '@' in address:
                        my_addresses.add(address.lower())
        source_stats['mail'] = add_mail(builder, sources['mail'], my_addresses)
    if 'photos' in sources and sources['photos'].is_file():
        source_stats['photos'] = add_photos(builder, sources['photos'])
    if 'biome' in sources and sources['biome'].is_dir():
        source_stats['biome'] = add_biome(builder, sources['biome'], knowledge_peers)
    renamed = 0
    if name_overrides is not None and name_overrides.is_file():
        renamed = builder.apply_name_overrides(json.loads(name_overrides.read_text()))
    semantic_edges = Counter(record['label'] for record in edge_records)
    # All original SQLite rows are also retained in a typed, lossless archive.
    # Namespaced JSON avoids TuringDB's single global type per property name.
    def typed(value):
        if value is None:
            return {'type': 'null', 'value': None}
        if isinstance(value, bytes):
            return {'type': 'blob', 'value': base64.b64encode(value).decode('ascii')}
        if isinstance(value, int):
            return {'type': 'integer', 'value': str(value)}
        if isinstance(value, float):
            return {'type': 'real', 'value': value.hex()}
        return {'type': 'text', 'value': value}

    table_counts = {}
    for database, table, schema, contents in ([] if entities_only else raw_tables):
        database_id = node(('database', database), ['AppleDatabase'], name=database)
        table_id = node(('table', database, table), ['AppleTable'], name=f'{database}/{table}',
                        database_name=database, table_name=table, sqlite_schema=schema or '', row_count=len(contents))
        edge(database_id, table_id, 'HAS_TABLE')
        table_counts[f'{database}/{table}'] = len(contents)
        for index, row in enumerate(contents):
            raw = json.dumps({key: typed(value) for key, value in row.items()}, ensure_ascii=False, sort_keys=True)
            record = node(('row', database, table, index), ['AppleSQLiteRow'],
                          name=f'{database}/{table} row {index + 1}', database_name=database,
                          table_name=table, row_json=raw, row_sha256=hashlib.sha256(raw.encode()).hexdigest())
            edge(table_id, record, 'HAS_ROW')
            if database == 'ontology.db':
                if table == 'predicate_domain':
                    start, end = nodes.get(('predicate', row['predicateId'])), nodes.get(('class', row['domain']))
                    if start is not None and end is not None:
                        edge(start, end, 'DOMAIN')
                elif table == 'predicate_range' and row['dataType'] == 'class':
                    start, end = nodes.get(('predicate', row['predicateId'])), nodes.get(('class', row['range']))
                    if start is not None and end is not None:
                        edge(start, end, 'RANGE')
                elif table == 'inheritance':
                    start, end = nodes.get(('class', row['child'])), nodes.get(('class', row['parent']))
                    if start is not None and end is not None:
                        edge(start, end, 'SUBCLASS_OF')
    with output.open('w') as stream:
        for record in (*node_records, *edge_records):
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
    output.chmod(0o600)
    labels = Counter(label for record in node_records for label in record['labels'])
    stats = dict(rows=dict(Counter(table for table, _ in rows)), entities=len(subjects),
                 entity_labels={k: v for k, v in sorted(labels.items()) if k not in ('AppleSQLiteRow', 'AppleTable', 'AppleDatabase')},
                 relationship_edges=len(group_edges), flattened_groups=flattened,
                 semantic_edges=dict(sorted(semantic_edges.items())), unresolved_references=dict(unresolved),
                 knowledge=knowledge_stats, sources=source_stats, days=sum(1 for key in nodes if key[0] == 'day'),
                 renamed=renamed,
                 tables=table_counts, nodes=len(nodes), edges=len(edge_records),
                 sqlite_rows=sum(table_counts.values()), databases=len(databases))
    output.with_suffix('.manifest.json').write_text(json.dumps(stats, indent=2) + '\n')
    return stats


def open_db(target: Path, port: int | None = None):
    from turingdb import TuringDB
    if port is not None:
        return TuringDB(type="native", host="127.0.0.1", port=port)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    return TuringDB(type="embedded", data_dir=str(target.resolve()))


DEFAULT_PORT = 16666  # The server port the README starts.


@contextmanager
def quiet_stdout():
    """Silence the embedded engine, which logs to the process's stdout, so result tables stay clean."""
    sys.stdout.flush()
    saved, null = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    try:
        yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)
        os.close(null)


def open_for_queries(target: Path, port: int | None):
    """The --port server if given; else an embedded engine, or the server on the default port if one holds the folder."""
    if port is not None:
        return open_db(target, port)
    try:
        with quiet_stdout():
            return open_db(target)
    except Exception as exc:
        if 'Already locked' not in str(exc):
            raise
    print(f'A TuringDB server holds {target}; querying it on port {DEFAULT_PORT} (use --port for another).', file=sys.stderr)
    return open_db(target, DEFAULT_PORT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_TARGET)
    parser.add_argument('--graph', default='apple_life')
    parser.add_argument('--port', type=int, help='Use an existing local TuringDB server instead of embedded mode')
    commands = parser.add_subparsers(dest='command', required=True)
    imp = commands.add_parser('import', help='Snapshot Apple SQLite and import a new TuringDB graph')
    imp.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    imp.add_argument('--export-only', action='store_true')
    imp.add_argument('--entities-only', action='store_true',
                     help='Skip the SQLite row archive and ontology; keep only entities and their edges')
    for name, default in DEFAULT_PATHS.items():
        imp.add_argument(f'--{name}', type=Path, default=default, metavar='PATH',
                         help=f'Path of the {name} source (skipped if missing)')
    imp.add_argument('--skip', action='append', default=[], choices=sorted(DEFAULT_PATHS), metavar='SOURCE',
                     help='Leave out one of: ' + ', '.join(sorted(DEFAULT_PATHS)) + ' (repeatable)')
    imp.add_argument('--no-knowledge', action='store_true', help=argparse.SUPPRESS)
    imp.add_argument('--names', type=Path, metavar='JSON',
                     help='Display-name overrides keyed by apple_id, Photos person UUID, handle or bundle id (default: <data-dir>/names.json)')
    commands.add_parser('verify', help='Compare persisted graph counts against the JSONL export')
    query = commands.add_parser('query', help='Run Cypher on the imported graph')
    query.add_argument('cypher')
    catalogue = commands.add_parser('queries', help='List catalogue queries, or run one and print its results')
    catalogue.add_argument('name', nargs='?', help='Print this query as one pasteable line, then run it and show the results')
    catalogue.add_argument('--rows', type=int, default=50, metavar='N', help='Show at most N result rows; 0 shows all (default: 50)')
    catalogue.add_argument('--no-run', action='store_true', help='Only print the pasteable line, without running the query')
    catalogue.add_argument('--category', choices=('insight', 'inference', 'visual'))
    catalogue.add_argument('--param', action='append', default=[], metavar='KEY=VALUE',
                           help='Fill a $parameter (repeatable)')
    face = commands.add_parser('faces', help='List the photos behind a face cluster and optionally reveal them in Photos')
    face.add_argument('identifier', help='apple_id (as shown on person nodes) or Photos person UUID')
    face.add_argument('--library', type=Path, default=faces.DEFAULT_LIBRARY)
    face.add_argument('--source', type=Path, default=DEFAULT_SOURCE, help='IntelligencePlatform directory used to map apple_id to a Photos person')
    face.add_argument('--limit', type=int, default=12)
    face.add_argument('--reveal', type=int, nargs='?', const=1, default=0, metavar='N', help='Select the first N photos in the Photos app')
    face.add_argument('--open', type=int, nargs='?', const=4, default=0, metavar='N', help='Open up to N locally stored originals')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.graph):
        parser.error('--graph must be a simple identifier')
    target = args.data_dir.expanduser().resolve()
    try:
        if args.command == 'queries':
            from . import queries
            if args.name is None:
                queries.show(args.category)
                return
            if args.name not in queries.QUERIES:
                parser.error(f'unknown query {args.name!r}; run "apple-graph queries" for the list')
            values = {}
            for item in args.param:
                key, sep, value = item.partition('=')
                if not sep:
                    parser.error(f'--param expects KEY=VALUE, got {item!r}')
                values[key] = queries.parameter_value(key, value)
            try:
                print(queries.one_line(args.name, **values))
            except KeyError as error:
                parser.error(error.args[0])
            if args.no_run:
                return
            db = open_for_queries(target, args.port)
            with quiet_stdout():
                db.load_graph(args.graph, raise_if_loaded=False)
                db.set_graph(args.graph)
                found = queries.run(db, args.name, **values)
                shown = found.head(args.rows) if args.rows > 0 else found
                result = queries.table(queries.readable(db, queries.cypher(args.name), shown), total=len(found))
            print(f'\n{queries.QUERIES[args.name][0]} · graph {args.graph}')
            print(result)
            if len(shown) < len(found):
                print('Pass --rows 0 to see them all.')
            return
        if args.command == 'faces':
            faces.main(args)
            return
        if args.command == 'import':
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            db = None if args.export_only else open_db(target, args.port)
            if db is not None:
                # Check disk too: a persisted graph need not already be loaded.
                if args.graph in db.list_available_graphs() or args.graph in db.list_loaded_graphs():
                    raise ValueError('Graph already exists; use a new --graph name for a fresh snapshot.')
            output = target / 'data' / f'{args.graph}.jsonl'
            skipped = set(args.skip) | ({'knowledge'} if args.no_knowledge else set())
            paths = {name: getattr(args, name).expanduser() for name in DEFAULT_PATHS if name not in skipped}
            stats = export_graph(args.source.expanduser(), output, entities_only=args.entities_only,
                                 knowledge=paths.pop('knowledge', None), sources=paths,
                                 name_overrides=(args.names.expanduser() if args.names else target / 'names.json'))
            if db is not None:
                db.query(f"LOAD JSONL '{output.name}' AS {args.graph}")
                db.set_graph(args.graph)
                actual_nodes = int(db.query('MATCH (n) RETURN count(n) AS total').iloc[0, 0])
                actual_edges = int(db.query('MATCH ()-[r]->() RETURN count(r) AS total').iloc[0, 0])
                if (actual_nodes, actual_edges) != (stats['nodes'], stats['edges']):
                    raise RuntimeError(f'Import count mismatch: {actual_nodes} nodes, {actual_edges} edges')
                stats['verified'] = True
            print(json.dumps(dict(graph=args.graph, data_dir=str(target), **stats), indent=2))
        else:
            db = open_db(target, args.port)
            db.load_graph(args.graph, raise_if_loaded=False)
            db.set_graph(args.graph)
            if args.command == 'verify':
                with (target / 'data' / f'{args.graph}.jsonl').open() as stream:
                    expected = Counter(json.loads(line)['type'] for line in stream)
                actual = {
                    'node': int(db.query('MATCH (n) RETURN count(n) AS total').iloc[0, 0]),
                    'relationship': int(db.query('MATCH ()-[r]->() RETURN count(r) AS total').iloc[0, 0]),
                }
                if actual != dict(expected):
                    raise RuntimeError(f'Count mismatch: expected {dict(expected)}, got {actual}')
                expected_rows = Counter()
                with (target / 'data' / f'{args.graph}.jsonl').open() as stream:
                    for line in stream:
                        record = json.loads(line)
                        if record.get('labels') == ['AppleSQLiteRow']:
                            prop = record['properties']
                            expected_rows[(prop['database_name'], prop['table_name'], prop['row_json'])] += 1
                if expected_rows:
                    stored = db.query('MATCH (n:AppleSQLiteRow) RETURN n.database_name, n.table_name, n.row_json')
                    if Counter(stored.itertuples(index=False, name=None)) != expected_rows:
                        raise RuntimeError('Stored SQLite row contents differ from the export')
                    names = db.query('MATCH (n) RETURN n.name')
                    if not all(isinstance(value, str) and value for value in names.iloc[:, 0]):
                        raise RuntimeError('Some nodes lack display names')
                print(json.dumps(dict(verified=True, sqlite_rows_verified=sum(expected_rows.values()), **actual), indent=2))
            else:
                print(db.query(args.cypher).to_string(index=False))
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        parser.exit(1, f'Error: {exc}\n')
    except Exception as exc:
        if type(exc).__name__ != 'TuringDBException':
            raise
        parser.exit(1, f'Error: {exc}\n')
