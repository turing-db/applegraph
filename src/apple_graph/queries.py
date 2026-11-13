"""A catalogue of ready-made Cypher queries over the apple_life graph.

Queries come in three categories: ``insight`` (tables about your habits), ``inference`` (what
Apple can infer about you by joining sources) and ``visual`` (queries that return paths, for the
TuringDB visualizer).

    >>> from apple_graph.queries import connect, run, show, one_line
    >>> db = connect()                       # native client on 127.0.0.1:16666, graph apple_life
    >>> show()                               # every query name with a one-line description
    >>> show('visual')                       # only one category
    >>> run(db, 'inner_circle')              # returns a pandas DataFrame
    >>> run(db, 'trip', place='Paris')   # keyword arguments fill $parameters
    >>> print(one_line('trip', place='Paris'))   # pasteable into the visualizer
    >>> print(report(db, 'social_star'))     # a result table, with nodes and paths shown by name

The same is available from the shell with ``apple-graph queries [NAME] [--category C] [--param k=v]``.
"""
from __future__ import annotations

import numbers
import re
import shutil
import sys
import unicodedata

QUERIES: dict[str, tuple[str, str]] = {
    # Who matters -------------------------------------------------------------------------
    'inner_circle': ('People ranked across messages, calls and talk time', '''
        MATCH (me:Me)-[r:MESSAGES_WITH|CALLS_WITH|EMAILED]->(p)
        WITH p, sum(coalesce(r.messages, 0)) AS messages, sum(coalesce(r.calls, 0)) AS calls,
             sum(coalesce(r.talk_seconds, 0)) / 60 AS talk_minutes
        RETURN p.name, messages, calls, talk_minutes
        ORDER BY messages + calls * 20 DESC LIMIT 20'''),
    'never_emailed': ('Heavy messaging contacts you have never emailed', '''
        MATCH (me:Me)-[m:MESSAGES_WITH]->(p:Person)
        WHERE NOT (me)-[:EMAILED]-(p) AND m.messages > 100
        RETURN p.name, m.messages, m.apps ORDER BY m.messages DESC'''),
    'conversation_balance': ('Who writes more in each conversation', '''
        MATCH (me:Me)-[m:MESSAGES_WITH]->(p)
        WHERE m.messages >= 50
        RETURN p.name, m.sent, m.received, round(100.0 * m.sent / m.messages) AS percent_you
        ORDER BY percent_you DESC'''),
    'gone_quiet': ('Big conversations with no message since September', '''
        MATCH (me:Me)-[m:MESSAGES_WITH]->(p)
        WHERE m.messages >= 100 AND m.last < '2026-09-01'
        RETURN p.name, m.messages, m.last ORDER BY m.last'''),
    # Days and rhythms ---------------------------------------------------------------------
    'busiest_days': ('Days with the most activity across every source', '''
        MATCH (d:Day)<-[r]-(x)
        WITH d, count(r) AS touches, sum(coalesce(r.messages, 0)) AS messages, sum(coalesce(r.seconds, 0)) / 3600 AS app_hours
        RETURN d.date, d.weekday, touches, messages, app_hours, d.photos_taken, d.screen_on_seconds / 3600 AS screen_hours
        ORDER BY touches DESC LIMIT 15'''),
    'weekday_messaging': ('Messages per weekday', '''
        MATCH (p)-[m:MESSAGED_ON]->(d:Day)
        RETURN d.weekday, sum(m.messages) AS messages, count(DISTINCT d) AS days,
               sum(m.messages) / count(DISTINCT d) AS per_day
        ORDER BY per_day DESC'''),
    'meetings_vs_terminal': ('Terminal minutes on days with and without calendar events', '''
        MATCH (s:Software {name: 'Ghostty'})-[u:USED_ON]->(d:Day)
        OPTIONAL MATCH (e:CalendarEvent)-[:SCHEDULED_ON]->(d)
        WITH d, u.minutes AS minutes, count(e) AS meetings
        RETURN meetings > 0 AS had_meetings, count(d) AS days, avg(minutes) AS avg_terminal_minutes'''),
    'browsing_on_meeting_days': ('Domains visited most on days that had meetings', '''
        MATCH (e:CalendarEvent)-[:SCHEDULED_ON]->(d:Day)<-[v:VISITED_ON]-(w:WebDomain)
        RETURN w.name, sum(v.visits) AS visits, count(DISTINCT d) AS meeting_days
        ORDER BY visits DESC LIMIT 15'''),
    # Photos -------------------------------------------------------------------------------
    'appears_with': ('Pairs of people photographed together at least 10 times', '''
        MATCH (a:Person)-[r:APPEARS_WITH]->(b:Person)
        WHERE r.photos >= 10
        RETURN a.name, b.name, r.photos, r.first, r.last ORDER BY r.photos DESC'''),
    'trips': ('Places photographed a lot on only a few days', '''
        MATCH (d:Day)-[r:PHOTOGRAPHED_AT]->(p:Place)
        WITH p, count(d) AS days, sum(r.photos) AS photos
        WHERE days <= 5 AND photos >= 20
        RETURN p.name, days, photos, p.latitude, p.longitude ORDER BY photos DESC'''),
    'photos_by_month': ('Photo and video volume per month', '''
        MATCH (m:Month)<-[:IN_MONTH]-(d:Day)
        WHERE d.photos_taken IS NOT NULL
        RETURN m.month, sum(d.photos_taken) AS photos, sum(d.videos_taken) AS videos
        ORDER BY m.month'''),
    # Media and apps -----------------------------------------------------------------------
    'work_soundtrack': ('Artists played on heavy terminal days', '''
        MATCH (i:MediaItem)-[p:PLAYED_ON]->(d:Day)<-[u:USED_ON]-(s:Software {name: 'Ghostty'})
        WHERE u.minutes > 180
        RETURN i.artist, count(DISTINCT i) AS items, sum(p.plays) AS plays
        ORDER BY plays DESC LIMIT 15'''),
    'app_by_device': ('Hours per device for one app (parameter: app)', '''
        MATCH (s:Software {name: $app})-[u:USED_ON]->(d:Day)
        RETURN s.name, u.device, sum(u.seconds) / 3600 AS hours, count(d) AS days
        ORDER BY hours DESC'''),
    'all_apps_by_device': ('Hours per app and device', '''
        MATCH (s:Software)-[u:USED_ON]->(d:Day)
        WITH s, u.device AS device, sum(u.seconds) / 3600 AS hours
        RETURN s.name, device, hours ORDER BY hours DESC LIMIT 40'''),
    'unused_apps': ('Third-party apps Apple knows about that you never use', '''
        MATCH (s:Software)-[:PUBLISHED_BY]->(o:Organization)
        WHERE NOT (s)-[:USED_ON]->() AND o.name <> 'Apple'
        RETURN s.name, o.name'''),
    # Mail ---------------------------------------------------------------------------------
    'mail_balance': ('Correspondents by received versus sent', '''
        MATCH (c)-[inbound:EMAILED]->(me:Me)
        OPTIONAL MATCH (me)-[outbound:EMAILED]->(c)
        RETURN c.name, c.entity_type, inbound.messages AS received, coalesce(outbound.messages, 0) AS sent
        ORDER BY received DESC LIMIT 25'''),
    'mail_triangles': ('Pairs of frequent senders who also email each other', '''
        MATCH (a)-[r1:EMAILED]->(me:Me)<-[r2:EMAILED]-(b)
        WHERE a <> b AND r1.conversations > 3 AND r2.conversations > 3
        MATCH (a)-[:EMAILED]->(b)
        RETURN a.name, b.name, r1.messages, r2.messages LIMIT 20'''),
    # Multi-hop ----------------------------------------------------------------------------
    'photo_friends_of_friends': ('People in photos with your close contacts whom you never talk to', '''
        MATCH (me:Me)-[m:MESSAGES_WITH]->(friend:Person)-[a:APPEARS_WITH]-(other:Person)
        WHERE m.messages >= 100 AND a.photos >= 5 AND other <> me AND NOT (me)-[:MESSAGES_WITH|CALLS_WITH|EMAILED]-(other)
        RETURN other.name, count(DISTINCT friend) AS mutual_friends, sum(a.photos) AS shared_photos
        ORDER BY mutual_friends DESC, shared_photos DESC LIMIT 20'''),
    'where_when_messaging': ('Places photographed on the days you messaged each person', '''
        MATCH (me:Me)-[:MESSAGES_WITH]->(p:Person)-[m:MESSAGED_ON]->(d:Day)-[:PHOTOGRAPHED_AT]->(place:Place)
        WHERE m.messages >= 20
        RETURN p.name, place.name, count(DISTINCT d) AS days, sum(m.messages) AS messages
        ORDER BY messages DESC LIMIT 20'''),
    'messages_on_meeting_days': ('Messages exchanged with attendees on the days you met them', '''
        MATCH (e:CalendarEvent)-[:HAS_ATTENDEE|HAS_ORGANIZER]->(p:Person)
        MATCH (e)-[:SCHEDULED_ON]->(d:Day)<-[m:MESSAGED_ON]-(p)
        RETURN p.name, count(DISTINCT e) AS meetings, sum(m.messages) AS messages_on_meeting_days
        ORDER BY meetings DESC'''),
    'photo_cliques': ('People within three co-appearance hops of you (variable-length path)', '''
        MATCH path = (me:Me)-[:APPEARS_WITH*1..3]-(p:Person)
        WHERE p <> me
        RETURN p.name, min(length(path)) AS degrees, count(path) AS paths
        ORDER BY degrees, paths DESC LIMIT 30'''),
    'trip_picture': ('Everything from the days photographed at one place (parameter: place)', '''
        MATCH (place:Place {name: $place})<-[:PHOTOGRAPHED_AT]-(d:Day)<-[r]-(x)
        WHERE NOT x:Place
        RETURN d.date, type(r) AS via, x.name, coalesce(r.messages, r.plays, r.minutes, r.photos) AS weight
        ORDER BY d.date, weight DESC'''),
    'soundtrack_by_domain': ('Artists played on days a domain was browsed heavily (parameter: domain)', '''
        MATCH (w:WebDomain {name: $domain})-[v:VISITED_ON]->(d:Day)<-[p:PLAYED_ON]-(i:MediaItem)
        WHERE v.visits >= 20
        RETURN i.artist, sum(p.plays) AS plays, count(DISTINCT d) AS days
        ORDER BY plays DESC LIMIT 15'''),
    'mail_messaging_bridges': ('Email correspondents who also email people you message', '''
        MATCH (c)-[e1:EMAILED]->(me:Me)-[m:MESSAGES_WITH]->(p:Person)<-[e2:EMAILED]-(c)
        WHERE c <> p AND e1.messages >= 3
        RETURN c.name, p.name, e1.messages AS to_you, e2.messages AS to_them, m.messages AS you_and_them
        ORDER BY to_you DESC'''),
    'apps_across_devices': ('Apps used on the Mac and a phone on the same day', '''
        MATCH (s:Software)-[mac:USED_ON {device: 'This Mac'}]->(d:Day)<-[phone:USED_ON]-(s)
        WHERE phone.device <> 'This Mac'
        RETURN s.name, count(d) AS shared_days, sum(mac.minutes) AS mac_minutes, sum(phone.minutes) AS phone_minutes
        ORDER BY shared_days DESC'''),
    'shortest_social_path': ('Shortest social path from you to a person (parameter: apple_id)', '''
        MATCH p = (me:Me)-[:APPEARS_WITH|MESSAGES_WITH|CALLS_WITH|EMAILED|MEMBER_OF_CHAT*1..4]-(x:Person {apple_id: $apple_id})
        RETURN p, length(p) AS hops ORDER BY hops LIMIT 1'''),
    # Visual -------------------------------------------------------------------------------
    'month_picture': ('Everything attached to the days of one month (parameter: month, e.g. 2026-10)', '''
        MATCH (d:Day)<-[r:MESSAGED_ON|CALLED_ON|SCHEDULED_ON|PLAYED_ON|PHOTOGRAPHED_ON]-(x)
        WHERE d.month = $month
        RETURN d, r, x'''),
}

