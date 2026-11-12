"""Safari browsing history as per-day domain visits."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlsplit

from .model import Builder, apple_time, snapshot, system_day


def add_safari(builder: Builder, path: Path, min_visits: int = 2) -> dict:
    with tempfile.TemporaryDirectory(prefix='apple-graph-safari-') as tmp:
        copy = Path(tmp) / 'History.db'
        snapshot(path, copy)
        with closing(sqlite3.connect(copy)) as db:
            rows = db.execute('SELECT i.url, v.visit_time FROM history_visits v JOIN history_items i ON i.id = v.history_item').fetchall()
    per_day = defaultdict(lambda: dict(visits=0, pages=set(), first=None, last=None))
    totals = defaultdict(lambda: dict(visits=0, pages=set(), first=None, last=None))
    for url, visit_time in rows:
        host = (urlsplit(url).hostname or '').lower()
        day = system_day(visit_time)
        if not host or day is None:
            continue
        for bucket in (per_day[host, day], totals[host]):
            bucket['visits'] += 1
            bucket['pages'].add(url)
            bucket['first'] = visit_time if bucket['first'] is None else min(bucket['first'], visit_time)
            bucket['last'] = visit_time if bucket['last'] is None else max(bucket['last'], visit_time)
    kept = {host for host, total in totals.items() if total['visits'] >= min_visits}
    for host in sorted(kept):
        total = totals[host]
        props = builder.props(builder.web_domain(host))
        props.update(safari_visits=total['visits'], safari_pages=len(total['pages']),
                     safari_first_visit=apple_time(total['first']) or '', safari_last_visit=apple_time(total['last']) or '')
    edges = 0
    for (host, day), bucket in sorted(per_day.items(), key=lambda item: (item[0][1], item[0][0])):
        if host not in kept:
            continue
        builder.edge(builder.web_domain(host), builder.day(day), 'VISITED_ON',
                     dict(visits=bucket['visits'], pages=len(bucket['pages']), first_visit=apple_time(bucket['first']) or '',
                          last_visit=apple_time(bucket['last']) or '', source='Safari'))
        edges += 1
    return dict(visits=len(rows), domains=len(kept), skipped_domains=len(totals) - len(kept), visited_on_edges=edges,
                first_visit=apple_time(min((r[1] for r in rows), default=None)) or '', last_visit=apple_time(max((r[1] for r in rows), default=None)) or '')
