"""Biome streams: messages, calls, media playback and iPhone app focus.

Field numbers below were mapped empirically from this machine's streams; Apple
publishes no schema, so each mapping is documented next to its use.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from .model import Builder, UNIX_TO_APPLE, apple_time, split_days, system_day, union_seconds
from .segb import first, stream_events, values

DIRECTIONS = {1: 'incoming', 2: 'outgoing'}  # Siri Remembers interaction field 1.6, per the sender/recipient split.


def _participants(fields: list) -> dict[str, list[tuple[str, str]]]:
    """Role -> [(handle, display name)] from the repeated participant groups (field 2)."""
    roles = defaultdict(list)
    for group in values(fields, 2, 'msg'):
        role = first(group, 1, 'str') or ''
        for entry in values(group, 2, 'msg'):
            handle = first(entry, 1, 'str') or ''
            name = ''
            blob = first(entry, 4, 'str')
            if blob:
                try:
                    name = str(json.loads(blob).get('name') or '')
                except (ValueError, AttributeError):
                    name = ''
            roles[role].append((handle, name))
    return roles


def _interaction(fields: list) -> dict | None:
    info = first(fields, 1, 'msg')
    if not info:
        return None
    started = first(info, 8, 'fixed64')
    if not isinstance(started, float) or started <= 0:
        return None
    group = False
    blob = first(info, 11, 'str')
    if blob:
        try:
            group = bool(json.loads(blob).get('isGroup'))
        except (ValueError, AttributeError):
            group = False
    return dict(app=first(info, 4, 'str') or '', apple_seconds=started - UNIX_TO_APPLE, duration=first(info, 9, 'fixed64') or 0.0,
                direction_code=first(info, 6, 'varint'), group=group, participants=_participants(fields))


def _counterparts(builder: Builder, event: dict, roles: tuple[str, ...], source: str) -> list[int]:
    """Nodes for the people (or the group chat) on the other side of an interaction."""
    people = []
    for role in roles:
        for handle, name in event['participants'].get(role, []):
            if handle or name:
                people.append(builder.person(handle, name=name, source=source))
    if event['group']:
        names = [name for name, _ in event['participants'].get('speakableGroupName', [])]
        label = next((n for n in names if n), '') or f"Group chat ({len(people)} members)"
        chat = builder.node(('chat', event['app'], label), ['GroupChat'], name=label, app=event['app'], source=source)
        for member in dict.fromkeys(people):
            builder.edge(member, chat, 'MEMBER_OF_CHAT', dict(app=event['app'], source=source))
        return [chat]
    return list(dict.fromkeys(people))


def add_messages(builder: Builder, stream: Path) -> dict:
    per_day = defaultdict(lambda: dict(messages=0, sent=0, received=0))
    summary = defaultdict(lambda: dict(messages=0, sent=0, received=0, first=None, last=None, apps=set()))
    counted = 0
    for _, _, fields in stream_events(stream):
        event = _interaction(fields)
        if event is None:
            continue
        incoming = bool(event['participants'].get('senderHandle') or event['participants'].get('sender'))
        roles = ('senderHandle', 'sender') if incoming else ('recipientHandles', 'recipients')
        day = system_day(event['apple_seconds'])
        if day is None:
            continue
        targets = _counterparts(builder, event, roles, 'Messages')
        if not targets:
            continue
        counted += 1
        for target in targets:
            bucket = per_day[target, day, event['app']]
            bucket['messages'] += 1
            bucket['received' if incoming else 'sent'] += 1
            total = summary[target]
            total['messages'] += 1
            total['received' if incoming else 'sent'] += 1
            total['first'] = event['apple_seconds'] if total['first'] is None else min(total['first'], event['apple_seconds'])
            total['last'] = event['apple_seconds'] if total['last'] is None else max(total['last'], event['apple_seconds'])
            total['apps'].add(event['app'])
    for (target, day, app), bucket in sorted(per_day.items(), key=lambda item: (item[0][1], item[0][0])):
        builder.edge(target, builder.day(day), 'MESSAGED_ON', dict(**bucket, app=app, source='Messages'))
    for target, total in summary.items():
        builder.edge(builder.me, target, 'MESSAGES_WITH',
                     dict(messages=total['messages'], sent=total['sent'], received=total['received'],
                          first=apple_time(total['first']) or '', last=apple_time(total['last']) or '',
                          apps=' | '.join(sorted(total['apps'])), source='Messages'))
    return dict(messages=counted, counterparts=len(summary), messaged_on_edges=len(per_day))


def add_calls(builder: Builder, stream: Path) -> dict:
    per_day = defaultdict(lambda: dict(calls=0, incoming=0, outgoing=0, talk_seconds=0.0))
    summary = defaultdict(lambda: dict(calls=0, incoming=0, outgoing=0, talk_seconds=0.0, first=None, last=None, apps=set()))
    counted = 0
    for _, _, fields in stream_events(stream):
        event = _interaction(fields)
        if event is None:
            continue
        day = system_day(event['apple_seconds'])
        if day is None:
            continue
        targets = _counterparts(builder, event, ('contactHandles', 'contacts'), 'Calls')
        if not targets:
            continue
        counted += 1
        direction = DIRECTIONS.get(event['direction_code'], 'unknown')
        for target in targets:
            for bucket in (per_day[target, day, event['app']], summary[target]):
                bucket['calls'] += 1
                if direction in ('incoming', 'outgoing'):
                    bucket[direction] += 1
                bucket['talk_seconds'] += event['duration']
            total = summary[target]
            total['first'] = event['apple_seconds'] if total['first'] is None else min(total['first'], event['apple_seconds'])
            total['last'] = event['apple_seconds'] if total['last'] is None else max(total['last'], event['apple_seconds'])
            total['apps'].add(event['app'])
    for (target, day, app), bucket in sorted(per_day.items(), key=lambda item: (item[0][1], item[0][0])):
        builder.edge(target, builder.day(day), 'CALLED_ON',
                     dict(calls=bucket['calls'], incoming=bucket['incoming'], outgoing=bucket['outgoing'],
                          talk_seconds=int(round(bucket['talk_seconds'])), app=app, source='Calls'))
    for target, total in summary.items():
        builder.edge(builder.me, target, 'CALLS_WITH',
                     dict(calls=total['calls'], incoming=total['incoming'], outgoing=total['outgoing'],
                          talk_seconds=int(round(total['talk_seconds'])), first=apple_time(total['first']) or '',
                          last=apple_time(total['last']) or '', apps=' | '.join(sorted(total['apps'])), source='Calls'))
    return dict(calls=counted, counterparts=len(summary), called_on_edges=len(per_day))


def add_now_playing(builder: Builder, stream: Path, device_names: dict[str, str]) -> dict:
    # Event records carry the player bundle in field 15, the timestamp in field 2 (Apple epoch),
    # title in 8, artist in 5, album in 4 and duration seconds in 6.
    plays = defaultdict(set)
    items = {}
    for origin, device, fields in stream_events(stream):
        app = first(fields, 15, 'str')
        stamp = first(fields, 2, 'fixed64')
        title = (first(fields, 8, 'str') or '').strip()
        if not app or not isinstance(stamp, float) or not title:
            continue
        artist = (first(fields, 5, 'str') or '').strip()
        album = (first(fields, 4, 'str') or '').strip()
        day = system_day(stamp)
        if day is None:
            continue
        key = ('media', title.lower(), artist.lower())
        if key not in items:
            duration = first(fields, 6, 'varint')
            node = builder.node(key, ['MediaItem'], name=title, title=title, artist=artist, album=album,
                                duration_seconds=int(duration) if isinstance(duration, int) and duration < 10**7 else 0, source='NowPlaying')
            items[key] = node
        device_name = 'This Mac' if origin == 'local' else device_names.get(device, 'synced device')
        plays[items[key], day, app, device_name].add(int(stamp // 300))  # Distinct five-minute buckets count as plays.
    per_app = defaultdict(lambda: dict(plays=0, items=set()))
    for (item, day, app, device_name), buckets in sorted(plays.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        builder.edge(item, builder.day(day), 'PLAYED_ON', dict(plays=len(buckets), app=app, device=device_name, source='NowPlaying'))
        per_app[app, item]['plays'] += len(buckets)
    for (app, item), total in per_app.items():
        builder.edge(builder.app(app, source='Biome'), item, 'PLAYED', dict(plays=total['plays'], source='NowPlaying'))
    return dict(media_items=len(items), played_on_edges=len(plays), played_edges=len(per_app))


def add_app_focus(builder: Builder, stream: Path, device_names: dict[str, str]) -> dict:
    # Remote (phone/tablet) focus events: bundle in field 6, timestamp in field 4, field 3 is 1 for
    # gaining focus and 0 for losing it. Local records duplicate knowledgeC and are skipped.
    events = defaultdict(list)
    for origin, device, fields in stream_events(stream, origin='remote'):
        bundle = first(fields, 6, 'str')
        stamp = first(fields, 4, 'fixed64')
        starting = first(fields, 3, 'varint')
        if not bundle or not isinstance(stamp, float) or starting not in (0, 1):
            continue
        events[device, bundle].append((stamp, starting))
    intervals = defaultdict(list)
    for (device, bundle), items in events.items():
        open_at = None
        for stamp, starting in sorted(items):
            if starting == 1:
                open_at = stamp
            elif open_at is not None:
                for day, piece_start, piece_end in split_days(open_at, max(stamp, open_at)):
                    intervals[device, bundle, day].append((piece_start, piece_end))
                open_at = None
    day_totals = defaultdict(float)
    buckets = {}
    for (device, bundle, day), pieces in intervals.items():
        seconds = union_seconds(pieces)
        buckets[device, bundle, day] = (seconds, len(pieces), min(s for s, _ in pieces), max(e for _, e in pieces))
        day_totals[device, day] += seconds
    for (device, bundle, day), (seconds, sessions, start, end) in sorted(buckets.items(), key=lambda kv: (kv[0][2], kv[0][1])):
        share = seconds / day_totals[device, day] if day_totals[device, day] else 0.0
        builder.edge(builder.app(bundle, source='Biome'), builder.day(day), 'USED_ON',
                     dict(first_start=apple_time(start) or '', last_end=apple_time(end) or '', seconds=int(round(seconds)),
                          minutes=round(seconds / 60, 1), sessions=sessions, share_of_day=round(share, 4),
                          device=device_names.get(device, 'synced device'), source='Biome'))
    return dict(focus_sessions=sum(len(v) for v in intervals.values()), used_on_edges=len(buckets), devices=len({d for d, _, _ in buckets}))


def add_biome(builder: Builder, root: Path, device_names: dict[str, str]) -> dict:
    if not root.is_dir():
        return {}
    return dict(messages=add_messages(builder, root / 'Siri.Remembers.MessageHistory'),
                calls=add_calls(builder, root / 'Siri.Remembers.CallHistory'),
                now_playing=add_now_playing(builder, root / 'Media.NowPlaying', device_names),
                app_focus=add_app_focus(builder, root / 'App.InFocus', device_names))