INFERENCE: dict[str, tuple[str, str]] = {
    # Habits Apple can infer -----------------------------------------------------------------
    'sleep_schedule': ('First and last Mac app activity per day (UTC timestamps): when you woke and slept', '''
        MATCH (s:Software)-[r:USED_ON {device: 'This Mac'}]->(d:Day)
        RETURN d.date, d.weekday, min(r.first_start) AS first_touch, max(r.last_end) AS last_touch
        ORDER BY d.date DESC LIMIT 30'''),
    'lonely_days': ('Days with over ten hours of screen time and no messages or calls', '''
        MATCH (d:Day)
        WHERE d.screen_on_seconds > 36000 AND NOT ()-[:MESSAGED_ON]->(d) AND NOT ()-[:CALLED_ON]->(d)
        RETURN d.date, d.weekday, d.screen_on_seconds / 3600 AS screen_hours
        ORDER BY screen_hours DESC LIMIT 20'''),
    'bluetooth_routine': ('Bluetooth devices by weekday: commutes, gym, desk setups', '''
        MATCH (b:BluetoothDevice)-[r:CONNECTED_ON]->(d:Day)
        RETURN b.name, d.weekday, count(d) AS days, sum(r.seconds) / 3600 AS hours
        ORDER BY b.name, days DESC'''),
    'naggy_apps': ('Apps that notify you far more than you open them', '''
        MATCH (s:Software)-[n:NOTIFIED_ON]->(d:Day)
        OPTIONAL MATCH (s)-[u:USED_ON {device: 'This Mac'}]->(d)
        WITH s, sum(n.notifications) AS pings, sum(coalesce(u.minutes, 0)) AS minutes
        RETURN s.name, pings, minutes, toFloat(pings) / (minutes + 1) AS pings_per_minute
        ORDER BY pings_per_minute DESC LIMIT 15'''),
    'new_obsessions': ('Sites visited a lot that you discovered recently', '''
        MATCH (w:WebDomain)
        WHERE w.safari_visits >= 30
        RETURN w.domain, w.safari_visits, w.safari_first_visit, w.safari_last_visit
        ORDER BY w.safari_first_visit DESC LIMIT 20'''),
    'siri_ledger': ('Every action apps donated to Siri, by app and verb', '''
        MATCH (s:Software)-[r]->(d:Day)
        WHERE r.intent_class IS NOT NULL
        RETURN s.name, type(r) AS verb, r.direction, count(r) AS times,
               min(r.local_time) AS earliest_time, max(r.local_time) AS latest_time
        ORDER BY times DESC LIMIT 25'''),
    'day_reconstruction': ('Every app, person, song, site and photo tied to one day (parameter: date)', '''
        MATCH (d:Day {date: $date})<-[r]-(x)
        RETURN type(r) AS what, x.name, r.messages, r.calls, r.minutes, r.plays, r.visits, r.photos
        ORDER BY what'''),
    'big_days': ('Days you took the most photos, with how many people appear', '''
        MATCH (d:Day)
        WHERE d.photos_taken >= 50
        OPTIONAL MATCH (p:Person)-[:PHOTOGRAPHED_ON]->(d)
        RETURN d.date, d.weekday, d.photos_taken, count(p) AS people, collect(p.name) AS names
        ORDER BY d.photos_taken DESC LIMIT 15'''),
    # Relationships Apple can infer --------------------------------------------------------
    'drifted_apart': ('Once-heavy conversations, oldest last message first', '''
        MATCH (me:Me)-[r:MESSAGES_WITH]->(p:Person)
        WHERE r.messages >= 100
        RETURN p.name, r.messages, r.first, r.last ORDER BY r.last ASC LIMIT 15'''),
    'chase_ratio': ('Who you message far more than they message you', '''
        MATCH (me:Me)-[r:MESSAGES_WITH]->(p:Person)
        WHERE r.messages >= 50
        RETURN p.name, r.sent, r.received, toFloat(r.sent) / (r.received + 1) AS chase_ratio
        ORDER BY chase_ratio DESC LIMIT 15'''),
    'calls_not_texts': ('People you call but never message', '''
        MATCH (me:Me)-[c:CALLS_WITH]->(p:Person)
        WHERE NOT (me)-[:MESSAGES_WITH]-(p)
        RETURN p.name, c.calls, c.incoming, c.outgoing, c.talk_seconds / 60 AS minutes
        ORDER BY c.calls DESC LIMIT 15'''),
    'weekend_people': ('Messages per person split into weekend and weekday', '''
        MATCH (p:Person)-[r:MESSAGED_ON]->(d:Day)
        WITH p, d.weekday IN ['Saturday', 'Sunday'] AS weekend, sum(r.messages) AS messages
        WHERE messages >= 50
        RETURN p.name, weekend, messages ORDER BY p.name, weekend'''),
    'chat_only_people': ('People in your group chats you never message directly', '''
        MATCH (p:Person)-[:MEMBER_OF_CHAT]->(c:GroupChat)
        WHERE NOT (:Me)-[:MESSAGES_WITH]-(p)
        RETURN p.name, count(c) AS chats, collect(c.name) AS chat_names
        ORDER BY chats DESC LIMIT 20'''),
    'relationship_soundtrack': ('Songs played on days of heavy conversation with each person', '''
        MATCH (p:Person)-[m:MESSAGED_ON]->(d:Day)<-[:PLAYED_ON]-(s:MediaItem)
        WHERE m.messages >= 30
        RETURN p.name, s.title, s.artist, count(d) AS days
        ORDER BY days DESC LIMIT 25'''),
    'browsing_while_talking': ('Sites browsed on days you messaged one person a lot (parameter: person)', '''
        MATCH (p:Person {name: $person})-[m:MESSAGED_ON]->(d:Day)<-[v:VISITED_ON]-(w:WebDomain)
        WHERE m.messages >= 20
        RETURN w.domain, count(d) AS days, sum(v.visits) AS visits
        ORDER BY days DESC LIMIT 20'''),
    'secret_copies': ('People you blind-copy on email', '''
        MATCH (me:Me)-[r:EMAILED]->(p)
        WHERE r.bcc > 0
        RETURN p.name, r.bcc, r.messages, r.first, r.last ORDER BY r.bcc DESC LIMIT 15'''),
    # Places and faces ---------------------------------------------------------------------
    'home_from_photos': ('Places photographed on the most distinct days: home, work, partner', '''
        MATCH (d:Day)-[r:PHOTOGRAPHED_AT]->(pl:Place)
        RETURN pl.name, pl.latitude, pl.longitude, count(d) AS days, sum(r.photos) AS photos
        ORDER BY days DESC LIMIT 10'''),
    'who_where': ('Which person shows up at which place', '''
        MATCH (pl:Place)<-[:PHOTOGRAPHED_AT]-(d:Day)<-[:PHOTOGRAPHED_ON]-(p:Person)
        WHERE NOT p:Me
        RETURN pl.name, p.name, count(d) AS days ORDER BY days DESC LIMIT 25'''),
    'unnamed_faces': ('Recurring people you never named, most recent first', '''
        MATCH (me:Me)-[r:APPEARS_WITH]-(p:Person)
        WHERE (p.name STARTS WITH 'Face cluster' OR p.name STARTS WITH 'person ') AND r.photos >= 5
        RETURN p.name, p.apple_id, p.photos_person_uuid, p.photos_faces, r.photos, r.first, r.last
        ORDER BY r.last DESC LIMIT 20'''),
    'met_and_messaged': ('People photographed on a day versus messaged that same day', '''
        MATCH (p:Person)-[:PHOTOGRAPHED_ON]->(d:Day)
        WHERE NOT p:Me
        OPTIONAL MATCH (p)-[m:MESSAGED_ON]->(d)
        RETURN p.name, count(d) AS photo_days, count(m) AS also_messaged
        ORDER BY photo_days DESC LIMIT 20'''),
    'identity_gap': ('People present in both Messages and Photos (1 means only you)', '''
        MATCH (p:Person)
        WHERE (p)-[:MESSAGES_WITH]-() AND (p)-[:APPEARS_WITH]-()
        RETURN p.name, p.apple_id'''),
}

VISUAL: dict[str, tuple[str, str]] = {
    'social_star': ('You at the centre, a spoke per close person, one edge per channel', '''
        MATCH p = (me:Me)-[r:MESSAGES_WITH|CALLS_WITH|APPEARS_WITH]-(x:Person)
        WHERE r.messages >= 100 OR r.calls >= 10 OR r.photos >= 20
        RETURN p LIMIT 150'''),
    'group_chats': ('Group chats as hubs with members around them', '''
        MATCH p = (x:Person)-[:MEMBER_OF_CHAT]->(c:GroupChat)
        RETURN p LIMIT 400'''),
    'photo_circles': ('Who is photographed together, without you, so friend groups separate', '''
        MATCH p = (a:Person)-[r:APPEARS_WITH]-(b:Person)
        WHERE r.photos >= 10 AND NOT a:Me AND NOT b:Me AND id(a) < id(b)
        RETURN p LIMIT 300'''),
    'photo_day': ('Everyone photographed on one date and their shared-photo edges (parameter: date)', '''
        MATCH (a:Person)-[:PHOTOGRAPHED_ON]->(d:Day {date: $date})<-[:PHOTOGRAPHED_ON]-(b:Person)
        MATCH p = (a)-[:APPEARS_WITH]-(b)
        WHERE id(a) < id(b)
        RETURN p'''),
    'friends_of_friends': ('Your photo companions and the people behind them you never message', '''
        MATCH p = (me:Me)-[a:APPEARS_WITH]-(x:Person)-[b:APPEARS_WITH]-(y:Person)
        WHERE a.photos >= 10 AND b.photos >= 5 AND y <> me AND NOT (me)-[:MESSAGES_WITH]-(y)
        RETURN p LIMIT 100'''),
    'places': ('Places fanning into days, days fanning into people', '''
        MATCH p = (pl:Place)<-[:PHOTOGRAPHED_AT]-(d:Day)<-[:PHOTOGRAPHED_ON]-(x:Person)
        WHERE NOT x:Me
        RETURN p LIMIT 300'''),
    'trip': ('People, chats and songs around one place (parameter: place)', '''
        MATCH p = (pl:Place {name: $place})<-[:PHOTOGRAPHED_AT]-(d:Day)<-[:PHOTOGRAPHED_ON|MESSAGED_ON|PLAYED_ON]-(x)
        RETURN p LIMIT 300'''),
    'month': ('One month as a hub of days with the busiest people around them (parameter: month)', '''
        MATCH p = (m:Month {month: $month})<-[:IN_MONTH]-(d:Day)<-[r:MESSAGED_ON|CALLED_ON|PHOTOGRAPHED_ON]-(x)
        WHERE r.messages >= 20 OR r.calls >= 1 OR r.photos >= 3
        RETURN p LIMIT 300'''),
    'soundtrack': ('Songs, days and people: what you played around whom', '''
        MATCH p = (s:MediaItem)-[a:PLAYED_ON]->(d:Day)<-[b:MESSAGED_ON]-(x:Person)
        WHERE a.plays >= 3 AND b.messages >= 30
        RETURN p LIMIT 200'''),
    'person': ('Two hops around one person, without the month nodes (parameter: person)', '''
        MATCH p = (x:Person {name: $person})-[*1..2]-(y)
        WHERE NOT y:Month
        RETURN p LIMIT 200'''),
    'mail_loops': ('Closed email loops between you and two correspondents', '''
        MATCH p = (me:Me)-[:EMAILED]-(a)-[:EMAILED]-(b)-[:EMAILED]-(me)
        WHERE id(a) < id(b)
        RETURN p LIMIT 150'''),
    'meetings': ('Calendar events bound to attendees you also message', '''
        MATCH p = (e:CalendarEvent)-[:HAS_ATTENDEE]->(x:Person)<-[:MESSAGES_WITH]-(me:Me)
        RETURN p LIMIT 200'''),
    'other_devices': ('Apps used for 30+ minutes on synced devices, by day', '''
        MATCH p = (s:Software)-[r:USED_ON]->(d:Day)
        WHERE r.device <> 'This Mac' AND r.minutes >= 30
        RETURN p LIMIT 300'''),
}

VISUAL_INSIGHT = ('month_picture', 'shortest_social_path')

CATEGORIES: dict[str, str] = {name: 'insight' for name in QUERIES}
CATEGORIES.update({name: 'visual' for name in VISUAL_INSIGHT})
for _category, _entries in (('inference', INFERENCE), ('visual', VISUAL)):
    _clash = set(_entries) & set(QUERIES)
    assert not _clash, _clash
    QUERIES.update(_entries)
    CATEGORIES.update({name: _category for name in _entries})


def connect(port: int = 16666, graph: str = 'apple_life', host: str = '127.0.0.1'):
    """Native client on the running local server, with the graph selected."""
    from turingdb import TuringDB
    db = TuringDB(type='native', host=host, port=port)
    db.load_graph(graph, raise_if_loaded=False)
    db.set_graph(graph)
    return db


def show(category: str | None = None) -> None:
    """Print query names and descriptions, optionally for one category."""
    names = [name for name in QUERIES if category is None or CATEGORIES[name] == category]
    if not names:
        raise KeyError(f'Unknown category {category!r}; use one of {sorted(set(CATEGORIES.values()))}')
    width = max(len(name) for name in names)
    for name in names:
        print(f'{name:<{width}}  [{CATEGORIES[name]}] {QUERIES[name][0]}')


# Every catalogue parameter is text, including apple_id, which looks like a number but is stored as a string.
TEXT_PARAMETERS = {'app', 'apple_id', 'date', 'domain', 'month', 'person', 'place'}


def parameter_value(key: str, text: str):
    """A --param value: text for the catalogue's text parameters, otherwise numbers stay numbers."""
    if key in TEXT_PARAMETERS:
        return text
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def parameters(name: str) -> list[str]:
    """The $parameters a catalogue query expects."""
    return sorted(set(re.findall(r'\$([A-Za-z_][A-Za-z0-9_]*)', QUERIES[name][1])))


def cypher(name: str) -> str:
    return QUERIES[name][1].strip()


def _literal(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def one_line(name: str, **values) -> str:
    """The query on a single line with $parameters inlined, for pasting into the visualizer."""
    missing = [key for key in parameters(name) if key not in values]
    if missing:
        raise KeyError(f'{name} needs parameter(s): {", ".join(missing)}')
    text = ' '.join(cypher(name).split())
    return re.sub(r'\$([A-Za-z_][A-Za-z0-9_]*)', lambda match: _literal(values[match.group(1)]), text)


def run(db, name: str, **values):
    """Run a catalogue query; keyword arguments fill $parameters."""
    missing = [key for key in parameters(name) if key not in values]
    if missing:
        raise KeyError(f'{name} needs parameter(s): {", ".join(missing)}')
    if values:
        return db.query(cypher(name), values)
    return db.query(cypher(name))


# Result tables ----------------------------------------------------------------------------------
TIMESTAMP = re.compile(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?')


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and value != value) or type(value).__name__ in ('NAType', 'NaTType')


def _is_bool(value) -> bool:
    return isinstance(value, bool) or type(value).__name__ in ('bool_', 'bool')  # numpy 1 and 2


def _is_number(value) -> bool:
    return isinstance(value, numbers.Number) and not _is_bool(value)


def cell(value) -> str:
    """One value as table text: thousands separators, two decimals, short UTC timestamps, joined lists."""
    if _missing(value):
        return ''
    if _is_bool(value):
        return 'true' if value else 'false'
    if isinstance(value, numbers.Integral):
        return f'{int(value):,}'
    if isinstance(value, numbers.Real):
        return f'{value:,.0f}' if float(value).is_integer() else f'{value:,.2f}'
    if isinstance(value, (list, tuple)):
        return ', '.join(cell(item) for item in value)
    text = ' '.join(str(value).split())
    match = TIMESTAMP.fullmatch(text)
    if match:
        date, time, zone = match.groups()
        return f"{date} {time}{' UTC' if zone in ('Z', '+00:00') else f' {zone}' if zone else ''}"
    return text


def display_width(text: str) -> int:
    """Terminal columns: wide characters and most emoji take two, combining marks none."""
    return sum(0 if unicodedata.combining(ch) or ch in '‍️' else 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
               for ch in text)


def clip(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        size = display_width(ch)
        if used + size > width - 1:
            break
        out.append(ch)
        used += size
    return ''.join(out) + '…'


def table(df, total: int | None = None, width: int | None = None, bold: bool | None = None) -> str:
    """A DataFrame as a box-drawn table that fits the terminal, numbers right-aligned.

    ``total`` is the row count before the frame was cut down, for the footer.
    """
    width = width or shutil.get_terminal_size((120, 24)).columns
    bold = sys.stdout.isatty() if bold is None else bold
    headers = [str(column) for column in df.columns]
    rows = [[cell(value) for value in record] for record in df.itertuples(index=False, name=None)]
    numeric = []
    for column in df.columns:
        present = [value for value in df[column] if not _missing(value)]
        numeric.append(bool(present) and all(_is_number(value) for value in present))
    widths = [max([display_width(h), *(display_width(r[i]) for r in rows)]) for i, h in enumerate(headers)]
    budget = max(width - 3 * len(widths) - 1, 4 * len(widths))
    widths = [min(w, budget) for w in widths]
    while sum(widths) > budget and max(widths) > 4:
        widest = widths.index(max(widths))
        widths[widest] -= max(1, (sum(widths) - budget) // 4)
        widths[widest] = max(widths[widest], 4)

    def line(left, middle, right):
        return left + middle.join('─' * (w + 2) for w in widths) + right

    def row(values, header=False):
        cells = []
        for value, w, right in zip(values, widths, numeric):
            text = clip(value, w)
            pad = ' ' * (w - display_width(text))
            if header and bold:
                text = f'\033[1m{text}\033[0m'
            cells.append(pad + text if right else text + pad)
        return '│ ' + ' │ '.join(cells) + ' │'
    out = [line('┌', '┬', '┐'), row(headers, header=True), line('├', '┼', '┤'), *(row(r) for r in rows), line('└', '┴', '┘')]
    total = len(rows) if total is None else total
    if total == len(rows):
        out.append('no rows' if total == 0 else '1 row' if total == 1 else f'{total:,} rows')
    else:
        out.append(f'{len(rows):,} of {total:,} rows')
    return '\n'.join(out)


def _variables(cypher_text: str) -> tuple[set[str], set[str], set[str]]:
    """Path, relationship and node variables bound in a query's patterns."""
    # A pattern's "(" or "[" never directly follows a name, which tells nodes apart from calls like count(d).
    paths = set(re.findall(r'(?<![\w.])([A-Za-z_]\w*)\s*=\s*\(', cypher_text))
    relationships = set(re.findall(r'(?<![\w)\]])\[\s*([A-Za-z_]\w*)', cypher_text))
    nodes = set(re.findall(r'(?<![\w.])\(\s*([A-Za-z_]\w*)\s*[:){]', cypher_text))
    return paths, relationships, nodes


def readable(db, cypher_text: str, df):
    """Replace returned nodes, relationships and paths, which arrive as internal ids, with names.

    Nodes show their name, relationships their type, and paths read like
    ``Me ─CALLS_WITH→ Alice ←APPEARS_WITH─ Bob``.
    """
    paths, relationships, nodes = _variables(cypher_text)
    kinds = {}
    for column in df.columns:
        if column in paths:
            kinds[column] = 'path'
        elif column in relationships:
            kinds[column] = 'relationship'
        elif column in nodes:
            kinds[column] = 'node'
    if not kinds or df.empty:
        return df
    node_ids, edge_ids = set(), set()
    for column, kind in kinds.items():
        for value in df[column]:
            if kind == 'path' and isinstance(value, (list, tuple)):
                node_ids.update(int(v) for v in value[0::2])
                edge_ids.update(int(v) for v in value[1::2])
            elif isinstance(value, numbers.Integral) and not _is_bool(value):
                (node_ids if kind == 'node' else edge_ids).add(int(value))
    names, edges = {}, {}
    if node_ids:
        found = db.query('MATCH (n) WHERE id(n) IN $ids RETURN id(n) AS id, n.name AS name', {'ids': sorted(node_ids)})
        names = {int(i): name for i, name in found.itertuples(index=False, name=None)}
    if edge_ids:
        found = db.query('MATCH (a)-[r]->(b) WHERE id(r) IN $ids RETURN id(r) AS id, type(r) AS type, id(a) AS a, id(b) AS b',
                         {'ids': sorted(edge_ids)})
        edges = {int(i): (kind, int(a), int(b)) for i, kind, a, b in found.itertuples(index=False, name=None)}

    def node_name(i):
        return str(names.get(int(i), f'#{i}'))

    def path_text(ids):
        parts = [node_name(ids[0])]
        for position in range(1, len(ids) - 1, 2):
            kind, start, _ = edges.get(int(ids[position]), ('?', None, None))
            parts.append(f'─{kind}→' if start == int(ids[position - 1]) else f'←{kind}─')
            parts.append(node_name(ids[position + 1]))
        return ' '.join(parts)
    df = df.copy()
    for column, kind in kinds.items():
        if kind == 'path':
            df[column] = [path_text(v) if isinstance(v, (list, tuple)) and v else v for v in df[column]]
        elif kind == 'node':
            df[column] = [node_name(v) if isinstance(v, numbers.Integral) and not _is_bool(v) else v for v in df[column]]
        else:
            df[column] = [edges.get(int(v), ('?',))[0] if isinstance(v, numbers.Integral) and not _is_bool(v) else v for v in df[column]]
    return df


def report(db, name: str, rows: int | None = 50, width: int | None = None, **values) -> str:
    """Run a catalogue query and lay out the first ``rows`` results (all if 0 or None) as a table."""
    df = run(db, name, **values)
    shown = df.head(rows) if rows else df
    return table(readable(db, cypher(name), shown), total=len(df), width=width)
