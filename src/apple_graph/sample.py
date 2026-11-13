"""A fictional Mac to try Apple Graph on without your own data.

``apple-graph sample`` writes the personal data stores of Oliver Dunmore, 34, co-founder and CEO of
Tallyhand (AI agents that reconcile invoices). He lives in Shoreditch with his fiancée Sophie, plays
padel on Tuesdays and Thursdays and listens to Coldplay. The folder is laid out like a home folder, so
``apple-graph --graph oliver import --home <folder>`` reads it like a real Mac: Apple's knowledge graph,
knowledgeC, Biome, Safari, Contacts, Mail and Photos, in the schemas the importers expect.

Oliver is very boring. Mostly. No single source gives him away, but joining them does.

Everything is invented and generated from a fixed seed, so every build is identical. Phone numbers come
from the ranges Ofcom reserves for drama (07700 900xxx, 020 7946 0xxx, 0117 496 0xxx, 01632 960xxx).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import random
import shutil
import sqlite3
import struct
import uuid as uuidlib
from zoneinfo import ZoneInfo

from .model import APPLE_EPOCH, HOME_LAYOUT

LONDON = ZoneInfo('Europe/London')
MARKER = '.apple-graph-sample'
SEED = 'oliver-dunmore'
LAST_DAY = date(2026, 11, 12)                      # The Mac was copied the next morning.
PHOTOS_FROM = date(2024, 1, 5)
MAIL_FROM = date(2025, 1, 2)
SAFARI_FROM = date(2025, 11, 13)                   # Safari keeps a year.
BIOME_FROM = date(2026, 5, 1)                      # Siri.Remembers message and call history.
MEDIA_FROM = date(2026, 8, 1)
RECENT_FROM = LAST_DAY - timedelta(days=27)        # knowledgeC and App.InFocus keep four weeks.
IPHONE = '5C1E2A7B-0D7E-4F4B-9B1D-3A3F1C2B9E10'
IPHONE_MODEL = 'iPhone16,2'


# Time ------------------------------------------------------------------------------------------
def clock(day: date, minutes: float) -> datetime:
    """London wall-clock time, ``minutes`` after local midnight."""
    return datetime(day.year, day.month, day.day, tzinfo=LONDON) + timedelta(minutes=minutes)


def apple(moment: datetime) -> float:
    return (moment - APPLE_EPOCH).total_seconds()


def gmt_offset(moment: datetime) -> int:
    return int(moment.utcoffset().total_seconds())


def every_day(start: date, end: date = LAST_DAY):
    for n in range((end - start).days + 1):
        yield start + timedelta(days=n)


def new_uuid(rng: random.Random) -> str:
    return str(uuidlib.UUID(int=rng.getrandbits(128), version=4)).upper()


def calendar_date(moment: datetime) -> str:
    """Apple's JSON calendar-date object, as the knowledge graph stores event times."""
    return json.dumps(dict(day=moment.day, hour=moment.hour, isLeapMonth=False, minute=moment.minute, month=moment.month,
                           timeZone={'identifier': 'Europe/London'}, year=moment.year), sort_keys=True, separators=(',', ':'))


# Oliver's fortnight ----------------------------------------------------------------------------
ANCHOR_FRIDAY = date(2026, 10, 23)
SKIPPED_FRIDAYS = {date(2026, 2, 13)}              # Proposed to Sophie on Valentine's Day instead.
CAMP = (date(2025, 12, 8), date(2025, 12, 12))     # "Padel training camp — Bristol".
TRIPS = {'Palma, Mallorca': (date(2025, 8, 11), date(2025, 8, 15), 7200),
         'Lisbon': (date(2026, 5, 14), date(2026, 5, 17), 3600)}


def bristol_friday(day: date) -> bool:
    """Every other Friday he catches the 17:30 from Paddington for a 'padel tournament (away)'."""
    return day.weekday() == 4 and (ANCHOR_FRIDAY - day).days % 14 == 0 and day not in SKIPPED_FRIDAYS


def in_bristol(day: date) -> bool:
    if CAMP[0] <= day <= CAMP[1]:
        return True
    return day.weekday() in (5, 6) and bristol_friday(day - timedelta(days=day.weekday() - 4))


def on_trip(day: date) -> str | None:
    return next((place for place, (start, end, _) in TRIPS.items() if start <= day <= end), None)


def in_london(day: date) -> bool:
    return not in_bristol(day) and on_trip(day) is None


def padel_night(day: date) -> bool:
    return day.weekday() in (1, 3) and in_london(day)


# The cast --------------------------------------------------------------------------------------
# key: first, last, organisation, job, birthday, phones, emails, Photos name. 'graph' people are also
# entities in Apple's knowledge graph; the rest exist only in Contacts.
CAST = {
    'me': dict(first='Oliver', last='Dunmore', org='Tallyhand', job='Co-founder & CEO', birthday=date(1992, 3, 14),
               phones=[('iPhone', '+44 7700 900123')], emails=[('work', 'oliver@tallyhand.ai'), ('home', 'ollie.dunmore@icloud.com')],
               address=('home', 'Flat 6, 41 Arnold Mews', 'London', 'E2 7QR', 'United Kingdom'), photos='Oliver Dunmore'),
    'sophie': dict(first='Sophie', last='Lang', org='Fieldwork Studio', job='Senior Product Designer', birthday=date(1993, 11, 19),
                   phones=[('mobile', '+44 7700 900101')], emails=[('home', 'soph.lang@icloud.com')],
                   address=('home', 'Flat 6, 41 Arnold Mews', 'London', 'E2 7QR', 'United Kingdom'), photos='Sophie Lang'),
    'marcus': dict(first='Marcus', last='Feld', org='Tallyhand', job='Co-founder & CTO', phones=[('mobile', '+44 7700 900202')],
                   emails=[('work', 'marcus@tallyhand.ai')], photos='Marcus Feld'),
    'jonny': dict(first='Jonny', last='Ries', phones=[('mobile', '+44 7700 900203')], photos='Jonny Ries'),
    'dev': dict(first='Dev', last='Malhotra', phones=[('mobile', '+44 7700 900204')], photos='Dev Malhotra'),
    'will': dict(first='Will', last='Asquith', phones=[('mobile', '+44 7700 900205')], photos='Will Asquith'),
    'henrietta': dict(first='Henrietta', last='Cole', org='Northbeam Ventures', job='Partner', phones=[('work', '+44 20 7946 0311')],
                      emails=[('work', 'henrietta@northbeam.vc')]),
    'mum': dict(first='Mum', phones=[('mobile', '+44 7700 900050')], emails=[('home', 'carol.dunmore@btinternet.com')], photos='Mum'),
    'dad': dict(first='Dad', phones=[('mobile', '+44 7700 900051')], photos='Dad'),
    'hannah': dict(first='Hannah', last='Dunmore', phones=[('mobile', '+44 7700 900052')], photos='Hannah Dunmore'),
    'ana': dict(first='Ana', last='Sousa', org='Tallyhand', job='Head of Operations', phones=[('mobile', '+44 7700 900206')],
                emails=[('work', 'ana@tallyhand.ai')]),
    'tom': dict(first='Tom', last='Becker', org='Tallyhand', job='Founding Engineer', phones=[('mobile', '+44 7700 900207')],
                emails=[('work', 'tom@tallyhand.ai')]),
    # A booking line on a mobile number, last edited on 2 March 2024.
    'padel': dict(first='Padel Club Bookings', phones=[('mobile', '+44 7700 900417')], modified=date(2024, 3, 2)),
    'courtside': dict(org='Courtside Padel Bethnal Green', phones=[('work', '+44 20 7946 0321')],
                      emails=[('work', 'bookings@courtsidepadel.co.uk')], graph=False),
    'hartwell': dict(org='Hartwell Barn Weddings', phones=[('work', '+44 1632 960012')], emails=[('work', 'events@hartwellbarn.co.uk')],
                     graph=False),
    'gary': dict(first='Gary', last='Plumber', phones=[('mobile', '+44 7700 900333')], graph=False),
}
NURSERY_PHONE = '+44 117 496 0832'                 # Never saved.
PARENTS = [('+44 7700 900511', 'Bea (Ivy’s mum)'), ('+44 7700 900512', 'Tom H'), ('+44 7700 900513', 'Ffion'),
           ('+44 7700 900514', 'Jas 🌻'), ('+44 7700 900515', 'Rich – Arlo’s dad'), ('+44 7700 900516', 'Mel')]
# Face clusters Photos found but nobody named. Apple's graph knows the first two as people.
UNNAMED = ('katie', 'pip', 'baby', 'sophies_friend')


def phone(key: str) -> str:
    return CAST[key]['phones'][0][1]


def handle(number: str) -> str:
    return number.replace(' ', '')


# Apple's knowledge graph -----------------------------------------------------------------------
PREDICATES = ['is a', 'name', 'first name', 'family name', 'is current user', 'job title', 'works for', 'date of birth',
              'identifier', 'identifier type', 'identifier id', 'same as', 'has contact information', 'phone number',
              'email address', 'contact label', 'bundle id', 'published by', 'has date', 'start time', 'end time', 'is all day',
              'has attendee', 'attendee identifier', 'participant obligation', 'has organizer', 'organizer identifier',
              'has location relationship', 'location identifier', 'address string', 'city', 'description',
              'has frequent location relationship', 'location label', 'visit count', 'nights spent']
P = {label: f'PS{index + 1}' for index, label in enumerate(PREDICATES)}   # 'is a' is PS1, as on a real Mac.
CLASSES = {'SB104': 'person', 'SB200': 'software', 'SB300': 'calendar event', 'SB400': 'organization', 'SB500': 'place',
           'CS38': 'participation relationship type'}
C = {label: code for code, label in CLASSES.items()}
GRAPH_SCHEMA = ('(subject INTEGER, predicate TEXT, relationshipId INTEGER, relationshipPredicate TEXT, object TEXT, '
                'sources INTEGER, confidence REAL, sourceDuplicates TEXT, timestamp REAL)')
SOFTWARE = [  # name, bundle id, publisher (None: no organisation)
    ('Slack', 'com.tinyspeck.slackmacgap', 'Slack Technologies'), ('Notion', 'notion.id', 'Notion Labs'),
    ('Linear', 'com.linear', 'Linear Orbit'), ('zoom.us', 'us.zoom.xos', 'Zoom Communications'),
    ('Safari', 'com.apple.Safari', 'Apple'), ('Mail', 'com.apple.mail', 'Apple'), ('Messages', 'com.apple.MobileSMS', 'Apple'),
    ('Calendar', 'com.apple.iCal', 'Apple'), ('Keynote', 'com.apple.iWork.Keynote', 'Apple'), ('Photos', 'com.apple.mobileslideshow', 'Apple'),
    ('Phone', 'com.apple.mobilephone', 'Apple'), ('FaceTime', 'com.apple.facetime', 'Apple'), ('Maps', 'com.apple.Maps', 'Apple'),
    ('Find My', 'com.apple.findmy', 'Apple'), ('Ghostty', 'com.mitchellh.ghostty', None),
    ('Visual Studio Code', 'com.microsoft.VSCode', 'Microsoft'), ('Spotify', 'com.spotify.client', 'Spotify'),
    ('WhatsApp', 'net.whatsapp.WhatsApp', 'WhatsApp'), ('LinkedIn', 'com.linkedin.LinkedIn', 'LinkedIn'),
    ('Playtomic', 'com.playtomic.app', 'Playtomic'), ('Citymapper', 'com.citymapper.app.release', 'Citymapper'),
    ('Monzo', 'co.uk.getmondo', 'Monzo Bank'), ('Trainline', 'com.thetrainline.app', 'Trainline'),
    ('Famly', 'app.famly.parent', 'Famly'), ('CBeebies Go Explore', 'uk.co.bbc.cbeebiesgoexplore', 'BBC'),
    ('Strava', 'com.strava.stravaride', 'Strava'), ('Headspace', 'com.getsomeheadspace.headspace', 'Headspace'),
    ('Duolingo', 'com.duolingo.DuolingoMobile', 'Duolingo'),
]
BUNDLE = {name: bundle for name, bundle, _ in SOFTWARE}


class Facts:
    """Subject/predicate/object rows the way graph.db stores them, with relationship groups."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.rows: dict[str, list[tuple]] = defaultdict(list)
        self.relationships = 0
        self.subjects = 0
        self.stamp = apple(clock(date(2026, 11, 2), 3 * 60 + 12))

    def subject(self) -> int:
        self.subjects += 1
        return 405112233440000000 + self.subjects * 104729

    def add(self, table: str, subject: int, predicate: str, value, relationship: int = 0, inner: str = '', when: float | None = None) -> None:
        self.rows[table].append((subject, P[predicate], relationship, P[inner] if inner else '', str(value),
                                 self.rng.choice((1, 2, 3, 32)), round(self.rng.uniform(.6, 1), 3), 'source', when or self.stamp))

    def fact(self, subject: int, predicate: str, value, table: str = 'stable_graph', when: float | None = None) -> None:
        self.add(table, subject, predicate, value, when=when)

    def group(self, subject: int, predicate: str, pairs, table: str = 'stable_graph', kind: str | None = None) -> None:
        self.relationships += 1
        if kind:
            self.add(table, subject, predicate, C[kind], self.relationships, 'is a')
        for inner, value in pairs:
            self.add(table, subject, predicate, value, self.relationships, inner)

    def entity(self, kind: str, table: str = 'stable_graph', **facts) -> int:
        subject = self.subject()
        self.fact(subject, 'is a', C[kind], table)
        for predicate, value in facts.items():
            self.fact(subject, predicate.replace('_', ' '), value, table)
        return subject


def write_knowledge_graph(folder: Path, ids: dict) -> dict:
    """graph.db and ontology.db. Fills ``ids`` with the Apple subject of each person, place and app."""
    rng = random.Random(f'{SEED}-graph')
    g = Facts(rng)
    organisations = {}
    for name in sorted({org for _, _, org in SOFTWARE if org} | {'Tallyhand', 'Northbeam Ventures', 'Fieldwork Studio'}):
        organisations[name] = g.entity('organization', name=name)
    for name, bundle, publisher in SOFTWARE:
        subject = ids[name] = g.entity('software', name=name, bundle_id=bundle)
        if publisher:
            g.fact(subject, 'published by', f'md:{organisations[publisher]}')
    for key, person in CAST.items():
        if not person.get('graph', True):
            continue
        subject = ids[key] = g.subject()
        g.fact(subject, 'is a', C['person'])
        full = ' '.join(p for p in (person.get('first'), person.get('last')) if p)
        g.fact(subject, 'name', full)
        g.fact(subject, 'first name', person['first'])
        if person.get('last'):
            g.fact(subject, 'family name', person['last'])
        if person.get('job'):
            g.fact(subject, 'job title', person['job'])
        if person.get('org'):
            g.fact(subject, 'works for', f"md:{organisations[person['org']]}")
        if person.get('birthday'):
            born = person['birthday']
            g.fact(subject, 'date of birth', json.dumps(dict(day=born.day, month=born.month, year=born.year), separators=(',', ':')))
        contact_id = ids[f'cn:{key}'] = f'{new_uuid(rng)}:ABPerson'
        g.fact(subject, 'same as', f'cnContact:{contact_id}')
        g.group(subject, 'identifier', [('identifier type', 'cnContact'), ('identifier id', contact_id)])
        if person.get('photos'):
            ids[f'ph:{key}'] = new_uuid(rng)
            g.group(subject, 'identifier', [('identifier type', 'phPerson'), ('identifier id', ids[f'ph:{key}'] + '/L0/050')])
        for label, number in person.get('phones', []):
            g.group(subject, 'has contact information', [('phone number', number), ('contact label', label)])
        for label, address in person.get('emails', []):
            g.group(subject, 'has contact information', [('email address', address), ('contact label', label)])
    g.fact(ids['me'], 'is current user', 1)
    # Two recurring faces Apple has clustered into people but nobody has named.
    for key in ('katie', 'pip'):
        subject = ids[key] = g.subject()
        g.fact(subject, 'is a', C['person'])
        ids[f'ph:{key}'] = new_uuid(rng)
        g.group(subject, 'identifier', [('identifier type', 'phPerson'), ('identifier id', ids[f'ph:{key}'] + '/L0/050')])
    # Places, and where Apple has noticed Oliver sleeps.
    places = {
        'home': dict(name='Home', address_string='Flat 6, 41 Arnold Mews, London E2 7QR', city='London'),
        'office': dict(name='Tallyhand office', address_string='3rd Floor, 88 Paul Street, London EC2A 4NE', city='London'),
        'courtside': dict(name='Courtside Padel Bethnal Green', address_string='Arch 214, Cambridge Heath Road, London E2 9NB', city='London'),
        'clifton': dict(address_string='9 Canynge Mews, Clifton, Bristol BS8 3LA', city='Bristol'),
        'bristol': dict(name='Bristol', city='Bristol'),
        'parents': dict(name='Mum & Dad', address_string='12 Larch Close, Guildford GU1 2QT', city='Guildford'),
        'hartwell': dict(name='Hartwell Barn', address_string='Hartwell Barn, near Chipping Norton OX7 5ZZ', city='Chipping Norton'),
    }
    for key, facts in places.items():
        ids[f'place:{key}'] = g.entity('place', **facts)
    for key, label, visits, nights in (('home', 'Home', 233, 271), ('office', 'Work', 198, 0), ('courtside', '', 71, 0),
                                       ('clifton', '', 29, 56), ('parents', '', 9, 3)):
        pairs = [('location identifier', f"md:{ids[f'place:{key}']}"), ('visit count', visits), ('nights spent', nights)]
        g.group(ids['me'], 'has frequent location relationship', pairs + ([('location label', label)] if label else []))

    def event(name: str, occurrences, attendees=(), place: str | None = None, all_day: bool = False, organiser: bool = False) -> int:
        subject = g.entity('calendar event', 'event_graph', name=name)
        g.fact(subject, 'is all day', int(all_day), 'event_graph')
        for start, end in occurrences:
            g.group(subject, 'has date', [('start time', calendar_date(start)), ('end time', calendar_date(end))], 'event_graph')
        for key in attendees:
            g.group(subject, 'has attendee', [('attendee identifier', f'md:{ids[key]}'), ('participant obligation', 'required')],
                    'event_graph', kind='participation relationship type')
        if organiser:
            g.group(subject, 'has organizer', [('organizer identifier', f"md:{ids['me']}")], 'event_graph')
        if place:
            g.group(subject, 'has location relationship', [('location identifier', f"md:{ids[f'place:{place}']}")], 'event_graph')
        return subject

    def weekly(weekdays, hour: float, minutes: int, start=date(2026, 9, 1), end=date(2026, 12, 18), when=lambda d: True):
        return [(clock(d, hour * 60), clock(d, hour * 60 + minutes)) for d in every_day(start, end)
                if d.weekday() in weekdays and when(d)]
    term = lambda d: on_trip(d) is None and not in_bristol(d)
    event('Daily standup', weekly(range(5), 9.5, 15, when=term), ['marcus', 'ana', 'tom'], 'office', organiser=True)
    event('Pipeline review', weekly([0], 11, 60, when=term), ['marcus', 'ana'], 'office', organiser=True)
    event('1:1 Marcus', weekly([2], 14, 30, when=term), ['marcus'], 'office', organiser=True)
    event('Gym', weekly([0, 2], 7, 60, when=term))
    event('Padel 🎾 Courtside', weekly([1, 3], 19.5, 90, when=padel_night), ['marcus', 'jonny', 'dev', 'will'], 'courtside')
    event('Waitrose delivery', weekly([5], 9, 60, when=term))
    event('Investor update — Northbeam', [(clock(d, 8.5 * 60), clock(d, 9 * 60)) for d in every_day(date(2026, 1, 1), date(2026, 12, 31))
                                          if d.weekday() == 3 and d.day <= 7], ['henrietta'], organiser=True)
    event('Board meeting', [(clock(date(2026, 10, 7), 600), clock(date(2026, 10, 7), 720))], ['henrietta', 'marcus'], 'office', organiser=True)
    event('Dentist', [(clock(date(2026, 10, 29), 495), clock(date(2026, 10, 29), 525))])
    event('Accountant — quarterly VAT', [(clock(date(2026, 11, 4), 960), clock(date(2026, 11, 4), 1020))])
    event('Sunday lunch at Mum & Dad’s', [(clock(date(2026, 11, 1), 750), clock(date(2026, 11, 1), 960))], ['sophie', 'mum', 'dad', 'hannah'], 'parents')
    event('Sophie’s birthday dinner 🎂', [(clock(date(2026, 11, 19), 1170), clock(date(2026, 11, 19), 1320))], ['sophie'])
    event('Wedding venue tasting — Hartwell Barn', [(clock(date(2026, 11, 21), 720), clock(date(2026, 11, 21), 960))], ['sophie'], 'hartwell')
    fridays = [d for d in every_day(date(2025, 1, 3), date(2026, 12, 18)) if bristol_friday(d)]
    event('🎾 Padel tournament (away) — Bristol', [(clock(d, 17 * 60), clock(d + timedelta(days=2), 20 * 60)) for d in fridays], place='bristol')
    event('Padel training camp — Bristol', [(clock(CAMP[0], 0), clock(CAMP[1], 23 * 60 + 59))], place='bristol', all_day=True)
    event('MOT — Volvo', [(clock(date(2026, 11, 7), 540), clock(date(2026, 11, 7), 600))], place='clifton')
    event('Parents’ evening (LA)', [(clock(date(2026, 11, 26), 1050), clock(date(2026, 11, 26), 1110))], place='clifton')
    # Deleted or edited long ago: only the expired tables remember.
    renamed = apple(clock(CAST['padel']['modified'], 22 * 60 + 41))
    for predicate, value in (('name', 'Katie 💛'), ('first name', 'Katie'), ('family name', 'Rowe')):
        g.fact(ids['padel'], predicate, value, 'expired_stable_graph', when=renamed)
    scan = g.subject()
    g.fact(scan, 'is a', C['calendar event'], 'expired_event_graph', when=apple(clock(date(2025, 7, 15), 600)))
    g.fact(scan, 'name', '20 week scan 🤍 (K)', 'expired_event_graph', when=apple(clock(date(2025, 7, 15), 600)))
    g.fact(scan, 'description', 'Southmead Hospital, Bristol', 'expired_event_graph', when=apple(clock(date(2025, 7, 15), 600)))

    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / 'graph.db')) as db:
        for table in ('stable_graph', 'event_graph', 'expired_stable_graph', 'expired_event_graph'):
            db.execute(f'CREATE TABLE {table} {GRAPH_SCHEMA}')
            db.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?)', g.rows[table])
        db.commit()
    with closing(sqlite3.connect(folder / 'ontology.db')) as db:
        db.executescript('''CREATE TABLE predicate (id TEXT PRIMARY KEY, label TEXT);
            CREATE TABLE class (id TEXT PRIMARY KEY, label TEXT);
            CREATE TABLE predicate_domain (predicateId TEXT, domain TEXT);
            CREATE TABLE predicate_range (predicateId TEXT, range TEXT, dataType TEXT);
            CREATE TABLE inheritance (child TEXT, parent TEXT);''')
        db.executemany('INSERT INTO predicate VALUES (?,?)', [(code, label) for label, code in P.items()])
        db.executemany('INSERT INTO class VALUES (?,?)', CLASSES.items())
        db.executemany('INSERT INTO predicate_domain VALUES (?,?)', [(P['first name'], C['person']), (P['bundle id'], C['software']),
                                                                    (P['has attendee'], C['calendar event'])])
        db.executemany('INSERT INTO predicate_range VALUES (?,?,?)', [(P['published by'], C['organization'], 'class'),
                                                                     (P['works for'], C['organization'], 'class'),
                                                                     (P['first name'], 'string', 'primitive')])
        db.commit()
    return dict(facts=sum(len(rows) for rows in g.rows.values()), calendar_events=sum(1 for r in g.rows['event_graph']
                                                                                      if r[1] == P['is a'] and not r[2]))


# Calls, messages and websites, shared by the sources that record them -------------------------
def london_evening(day: date) -> bool:
    return in_london(day) and not bristol_friday(day)


def plan_calls() -> list[dict]:
    """Every call from May onwards: who, when, how long, which app and which way."""
    rng = random.Random(f'{SEED}-calls')
    calls = []

    def call(day, minute, who, seconds, app='com.apple.InCallService', incoming=False):
        calls.append(dict(when=clock(day, minute), who=who, seconds=float(seconds), app=app, incoming=incoming))
    padel = [(handle(phone('padel')), 'Padel Club Bookings')]
    for day in every_day(BIOME_FROM):
        if london_evening(day):
            if rng.random() < .8:   # A short FaceTime around bedtime...
                call(day, 18 * 60 + 32 + rng.randint(0, 14), padel, rng.randint(240, 660), 'com.apple.facetime', rng.random() < .2)
            if rng.random() < .75:  # ...and a long one once Sophie has gone to bed.
                call(day, 21 * 60 + 40 + rng.randint(0, 25), padel, rng.randint(1080, 3300), incoming=rng.random() < .15)
        if day.weekday() == 6:
            call(day, 18 * 60 + rng.randint(0, 20), [(handle(phone('mum')), 'Mum')], rng.randint(900, 1800), incoming=rng.random() < .3)
        if in_bristol(day):
            for _ in range(rng.randint(1, 3)):
                call(day, rng.randint(10 * 60, 21 * 60), [(handle(phone('sophie')), 'Sophie Lang')],
                     0 if rng.random() < .4 else rng.randint(60, 240), incoming=True)
        elif rng.random() < .15:
            call(day, rng.randint(12 * 60, 18 * 60), [(handle(phone('sophie')), 'Sophie Lang')], rng.randint(60, 300), incoming=rng.random() < .5)
        if day.weekday() < 5 and in_london(day) and rng.random() < .25:
            call(day, rng.choice((8 * 60 + rng.randint(0, 40), 18 * 60 + rng.randint(0, 30))),
                 [(handle(phone('marcus')), 'Marcus Feld')], rng.randint(240, 1200), incoming=rng.random() < .5)
        if day.day in (3, 17):
            call(day, 16 * 60 + 30, [(handle(phone('henrietta')), 'Henrietta Cole')], rng.randint(900, 1500), incoming=day.day == 17)
    for day, minute, seconds in ((date(2026, 6, 9), 612, 138), (date(2026, 9, 22), 700, 241), (date(2026, 10, 14), 545, 92)):
        call(day, minute, [(handle(NURSERY_PHONE), '')], seconds, incoming=True)
    return sorted(calls, key=lambda c: c['when'])


GROUPS = {
    'Padel Boys 🎾': ['marcus', 'jonny', 'dev', 'will'],
    'Dunmore fam': ['mum', 'dad', 'hannah'],
    'Pebbles Room parents 🍎': ['padel', *PARENTS],
}


def member(entry) -> tuple[str, str]:
    if isinstance(entry, tuple):
        return handle(entry[0]), entry[1]
    person = CAST[entry]
    return handle(phone(entry)), ' '.join(p for p in (person.get('first'), person.get('last')) if p)


def plan_messages() -> list[dict]:
    """Every message from May onwards, without text: Siri.Remembers keeps only who, when and which app."""
    rng = random.Random(f'{SEED}-messages')
    messages = []

    def send(day, count, who, app, incoming, group=None, start=8 * 60, end=22 * 60 + 45):
        for _ in range(count):
            people = [member(who)] if group is None else [member(m) for m in GROUPS[group]]
            if group and incoming:
                people = [member(rng.choice(GROUPS[group]))]
            messages.append(dict(when=clock(day, rng.uniform(start, end)), who=people, app=app, incoming=incoming, group=group))
    imessage, whatsapp = 'com.apple.MobileSMS', 'net.whatsapp.WhatsApp'
    for day in every_day(BIOME_FROM):
        weekday = day.weekday()
        if in_bristol(day):        # Sophie keeps writing; Oliver barely answers.
            send(day, rng.randint(14, 30), 'sophie', imessage, True)
            send(day, rng.randint(2, 5), 'sophie', imessage, False)
        elif bristol_friday(day):
            send(day, rng.randint(4, 8), 'sophie', imessage, True, start=7 * 60 + 30)
            send(day, rng.randint(3, 6), 'sophie', imessage, False, start=7 * 60 + 30, end=17 * 60 + 30)
            send(day, rng.randint(5, 10), 'sophie', imessage, True, start=17 * 60 + 30)
            send(day, rng.randint(1, 2), 'sophie', imessage, False, start=17 * 60 + 30)
        else:
            send(day, rng.randint(4, 10), 'sophie', imessage, True)
            send(day, rng.randint(4, 9), 'sophie', imessage, False)
        if weekday < 5 and not in_bristol(day):
            send(day, rng.randint(1, 5), 'marcus', whatsapp, True)
            send(day, rng.randint(1, 5), 'marcus', whatsapp, False)
        if on_trip(day) == 'Lisbon' or padel_night(day) or padel_night(day + timedelta(days=1)):
            send(day, rng.randint(6, 16), 'marcus', whatsapp, True, 'Padel Boys 🎾')
            send(day, rng.randint(1, 4), 'marcus', whatsapp, False, 'Padel Boys 🎾')
        elif rng.random() < .3:
            send(day, rng.randint(1, 5), 'marcus', whatsapp, True, 'Padel Boys 🎾')
        if rng.random() < .6:
            send(day, rng.randint(1, 3), 'mum', whatsapp, True)
            send(day, rng.randint(0, 1), 'mum', whatsapp, False)
        if rng.random() < .25:
            send(day, rng.randint(2, 6), 'mum', whatsapp, True, 'Dunmore fam')
            send(day, rng.randint(0, 2), 'mum', whatsapp, False, 'Dunmore fam')
        if rng.random() < .2:
            send(day, rng.randint(1, 3), 'hannah', imessage, True)
            send(day, rng.randint(1, 3), 'hannah', imessage, False)
        if weekday == 3 and rng.random() < .5:
            send(day, 1, 'henrietta', imessage, True, start=9 * 60, end=18 * 60)
            send(day, rng.randint(1, 2), 'henrietta', imessage, False, start=9 * 60, end=18 * 60)
        if rng.random() < (.55 if weekday < 5 else .3):
            send(day, rng.randint(2, 8) if weekday < 5 else rng.randint(1, 4), 'padel', whatsapp, True, 'Pebbles Room parents 🍎',
                 start=7 * 60, end=21 * 60)
        if in_bristol(day) and rng.random() < .5:
            send(day, rng.randint(1, 2), 'padel', whatsapp, False, 'Pebbles Room parents 🍎', start=9 * 60, end=19 * 60)
    return sorted(messages, key=lambda m: m['when'])


# Websites: (path, title) per domain. Safari history and knowledgeC web usage both draw from these.
SITES = {
    'news.ycombinator.com': [('/', 'Hacker News'), ('/item?id=4{n}', 'Show HN: An agent that files your expenses'), ('/news?p=2', 'Hacker News')],
    'www.linkedin.com': [('/feed/', 'Feed | LinkedIn'), ('/in/oliverdunmore/', 'Oliver Dunmore | LinkedIn'), ('/notifications/', 'Notifications | LinkedIn')],
    'www.ft.com': [('/technology', 'Technology | Financial Times'), ('/content/{n}', 'UK AI start-ups struggle to raise Series A')],
    'www.bbc.co.uk': [('/news', 'Home - BBC News'), ('/sport/football', 'Football - BBC Sport'), ('/weather/2654675', 'Bristol - BBC Weather')],
    'github.com': [('/tallyhand/reconciler', 'tallyhand/reconciler'), ('/tallyhand/reconciler/pulls', 'Pull requests')],
    'huggingface.co': [('/models', 'Models - Hugging Face'), ('/blog', 'Hugging Face - Blog')],
    'arxiv.org': [('/abs/2609.0{n}', 'Agents for Document Reconciliation: A Benchmark')],
    'tallyhand.ai': [('/', 'Tallyhand — AI agents that reconcile your invoices'), ('/pricing', 'Pricing — Tallyhand')],
    'playtomic.io': [('/courtside-padel-bethnal-green', 'Courtside Padel Bethnal Green | Playtomic'), ('/matches', 'Open matches | Playtomic')],
    'www.waitrose.com': [('/ecom/shop/trolley', 'Trolley | Waitrose & Partners')],
    'app.famly.co': [('/#/account/feed', 'Famly'), ('/#/account/childProfile/{n}/observations', 'Famly')],
    'www.thetrainline.com': [('/book/results?origin=London%20Paddington&destination=Bristol%20Temple%20Meads', 'London Paddington to Bristol Temple Meads | Trainline'),
                             ('/my-account/bookings', 'My bookings | Trainline')],
    'www.hitched.co.uk': [('/wedding-venues/oxfordshire/', 'Wedding Venues in Oxfordshire | hitched.co.uk'), ('/wedding-planning/checklist/', 'Wedding checklist')],
    'www.johnlewis.com': [('/gift-list/{n}', 'Sophie & Oliver | Gift List | John Lewis')],
    'www.rightmove.co.uk': [('/property-for-sale/Clifton.html', 'Properties For Sale in Clifton | Rightmove'), ('/properties/{n}', '3 bedroom terraced house for sale in Clifton, Bristol, BS8')],
    'www.bristol.gov.uk': [('/residents/schools-learning-and-early-years/school-admissions', 'School admissions | Bristol City Council'),
                           ('/residents/schools-learning-and-early-years/primary-school-admissions', 'Apply for a primary school place | Bristol City Council')],
    'www.harcourtlowe.co.uk': [('/family-law/parental-responsibility', 'Parental responsibility | Harcourt & Lowe Family Law'),
                               ('/family-law/child-arrangements', 'Child arrangements orders | Harcourt & Lowe Family Law'), ('/contact', 'Contact us')],
    'www.mumsnet.com': [('/talk/relationships', 'Relationships | Mumsnet'), ('/talk/am_i_being_unreasonable', 'AIBU | Mumsnet')],
    'www.nhs.uk': [('/conditions/baby/', 'Your baby - NHS'), ('/conditions/hand-foot-mouth-disease/', 'Hand, foot and mouth disease - NHS')],
    'www.metoffice.gov.uk': [('/weather/forecast/gcnhtnumz', 'Bristol weather - Met Office')],
}


def sites(day: date, evening: bool) -> list[tuple[str, int]]:
    """Weighted domains for a stretch of browsing on this day."""
    if in_bristol(day):
        return [('www.nhs.uk', 3), ('www.bbc.co.uk', 3), ('www.metoffice.gov.uk', 2), ('app.famly.co', 2)]
    if not evening:
        weighted = [('news.ycombinator.com', 8), ('www.linkedin.com', 7), ('www.ft.com', 4), ('www.bbc.co.uk', 3), ('github.com', 2),
                    ('huggingface.co', 2), ('arxiv.org', 1), ('tallyhand.ai', 2)]
        if day.weekday() < 4:
            weighted.append(('playtomic.io', 2))
        return weighted
    weighted = [('www.bbc.co.uk', 4), ('www.linkedin.com', 2), ('www.mumsnet.com', 2), ('www.waitrose.com', 1)]
    for since, domain, weight in ((date(2026, 2, 16), 'www.hitched.co.uk', 4), (date(2026, 6, 1), 'www.johnlewis.com', 2),
                                  (date(2026, 8, 10), 'www.rightmove.co.uk', 4), (date(2026, 9, 3), 'www.harcourtlowe.co.uk', 3),
                                  (date(2026, 10, 28), 'www.bristol.gov.uk', 8)):
        if day >= since:
            weighted.append((domain, weight))
    return weighted


def pick(rng: random.Random, weighted):
    return rng.choices([item for item, _ in weighted], [weight for _, weight in weighted])[0]


def page(rng: random.Random, domain: str) -> tuple[str, str]:
    path, title = rng.choice(SITES[domain])
    return f"https://{domain}{path.replace('{n}', str(rng.randint(1000, 9999)))}", title


# Contacts --------------------------------------------------------------------------------------
LABELS = {'mobile': '_$!<Mobile>!$_', 'work': '_$!<Work>!$_', 'home': '_$!<Home>!$_', 'iPhone': 'iPhone'}
CONTACTS_SCHEMA = '''
    CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZUNIQUEID TEXT, ZFIRSTNAME TEXT, ZLASTNAME TEXT,
        ZMIDDLENAME TEXT, ZNICKNAME TEXT, ZORGANIZATION TEXT, ZJOBTITLE TEXT, ZDEPARTMENT TEXT, ZBIRTHDAY REAL,
        ZCONTAINERWHERECONTACTISME INTEGER, ZCREATIONDATE REAL, ZMODIFICATIONDATE REAL);
    CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT, ZLABEL TEXT, ZORDERINGINDEX INTEGER);
    CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT, ZLABEL TEXT, ZORDERINGINDEX INTEGER);
    CREATE TABLE ZABCDPOSTALADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZSTREET TEXT, ZCITY TEXT, ZZIPCODE TEXT,
        ZCOUNTRYNAME TEXT, ZLABEL TEXT);'''


def local_number(number: str) -> str:
    """How people type UK numbers into Contacts: '07700 900123', not '+44 7700 900123'."""
    return '0' + number.removeprefix('+44 ') if number.startswith('+44 ') else number


def write_contacts(path: Path, ids: dict) -> dict:
    """An empty main AddressBook plus the iCloud account's book, as macOS keeps them."""
    rng = random.Random(f'{SEED}-contacts')
    book = path.parent / 'Sources' / new_uuid(rng) / path.name
    for target in (path, book):
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target)) as db:
            db.executescript(CONTACTS_SCHEMA)
    created = apple(clock(date(2019, 6, 1), 600))
    with closing(sqlite3.connect(book)) as db:
        for pk, (key, person) in enumerate(CAST.items(), start=1):
            unique = ids.get(f'cn:{key}') or f'{new_uuid(rng)}:ABPerson'
            born = person.get('birthday')
            birthday = (datetime(born.year, born.month, born.day, 12, tzinfo=LONDON) - APPLE_EPOCH).total_seconds() if born else None
            modified = apple(clock(person['modified'], 22 * 60 + 41)) if person.get('modified') else created + pk * 86400
            db.execute('INSERT INTO ZABCDRECORD VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pk, 22, unique, person.get('first'), person.get('last'), None, None, person.get('org'), person.get('job'), None,
                        birthday, 1 if key == 'me' else None, created + pk * 3600, modified))
            for index, (label, number) in enumerate(person.get('phones', [])):
                db.execute('INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER, ZLABEL, ZORDERINGINDEX) VALUES (?,?,?,?)',
                           (pk, local_number(number), LABELS[label], index))
            for index, (label, address) in enumerate(person.get('emails', [])):
                db.execute('INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZADDRESS, ZLABEL, ZORDERINGINDEX) VALUES (?,?,?,?)',
                           (pk, address, LABELS[label], index))
            if person.get('address'):
                label, street, city, postcode, country = person['address']
                db.execute('INSERT INTO ZABCDPOSTALADDRESS (ZOWNER, ZSTREET, ZCITY, ZZIPCODE, ZCOUNTRYNAME, ZLABEL) VALUES (?,?,?,?,?,?)',
                           (pk, street, city, postcode, country, LABELS[label]))
        db.commit()
    return dict(contacts=len(CAST))


# knowledgeC.db ---------------------------------------------------------------------------------
KNOWLEDGE_SCHEMA = '''
    CREATE TABLE ZOBJECT (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZSTREAMNAME TEXT, ZVALUESTRING TEXT,
        ZVALUEINTEGER INTEGER, ZVALUEDOUBLE REAL, ZSTARTDATE REAL, ZENDDATE REAL, ZSECONDSFROMGMT INTEGER, ZCREATIONDATE REAL,
        ZSOURCE INTEGER, ZSTRUCTUREDMETADATA INTEGER, ZUUID TEXT);
    CREATE TABLE ZSTRUCTUREDMETADATA (Z_PK INTEGER PRIMARY KEY, Z_DKDIGITALHEALTHMETADATAKEY__WEBDOMAIN TEXT,
        Z_DKDIGITALHEALTHMETADATAKEY__WEBPAGEURL TEXT, Z_DKBLUETOOTHMETADATAKEY__NAME TEXT, Z_DKBLUETOOTHMETADATAKEY__ADDRESS TEXT,
        Z_DKBLUETOOTHMETADATAKEY__DEVICETYPE INTEGER, Z_DKBLUETOOTHMETADATAKEY__PRODUCTID INTEGER,
        Z_DKINTENTMETADATAKEY__INTENTCLASS TEXT, Z_DKINTENTMETADATAKEY__INTENTVERB TEXT, Z_DKINTENTMETADATAKEY__DIRECTION INTEGER,
        Z_DKINTENTMETADATAKEY__DONATEDBYSIRI INTEGER, Z_DKNOTIFICATIONUSAGEMETADATAKEY__BUNDLEID TEXT);
    CREATE TABLE ZSOURCE (Z_PK INTEGER PRIMARY KEY, ZBUNDLEID TEXT, ZDEVICEID TEXT, ZSOURCEID TEXT);
    CREATE TABLE ZSYNCPEER (Z_PK INTEGER PRIMARY KEY, ZDEVICEID TEXT, ZMODEL TEXT, ZNAME TEXT);'''
WORK_APPS = [('Slack', 26), ('Safari', 18), ('Notion', 12), ('Mail', 9), ('zoom.us', 9), ('Linear', 7), ('Keynote', 5), ('WhatsApp', 4),
             ('Calendar', 3), ('Messages', 3), ('Spotify', 2), ('Ghostty', 2), ('Visual Studio Code', 2)]
EVENING_APPS = [('Safari', 40), ('Messages', 10), ('WhatsApp', 10), ('Notion', 8), ('Mail', 10), ('Keynote', 8)]
BLUETOOTH = {'mouse': ('MX Master 3S', 'D4:2F:1A:77:0B:12', 2, 45108), 'airpods': ('Oliver’s AirPods Pro', 'AC:90:85:1F:3E:21', 1, 8212),
             'car': ('Volvo XC90', '00:1E:AE:4C:91:5D', 3, 0)}


def mac_blocks(day: date, rng: random.Random) -> list[tuple[int, int]]:
    """Stretches of Mac use in minutes after midnight. The Mac never leaves London."""
    if not in_london(day):
        return []
    weekday = day.weekday()
    if weekday < 5:
        end = 16 * 60 + 25 if bristol_friday(day) else 18 * 60 + 50 if padel_night(day) else 18 * 60 + 15 + rng.randint(0, 50)
        blocks = [(8 * 60 + 40 + rng.randint(0, 25), 12 * 60 + 25 + rng.randint(0, 15)), (13 * 60 + 10 + rng.randint(0, 15), end)]
        if weekday in (0, 2):
            blocks.append((20 * 60 + 20, 21 * 60 + 30 + rng.randint(0, 6)))
        return blocks
    if weekday == 5:
        return [(10 * 60 + rng.randint(0, 30), 11 * 60 + rng.randint(0, 40))]
    return [(19 * 60 + rng.randint(0, 20), 21 * 60 + 20 + rng.randint(0, 20))]


def write_knowledgec(path: Path, calls: list[dict], messages: list[dict]) -> dict:
    rng = random.Random(f'{SEED}-knowledge')
    objects, metadata, sources = [], [], {}

    def source(bundle: str | None, device: str | None = None) -> int | None:
        if bundle is None:
            return None
        return sources.setdefault((bundle, device), len(sources) + 1)

    def meta(**columns) -> int:
        metadata.append(columns)
        return len(metadata)

    def emit(stream, start, end, value=None, value_int=None, bundle=None, device=None, md=None):
        objects.append((stream, value, value_int, apple(start), apple(end), gmt_offset(start), source(bundle, device), md, new_uuid(rng)))

    def bluetooth(device, day, start, end, phone_side=False):
        name, address, kind, product = BLUETOOTH[device]
        emit('/bluetooth/isConnected', clock(day, start), clock(day, end), value_int=1, device=IPHONE if phone_side else None,
             bundle='com.apple.bluetooth' if phone_side else None, md=meta(Z_DKBLUETOOTHMETADATAKEY__NAME=name,
             Z_DKBLUETOOTHMETADATAKEY__ADDRESS=address, Z_DKBLUETOOTHMETADATAKEY__DEVICETYPE=kind, Z_DKBLUETOOTHMETADATAKEY__PRODUCTID=product))

    def notify(bundle, day, minute):
        emit('/notification/usage', clock(day, minute), clock(day, minute), value='Receive',
             md=meta(Z_DKNOTIFICATIONUSAGEMETADATAKEY__BUNDLEID=bundle))

    for day in every_day(RECENT_FROM):
        blocks = mac_blocks(day, rng)
        for index, (start, end) in enumerate(blocks):
            evening = start >= 19 * 60 or day.weekday() >= 5
            emit('/display/isBacklit', clock(day, start), clock(day, end), value_int=1)
            following = blocks[index + 1][0] if index + 1 < len(blocks) else end + 45
            emit('/display/isBacklit', clock(day, end), clock(day, following), value_int=0)
            if not evening:
                bluetooth('mouse', day, start, end)
            minute = start
            while minute < end - 2:
                name = pick(rng, EVENING_APPS if evening else WORK_APPS)
                length = min(rng.randint(2, 24), end - minute)
                emit('/app/usage', clock(day, minute), clock(day, minute + length), value=BUNDLE[name])
                if name == 'Safari':
                    cursor = minute
                    while cursor < minute + length - 1:
                        stint = min(rng.randint(1, 8), minute + length - cursor)
                        domain = pick(rng, sites(day, evening))
                        emit('/app/webUsage', clock(day, cursor), clock(day, cursor + stint), value=BUNDLE['Safari'],
                             md=meta(Z_DKDIGITALHEALTHMETADATAKEY__WEBDOMAIN=domain,
                                     Z_DKDIGITALHEALTHMETADATAKEY__WEBPAGEURL=page(rng, domain)[0]))
                        cursor += stint
                elif name == 'zoom.us':
                    bluetooth('airpods', day, minute, minute + length)
                minute += length + rng.choice((0, 0, 1, 2))
        # Notifications reach the Mac through iPhone Mirroring whether or not anyone opens the app.
        if day.weekday() < 5:
            for _ in range(rng.randint(15, 35)):
                notify(BUNDLE['Slack'], day, rng.randint(8 * 60 + 30, 19 * 60))
            for minute in (12 * 60 + 40, 15 * 60 + 10, 17 * 60 + 20):
                notify(BUNDLE['Famly'], day, minute + rng.randint(0, 9))
        for _ in range(rng.randint(1, 4)):
            notify(BUNDLE['LinkedIn'], day, rng.randint(7 * 60, 22 * 60))
        for _ in range(rng.randint(0, 3)):
            notify(BUNDLE['Monzo'], day, rng.randint(8 * 60, 21 * 60))
        if padel_night(day):
            notify(BUNDLE['Playtomic'], day, 9 * 60)
            notify(BUNDLE['Playtomic'], day, 18 * 60 + 30)
        if day.weekday() < 5 and in_london(day):
            emit('/app/intents', clock(day, 8 * 60 + 12), clock(day, 8 * 60 + 12), value=BUNDLE['Spotify'], bundle=BUNDLE['Spotify'],
                 device=IPHONE, md=meta(Z_DKINTENTMETADATAKEY__INTENTCLASS='INPlayMediaIntent', Z_DKINTENTMETADATAKEY__INTENTVERB='PlayMedia',
                                        Z_DKINTENTMETADATAKEY__DIRECTION=1, Z_DKINTENTMETADATAKEY__DONATEDBYSIRI=0))
        # The car only ever connects on Bristol weekends: pick-up at Temple Meads, errands, drop-off.
        if bristol_friday(day):
            bluetooth('car', day, 19 * 60 + 18, 19 * 60 + 44, phone_side=True)
        elif in_bristol(day) and day.weekday() == 5:
            bluetooth('car', day, 10 * 60 + 5, 10 * 60 + 36, phone_side=True)
            bluetooth('car', day, 15 * 60 + 10, 15 * 60 + 31, phone_side=True)
        elif in_bristol(day) and day.weekday() == 6:
            bluetooth('car', day, 16 * 60 + 52, 17 * 60 + 18, phone_side=True)
    recent = clock(RECENT_FROM, 0)
    for c in calls:
        if c['when'] >= recent:
            emit('/app/intents', c['when'], c['when'] + timedelta(seconds=c['seconds']), value=c['app'], bundle=c['app'], device=IPHONE,
                 md=meta(Z_DKINTENTMETADATAKEY__INTENTCLASS='INStartCallIntent', Z_DKINTENTMETADATAKEY__INTENTVERB='StartCall',
                         Z_DKINTENTMETADATAKEY__DIRECTION=2 if c['incoming'] else 1, Z_DKINTENTMETADATAKEY__DONATEDBYSIRI=0))
    for m in messages:
        if m['when'] >= recent and not m['incoming']:
            emit('/app/intents', m['when'], m['when'], value=m['app'], bundle=m['app'], device=IPHONE,
                 md=meta(Z_DKINTENTMETADATAKEY__INTENTCLASS='INSendMessageIntent', Z_DKINTENTMETADATAKEY__INTENTVERB='SendMessage',
                         Z_DKINTENTMETADATAKEY__DIRECTION=1, Z_DKINTENTMETADATAKEY__DONATEDBYSIRI=0))
    objects.sort(key=lambda row: row[3])
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(KNOWLEDGE_SCHEMA)
        db.execute('INSERT INTO ZSYNCPEER VALUES (1, ?, ?, ?)', (IPHONE, IPHONE_MODEL, 'Oliver’s iPhone'))
        db.executemany('INSERT INTO ZSOURCE VALUES (?,?,?,?)', [(pk, bundle, device, bundle) for (bundle, device), pk in sources.items()])
        columns = [r[1] for r in db.execute('PRAGMA table_info(ZSTRUCTUREDMETADATA)')][1:]
        db.executemany(f"INSERT INTO ZSTRUCTUREDMETADATA VALUES ({', '.join('?' * (len(columns) + 1))})",
                       [(pk, *(row.get(column) for column in columns)) for pk, row in enumerate(metadata, start=1)])
        db.executemany('INSERT INTO ZOBJECT VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       [(pk, 11, 1, stream, value, value_int, None, start, end, offset, end, src, md, uid)
                        for pk, (stream, value, value_int, start, end, offset, src, md, uid) in enumerate(objects, start=1)])
        db.commit()
    return dict(events=len(objects), streams=dict(Counter(row[0] for row in objects)))


# Biome -----------------------------------------------------------------------------------------
def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field(number: int, value) -> bytes:
    """One protobuf field: ints as varints, floats as doubles, str/bytes length-delimited."""
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, float):
        return varint(number << 3 | 1) + struct.pack('<d', value)
    if isinstance(value, str):
        value = value.encode()
    return varint(number << 3 | 2) + varint(len(value)) + value


def write_segb(path: Path, records: list[tuple[float, bytes]]) -> None:
    """SEGB v2: 32-byte header, 8-byte record headers, 4-byte alignment, and a footer index read from the end."""
    body, index = bytearray(), bytearray()
    for stamp, payload in records:
        body += struct.pack('<II', 0, 0) + payload
        index = struct.pack('<IId', len(body), 1, stamp) + index
        while len(body) % 4:
            body += b'\0'
    created = records[0][0] if records else 0.0
    header = struct.pack('<4sIdIIII', b'SEGB', len(records), created, 10, 0xFFFFFFFF, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(body) + b'\0' * 64 + bytes(index))


def write_stream(folder: Path, records: list[tuple[float, bytes]]) -> None:
    """One SEGB file per month, named after its first record, as biomed rotates them."""
    by_month = defaultdict(list)
    for stamp, payload in sorted(records, key=lambda r: r[0]):
        by_month[(APPLE_EPOCH + timedelta(seconds=stamp)).strftime('%Y-%m')].append((stamp, payload))
    for month, chunk in by_month.items():
        bookkeeping = (chunk[-1][0], field(1, str(int(chunk[-1][0] * 1000))) + field(5, 'biomesyncd'))
        write_segb(folder / str(int(chunk[0][0] * 1e6)), [*chunk, bookkeeping])


def interaction(rng, intent: str, app: str, moment: datetime, direction: int, roles: list[tuple[str, list]],
                seconds: float = 0.0, group: str | None = None) -> bytes:
    """A Siri.Remembers record: interaction info in field 1, then one field-2 group per participant role."""
    info = (field(1, new_uuid(rng)) + field(2, intent) + field(4, app) + field(5, 1) + field(6, direction)
            + field(8, moment.timestamp()) + field(9, float(seconds)) + field(11, json.dumps({'isGroup': group is not None})))
    payload = field(1, info)
    for role, people in roles:
        entries = b''.join(field(2, field(1, number) + field(2, 'PersonHandle') + field(4, json.dumps({'name': name}, ensure_ascii=False)))
                           for number, name in people)
        payload += field(2, field(1, role) + entries)
    if group:
        payload += field(2, field(1, 'speakableGroupName') + field(2, field(1, group) + field(2, 'MessageGroup')))
    return payload


KIDS_SONGS = [('Baby Shark', 'Pinkfong', 'Pinkfong Animal Songs', 81), ('Wheels on the Bus', 'CoComelon', 'CoComelon Kids Hits, Vol. 1', 160),
              ('Hey Duggee Theme Tune', 'Hey Duggee', 'Hey Duggee: The Album', 52), ('Let It Go', 'Idina Menzel', 'Frozen (Original Motion Picture Soundtrack)', 224),
              ('Twinkle Twinkle Little Star', 'Super Simple Songs', 'Super Simple Songs, Vol. 1', 118)]
WORK_SONGS = [('Yellow', 'Coldplay', 'Parachutes', 269), ('Fix You', 'Coldplay', 'X&Y', 295),
              ('Viva la Vida', 'Coldplay', 'Viva la Vida or Death and All His Friends', 242), ('Shape of You', 'Ed Sheeran', '÷', 234),
              ('Perfect', 'Ed Sheeran', '÷', 263), ('Time', 'Hans Zimmer', 'Inception (Music from the Motion Picture)', 275),
              ('Cornfield Chase', 'Hans Zimmer', 'Interstellar (Original Motion Picture Soundtrack)', 126),
              ('Mr. Brightside', 'The Killers', 'Hot Fuss', 222), ('Chasing Cars', 'Snow Patrol', 'Eyes Open', 267),
              ('Clocks', 'Coldplay', 'A Rush of Blood to the Head', 307)]
PODCASTS = [('Ep. 212: Pricing your AI agent per seat or per outcome?', 'The Seed Stage', 'The Seed Stage', 2890),
            ('Ep. 213: What Series A investors want in 2026', 'The Seed Stage', 'The Seed Stage', 3120),
            ('Bandeja vs víbora: when to use which', 'Padel Tactics Weekly', 'Padel Tactics Weekly', 1810),
            ('Ep. 214: The boring business of accounts payable', 'The Seed Stage', 'The Seed Stage', 2655)]


def now_playing(moment: datetime, song: tuple, app: str) -> tuple[float, bytes]:
    title, artist, album, seconds = song
    stamp = apple(moment)
    return stamp, (field(2, stamp) + field(3, 1) + field(4, album) + field(5, artist) + field(6, seconds) + field(8, title) + field(15, app))


def focus(moment: datetime, bundle: str, starting: bool) -> tuple[float, bytes]:
    stamp = apple(moment)
    return stamp, field(2, 1) + field(3, int(starting)) + field(4, stamp) + field(6, bundle)


def write_biome(root: Path, calls: list[dict], messages: list[dict]) -> dict:
    rng = random.Random(f'{SEED}-biome')
    remote = Path('remote') / IPHONE
    records = []
    for m in messages:
        role = 'senderHandle' if m['incoming'] else 'recipientHandles'
        records.append((apple(m['when']), interaction(rng, 'INSendMessageIntent', m['app'], m['when'], 1 if m['incoming'] else 2,
                                                      [(role, m['who'])], group=m['group'])))
    write_stream(root / 'Siri.Remembers.MessageHistory' / remote, records)
    records = [(apple(c['when']), interaction(rng, 'INStartCallIntent', c['app'], c['when'], 1 if c['incoming'] else 2,
                                              [('contactHandles', c['who'])], seconds=c['seconds'])) for c in calls]
    write_stream(root / 'Siri.Remembers.CallHistory' / remote, records)

    local, phone_side = [], []
    for day in every_day(MEDIA_FROM):
        if day.weekday() < 5 and in_london(day):
            episode = rng.choice(PODCASTS)
            for minute in range(8 * 60 + 15, 8 * 60 + 45, 5):
                phone_side.append(now_playing(clock(day, minute), episode, BUNDLE['Spotify']))
            for start in (10 * 60 + rng.randint(0, 60), 14 * 60 + rng.randint(0, 90)):
                minute = start
                while minute < start + rng.randint(45, 110):
                    song = rng.choice(WORK_SONGS)
                    local.append(now_playing(clock(day, minute), song, BUNDLE['Spotify']))
                    minute += song[3] / 60
        if in_bristol(day) and not CAMP[0] <= day <= CAMP[1]:
            for start, end in ((7 * 60 + 40, 8 * 60 + 50), (16 * 60 + 30, 17 * 60 + 20)):
                minute = start + rng.randint(0, 10)
                while minute < end:
                    song = rng.choice(KIDS_SONGS[:1] * 3 + KIDS_SONGS)   # Baby Shark, again.
                    phone_side.append(now_playing(clock(day, minute), song, 'com.apple.Music'))
                    minute += song[3] / 60 + rng.uniform(0, 1.5)
    write_stream(root / 'Media.NowPlaying' / 'local', local)
    write_stream(root / 'Media.NowPlaying' / remote, phone_side)

    sessions = []

    def use(day, minute, length, name):
        bundle = BUNDLE.get(name, name)
        sessions.append(focus(clock(day, minute), bundle, True))
        sessions.append(focus(clock(day, minute + length), bundle, False))
    for day in every_day(RECENT_FROM):
        awake = (7 * 60 + 10, 22 * 60 + 50)
        for name, low, high, longest in (('WhatsApp', 6, 15, 6), ('Messages', 5, 12, 4), ('Safari', 2, 6, 8)):
            for _ in range(rng.randint(low, high)):
                use(day, rng.randint(*awake), rng.uniform(.5, longest), name)
        if day.weekday() < 5 and in_london(day):
            use(day, 8 * 60 + 8, 3, 'Citymapper')
            use(day, 18 * 60 + 5 + rng.randint(0, 20), 2, 'Citymapper')
            for _ in range(5):
                use(day, rng.randint(9 * 60, 18 * 60), rng.uniform(1, 3), 'Slack')
            for _ in range(3):
                use(day, rng.randint(7 * 60, 22 * 60), rng.uniform(1, 4), 'LinkedIn')
        if day.weekday() < 5:
            use(day, 12 * 60 + 45, 2, 'Famly')
            use(day, rng.randint(8 * 60, 21 * 60), 1.5, 'Monzo')
            use(day, 17 * 60 + 25, 3.5, 'Famly')
        if padel_night(day):
            use(day, 18 * 60 + 35, 2, 'Playtomic')
            use(day, 21 * 60 + 5, 3, 'Playtomic')
        if bristol_friday(day):
            use(day, 17 * 60 + 8, 4, 'Trainline')
            use(day, 19 * 60 + 9, 2, 'Find My')
        if in_bristol(day):
            use(day, 7 * 60 + 30, 38 + rng.randint(0, 10), 'CBeebies Go Explore')
            use(day, 10 * 60 + 3, 5, 'Maps')
            for _ in range(5):
                use(day, rng.randint(9 * 60, 19 * 60), rng.uniform(1, 5), 'Photos')
            use(day, 13 * 60 + rng.randint(0, 60), 4, 'Famly')
            if day.weekday() == 6:
                use(day, 16 * 60 + 40, 3, 'Trainline')
    recent = clock(RECENT_FROM, 0)
    for c in calls:
        if c['when'] >= recent and c['app'] == BUNDLE['FaceTime']:
            sessions.append(focus(c['when'], BUNDLE['FaceTime'], True))
            sessions.append(focus(c['when'] + timedelta(seconds=c['seconds']), BUNDLE['FaceTime'], False))
    write_stream(root / 'App.InFocus' / remote, sessions)
    write_stream(root / 'App.InFocus' / 'local', [focus(clock(LAST_DAY, 9 * 60), BUNDLE['Slack'], True)])
    return dict(messages=len(messages), calls=len(calls), now_playing=len(local) + len(phone_side), focus_records=len(sessions))


# Safari ----------------------------------------------------------------------------------------
SAFARI_SCHEMA = '''
    CREATE TABLE history_items (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE, domain_expansion TEXT NULL,
        visit_count INTEGER NOT NULL, should_recompute_derived_visit_counts INTEGER NOT NULL DEFAULT 0,
        visit_count_score INTEGER NOT NULL DEFAULT 0, status_code INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE history_visits (id INTEGER PRIMARY KEY AUTOINCREMENT, history_item INTEGER NOT NULL REFERENCES history_items(id),
        visit_time REAL NOT NULL, title TEXT NULL, load_successful BOOLEAN NOT NULL DEFAULT 1, http_non_get BOOLEAN NOT NULL DEFAULT 0,
        synthesized BOOLEAN NOT NULL DEFAULT 0, origin INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0,
        attributes INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0);'''


def write_safari(path: Path) -> dict:
    """A year of history. Visits from the iPhone sync in with origin 1."""
    rng = random.Random(f'{SEED}-safari')
    visits = []

    def browse(day, count, start, end, weighted, origin=0):
        for _ in range(count):
            url, title = page(rng, pick(rng, weighted))
            visits.append((apple(clock(day, rng.uniform(start, end))), url, title, origin))
    for day in every_day(SAFARI_FROM):
        weekday = day.weekday()
        if in_bristol(day):
            browse(day, rng.randint(1, 5), 8 * 60, 21 * 60, sites(day, False), origin=1)
        elif on_trip(day):
            browse(day, rng.randint(0, 3), 9 * 60, 22 * 60, [('www.bbc.co.uk', 1), ('www.metoffice.gov.uk', 1)], origin=1)
        else:
            if weekday < 5:
                browse(day, rng.randint(8, 20), 9 * 60, 18 * 60 + 30, sites(day, False))
                browse(day, rng.randint(1, 2), 12 * 60 + 40, 12 * 60 + 55, [('app.famly.co', 1)])
            browse(day, rng.randint(0, 9), 20 * 60, 23 * 60 + 40, sites(day, True))
            if day >= date(2026, 10, 28):   # Primary school applications close on 15 January.
                browse(day, rng.randint(2, 6), 21 * 60, 23 * 60 + 30, [('www.bristol.gov.uk', 1)])
            if bristol_friday(day + timedelta(days=2)):
                browse(day, rng.randint(2, 4), 21 * 60, 22 * 60, [('www.thetrainline.com', 1)])
    visits.sort()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SAFARI_SCHEMA)
        items = {}
        for _, url, _, _ in visits:
            items.setdefault(url, len(items) + 1)
        counts = Counter(url for _, url, _, _ in visits)
        db.executemany('INSERT INTO history_items (id, url, domain_expansion, visit_count, visit_count_score) VALUES (?,?,?,?,?)',
                       [(pk, url, url.split('/')[2].removeprefix('www.').split('.')[0], counts[url], counts[url] * 100)
                        for url, pk in items.items()])
        db.executemany('INSERT INTO history_visits (history_item, visit_time, title, origin) VALUES (?,?,?,?)',
                       [(items[url], stamp, title, origin) for stamp, url, title, origin in visits])
        db.commit()
    return dict(visits=len(visits), pages=len(items))


# Mail ------------------------------------------------------------------------------------------
WORK, HOME = 'oliver@tallyhand.ai', 'ollie.dunmore@icloud.com'
CORRESPONDENTS = {
    WORK: 'Oliver Dunmore', HOME: 'Oliver Dunmore', 'marcus@tallyhand.ai': 'Marcus Feld', 'ana@tallyhand.ai': 'Ana Sousa',
    'henrietta@northbeam.vc': 'Henrietta Cole', 'raj@mehta.capital': 'Raj Mehta', 'clara@vossangel.co': 'Clara Voss',
    'ben@okoro.vc': 'Ben Okoro', 'fenella.hart@gmail.com': 'Fenella Hart', 'sam@whitlockfamilyoffice.com': 'Sam Whitlock',
    'priyanka@lowtide.vc': 'Priyanka Rao', 'portfolio@northbeam.vc': 'Northbeam Ventures',
    'dan.pryce@brightmere-logistics.co.uk': 'Dan Pryce', 'ap@haldenfoods.co.uk': 'Accounts Payable — Halden Foods',
    'hello@seedroundup.news': 'Seed Round-Up', 'no-reply@playtomic.io': 'Playtomic', 'bookings@courtsidepadel.co.uk': 'Courtside Padel',
    'auto-confirm@info.thetrainline.com': 'Trainline', 'office@littleacorns-clifton.co.uk': 'Little Acorns Nursery',
    'katie.rowe.bs8@gmail.com': 'Katie Rowe', 'soph.lang@icloud.com': 'Sophie Lang', 'events@hartwellbarn.co.uk': 'Hartwell Barn Weddings',
    'e.harcourt@harcourtlowe.co.uk': 'Eleanor Harcourt', 'noreply@wise.com': 'Wise', 'carol.dunmore@btinternet.com': 'Carol Dunmore',
    'deals@racketmegastore.biz': 'Racket Mega Store',
}
INVESTORS = ['henrietta@northbeam.vc', 'raj@mehta.capital', 'clara@vossangel.co', 'ben@okoro.vc', 'fenella.hart@gmail.com',
             'sam@whitlockfamilyoffice.com', 'priyanka@lowtide.vc', 'portfolio@northbeam.vc']
W, H = 'imap://oliver%40tallyhand.ai@imap.gmail.com', 'imap://ollie.dunmore@imap.mail.me.com'
MAILBOXES = [f'{W}/INBOX', f'{W}/%5BGmail%5D/Sent%20Mail', f'{W}/%5BGmail%5D/Spam', f'{H}/INBOX', f'{H}/Sent%20Messages', f'{H}/Junk']


def write_mail(path: Path) -> dict:
    """The Envelope Index: who wrote to whom and when. Subjects live here too, though the importer ignores them."""
    rng = random.Random(f'{SEED}-mail')
    messages = []
    threads = {}

    def mail(moment, sender, to, cc=(), bcc=(), subject='', junk=False):
        mine = sender in (WORK, HOME)
        personal = sender == HOME or HOME in (*to, *cc)
        box = (5 if personal else 2) if mine else (6 if junk else 4 if personal else 1)
        thread = threads.setdefault(subject.removeprefix('Re: ').removeprefix('Fwd: '), len(threads) + 1)
        messages.append(dict(when=moment, sender=sender, to=list(to), cc=list(cc), bcc=list(bcc), subject=subject, mailbox=box,
                             thread=thread))

    def at_work_hours(day):
        return clock(day, rng.randint(8 * 60 + 30, 18 * 60 + 30))
    for day in every_day(MAIL_FROM):
        weekday, month = day.weekday(), day.strftime('%B %Y')
        if weekday == 3 and day.day <= 7:
            subject = f'Tallyhand — {month} investor update'
            mail(clock(day, 8 * 60 + 40), WORK, [WORK], bcc=INVESTORS, subject=subject)
            if rng.random() < .5:
                mail(clock(day + timedelta(days=1), 10 * 60 + 5), 'henrietta@northbeam.vc', [WORK], subject=f'Re: {subject}')
                if rng.random() < .5:
                    mail(clock(day + timedelta(days=1), 15 * 60 + 30), WORK, ['henrietta@northbeam.vc'], subject=f'Re: {subject}')
        if weekday < 5 and in_london(day) and rng.random() < .3:
            subject = rng.choice(['Halden Foods pilot — next steps', 'SOC 2 timeline', 'Hiring: second ML engineer',
                                  'Match rate on the Brightmere invoices', 'Q4 board deck'])
            pair = [('marcus@tallyhand.ai', WORK), (WORK, 'marcus@tallyhand.ai')][rng.random() < .5]
            mail(at_work_hours(day), pair[0], [pair[1]], cc=['ana@tallyhand.ai'] if rng.random() < .3 else [], subject=subject)
            if rng.random() < .6:
                mail(at_work_hours(day), pair[1], [pair[0]], subject=f'Re: {subject}')
        if weekday < 5 and rng.random() < .06:
            mail(at_work_hours(day), 'dan.pryce@brightmere-logistics.co.uk', [WORK], subject='Unmatched invoices this month')
            mail(at_work_hours(day), WORK, ['dan.pryce@brightmere-logistics.co.uk'], cc=['marcus@tallyhand.ai'], subject='Re: Unmatched invoices this month')
        if day.day == 5:
            mail(clock(day, 9 * 60 + 15), 'ap@haldenfoods.co.uk', [WORK], subject=f'Remittance advice — {month}')
        if weekday == 0:
            mail(clock(day, 7 * 60), 'hello@seedroundup.news', [WORK], subject='Seed Round-Up: this week in European AI')
        if padel_night(day):
            mail(clock(day, 9 * 60 + 2), 'no-reply@playtomic.io', [HOME], subject='Your match at Courtside Padel Bethnal Green is confirmed')
        if day.day == 10:
            mail(clock(day, 11 * 60), 'bookings@courtsidepadel.co.uk', [HOME], subject='Courtside Winter League — sign up now')
        if bristol_friday(day + timedelta(days=2)):
            mail(clock(day, 21 * 60 + 14), 'auto-confirm@info.thetrainline.com', [HOME],
                 subject='Your e-tickets: London Paddington → Bristol Temple Meads (return)')
        if day.day == 1:
            subject = f"Invoice {day.strftime('%B')} — Pebbles Room"
            mail(clock(day, 8 * 60 + 30), 'office@littleacorns-clifton.co.uk', [HOME], cc=['katie.rowe.bs8@gmail.com'], subject=subject)
            if rng.random() < .3:
                mail(clock(day, 21 * 60 + 50), HOME, ['office@littleacorns-clifton.co.uk'], cc=['katie.rowe.bs8@gmail.com'], subject=f'Re: {subject}')
            mail(clock(day, 9 * 60 + 2), 'noreply@wise.com', [HOME], subject='You sent £1,450.00 to Katie Rowe')
        if day in (date(2025, 4, 22), date(2025, 9, 3), date(2026, 1, 6), date(2026, 4, 21), date(2026, 9, 2)):
            mail(clock(day, 16 * 60), 'office@littleacorns-clifton.co.uk', [HOME, 'katie.rowe.bs8@gmail.com'], subject='Welcome back! Term dates and news')
        if rng.random() < .07:
            subject = rng.choice(['Nursery forms', 'Half term?', 'Christmas', 'Car insurance renewal', 'Pip’s swimming',
                                  'Photos from Saturday'] + (['School applications — need your details', 'Pip’s school application (deadline 15 Jan)']
                                                             if day >= date(2026, 9, 1) else []))
            mail(clock(day, rng.randint(20 * 60, 22 * 60)), 'katie.rowe.bs8@gmail.com', [HOME], subject=subject)
            if rng.random() < .7:
                mail(clock(day, 21 * 60 + 55), HOME, ['katie.rowe.bs8@gmail.com'], subject=f'Re: {subject}')
        if day >= date(2026, 2, 20) and rng.random() < .08:
            subject = rng.choice(['Venues!!', 'Hartwell Barn — they have 12 June 2027!', 'Guest list v4', 'Wedding budget', 'Gift list'])
            mail(clock(day, rng.randint(12 * 60, 22 * 60)), 'soph.lang@icloud.com', [HOME], subject=subject)
            if rng.random() < .5:
                mail(clock(day, 22 * 60 + 30), HOME, ['soph.lang@icloud.com'], subject=f'Re: {subject}')
        if day >= date(2026, 3, 2) and day.day in (2, 16) and rng.random() < .7:
            subject = rng.choice(['Your date: 12 June 2027', 'Menu options', 'Your tasting on Saturday 21 November'])
            mail(clock(day, 10 * 60), 'events@hartwellbarn.co.uk', [HOME], cc=['soph.lang@icloud.com'], subject=subject)
            mail(clock(day, 19 * 60), HOME, ['events@hartwellbarn.co.uk'], cc=['soph.lang@icloud.com'], subject=f'Re: {subject}')
        if rng.random() < .03:
            mail(clock(day, 10 * 60), 'carol.dunmore@btinternet.com', [HOME], subject=rng.choice(['Christmas plans', 'Photos from Sunday', 'Dad’s 70th']))
        if rng.random() < .05:
            mail(clock(day, 3 * 60), 'deals@racketmegastore.biz', [HOME], subject='Congratulations! Claim your free padel racket', junk=True)
    for day, sender, to, subject in (
            (date(2026, 9, 3), HOME, 'e.harcourt@harcourtlowe.co.uk', 'Enquiry: parental responsibility (unmarried father)'),
            (date(2026, 9, 4), 'e.harcourt@harcourtlowe.co.uk', HOME, 'Re: Enquiry: parental responsibility (unmarried father)'),
            (date(2026, 9, 15), HOME, 'e.harcourt@harcourtlowe.co.uk', 'Re: Enquiry: parental responsibility (unmarried father)'),
            (date(2026, 9, 29), 'e.harcourt@harcourtlowe.co.uk', HOME, 'Confidential: your options before the wedding'),
            (date(2026, 10, 20), 'e.harcourt@harcourtlowe.co.uk', HOME, 'Draft parental responsibility agreement'),
            (date(2026, 10, 21), HOME, 'katie.rowe.bs8@gmail.com', 'Fwd: Draft parental responsibility agreement'),
            (date(2026, 10, 22), 'katie.rowe.bs8@gmail.com', HOME, 'Re: Fwd: Draft parental responsibility agreement'),
            (date(2026, 11, 9), HOME, 'e.harcourt@harcourtlowe.co.uk', 'Re: Draft parental responsibility agreement'),
            (date(2026, 11, 11), 'e.harcourt@harcourtlowe.co.uk', HOME, 'Re: Draft parental responsibility agreement')):
        mail(clock(day, 22 * 60 + 5 if sender == HOME else 11 * 60), sender, [to], subject=subject)
    messages = [m for m in messages if m['when'] <= clock(LAST_DAY, 23 * 60 + 59)]
    messages.sort(key=lambda m: m['when'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
            CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
            CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
            CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY, subject TEXT);
            CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, message_id INTEGER, sender INTEGER, subject INTEGER, date_sent INTEGER,
                date_received INTEGER, mailbox INTEGER, conversation_id INTEGER, read INTEGER, flagged INTEGER, deleted INTEGER, size INTEGER);
            CREATE TABLE recipients (ROWID INTEGER PRIMARY KEY, message INTEGER, address INTEGER, type INTEGER, position INTEGER);''')
        address_ids = {address: pk for pk, address in enumerate(CORRESPONDENTS, start=1)}
        db.executemany('INSERT INTO addresses VALUES (?,?,?)', [(pk, a, CORRESPONDENTS[a]) for a, pk in address_ids.items()])
        db.executemany('INSERT INTO mailboxes VALUES (?,?)', list(enumerate(MAILBOXES, start=1)))
        subject_ids = {}
        for m in messages:
            subject_ids.setdefault(m['subject'], len(subject_ids) + 1)
        db.executemany('INSERT INTO subjects VALUES (?,?)', [(pk, s) for s, pk in subject_ids.items()])
        for pk, m in enumerate(messages, start=1):
            sent = int(m['when'].timestamp())
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pk, rng.getrandbits(62), address_ids[m['sender']], subject_ids[m['subject']], sent, sent + rng.randint(1, 40),
                        m['mailbox'], 9_000_000 + m['thread'], 1, 0, 0, rng.randint(4000, 90000)))
            position = 0
            for kind, people in ((0, m['to']), (1, m['cc']), (2, m['bcc'])):
                for address in people:
                    db.execute('INSERT INTO recipients (message, address, type, position) VALUES (?,?,?,?)', (pk, address_ids[address], kind, position))
                    position += 1
        db.commit()
    return dict(messages=len(messages), correspondents=len(CORRESPONDENTS))


# Photos ----------------------------------------------------------------------------------------
PLACES = {
    'Shoreditch, London': (51.5263, -0.0786), 'Hackney, London': (51.5450, -0.0553), 'Bethnal Green, London': (51.5270, -0.0549),
    'Old Street, London': (51.5256, -0.0875), 'Primrose Hill, London': (51.5394, -0.1609), 'Guildford, Surrey': (51.2362, -0.5704),
    'Chipping Norton': (51.9416, -1.5466), 'Palma, Mallorca': (39.5696, 2.6502), 'Lisbon': (38.7223, -9.1393),
    'Clifton, Bristol': (51.4560, -2.6200), 'Southmead, Bristol': (51.4957, -2.5915), 'Weston-super-Mare': (51.3461, -2.9770),
}
PADEL_BOYS = {'marcus': .4, 'jonny': .4, 'dev': .35, 'will': .35, 'me': .3}
BRISTOL_KIDS_BORN = date(2025, 12, 9)


def photo_outings(rng: random.Random):
    """(day, place, photos, chance each person is in a photo, first and last minute, UTC offset) for every day with photos."""
    for day in every_day(PHOTOS_FROM):
        london = gmt_offset(clock(day, 720))
        trip = on_trip(day)
        family = {'pip': .7, 'katie': .3, 'me': .25, 'baby': .45 if day >= BRISTOL_KIDS_BORN else 0}
        if trip:
            people = {'sophie': .6, 'me': .35} if trip == 'Palma, Mallorca' else PADEL_BOYS
            yield day, trip, rng.randint(22, 36) if trip == 'Palma, Mallorca' else rng.randint(12, 24), people, (9 * 60, 21 * 60), TRIPS[trip][2]
        elif day in (date(2025, 12, 9), date(2025, 12, 10), date(2025, 12, 11)):
            yield day, 'Southmead, Bristol', {9: 64, 10: 31, 11: 18}[day.day], {'baby': .85, 'katie': .45, 'me': .35, 'pip': .3}, (6 * 60, 21 * 60), london
        elif day == date(2026, 7, 18):
            yield day, 'Weston-super-Mare', 61, {**family, 'katie': .4}, (10 * 60, 17 * 60), london
        elif in_bristol(day):
            yield day, 'Clifton, Bristol', rng.randint(6, 20), family, (8 * 60 + 30, 18 * 60), london
        elif bristol_friday(day):
            yield day, 'Clifton, Bristol', rng.randint(0, 3), family, (19 * 60 + 50, 20 * 60 + 30), london
        elif day == date(2026, 2, 14):
            yield day, 'Primrose Hill, London', 38, {'sophie': .8, 'me': .6}, (15 * 60, 18 * 60), london
        elif day == date(2026, 9, 12):
            yield day, 'Chipping Norton', 26, {'sophie': .7, 'me': .4}, (12 * 60, 17 * 60), london
        elif padel_night(day) and rng.random() < .3:
            yield day, 'Bethnal Green, London', rng.randint(1, 4), PADEL_BOYS, (19 * 60 + 30, 21 * 60), london
        elif day.weekday() == 6 and day.day <= 7:
            yield day, 'Guildford, Surrey', rng.randint(8, 15), {'mum': .5, 'dad': .4, 'hannah': .35, 'sophie': .3, 'me': .3}, (12 * 60, 17 * 60), london
        elif day.weekday() >= 5 and rng.random() < .6:
            people = {'sophie': .55, 'me': .25, 'sophies_friend': .12}
            yield day, rng.choice(['Hackney, London', 'Shoreditch, London']), rng.randint(2, 9), people, (11 * 60, 19 * 60), london
        elif day.weekday() < 5 and rng.random() < .35:
            yield day, rng.choice(['Shoreditch, London', 'Old Street, London']), rng.randint(1, 3), {'sophie': .4, 'me': .15}, (8 * 60, 22 * 60), london


def write_photos(path: Path, ids: dict) -> dict:
    rng = random.Random(f'{SEED}-photos')
    named = {key: CAST[key]['photos'] for key in CAST if CAST[key].get('photos')}
    people = [*named, *UNNAMED, *(f'stranger{n}' for n in range(40))]   # Passers-by: too few faces to count.
    person_pk = {key: pk for pk, key in enumerate(people, start=1)}
    assets, attributes, faces, moments = [], [], [], {}
    for day, place, count, chances, (first, last), offset in photo_outings(rng):
        if not count:
            continue
        lat, lon = PLACES[place]
        moment = moments.setdefault((place, day), len(moments) + 1)
        for minute in sorted(rng.uniform(first, last) for _ in range(count)):
            pk = len(assets) + 1
            moment_time = clock(day, minute)
            assets.append((pk, new_uuid(rng), apple(moment_time), int(rng.random() < .08), moment, int(rng.random() < .01),
                           f'IMG_{pk:04d}.HEIC', None, lat + rng.uniform(-.004, .004), lon + rng.uniform(-.004, .004), int(rng.random() < .03)))
            attributes.append((pk, pk, offset, f'GMT{offset // 3600:+03d}00', f'IMG_{pk:04d}.HEIC'))
            present = [key for key, chance in chances.items() if rng.random() < chance]
            if place.endswith('London') and rng.random() < .03:
                present.append(f'stranger{rng.randrange(40)}')
            for key in present:
                faces.append((len(faces) + 1, person_pk[key], pk, round(rng.uniform(.05, .4), 3), round(rng.uniform(.3, .95), 3),
                              round(rng.random(), 3), round(rng.random(), 3)))
    assets = [(*a[:7], a[1][0], *a[8:]) for a in assets]   # Originals live in a folder named after the asset's first UUID character.
    face_count = Counter(face[1] for face in faces)
    key_face = {}
    for face in faces:
        key_face.setdefault(face[1], face[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
            CREATE TABLE ZPERSON (Z_PK INTEGER PRIMARY KEY, ZPERSONUUID TEXT, ZMDID TEXT, ZFULLNAME TEXT, ZDISPLAYNAME TEXT,
                ZFACECOUNT INTEGER, ZVERIFIEDTYPE INTEGER, ZKEYFACE INTEGER, ZTYPE INTEGER);
            CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZUUID TEXT, ZDATECREATED REAL, ZKIND INTEGER, ZMOMENT INTEGER,
                ZTRASHEDSTATE INTEGER, ZFILENAME TEXT, ZDIRECTORY TEXT, ZLATITUDE REAL, ZLONGITUDE REAL, ZFAVORITE INTEGER);
            CREATE TABLE ZADDITIONALASSETATTRIBUTES (Z_PK INTEGER PRIMARY KEY, ZASSET INTEGER, ZTIMEZONEOFFSET INTEGER,
                ZTIMEZONENAME TEXT, ZORIGINALFILENAME TEXT);
            CREATE TABLE ZDETECTEDFACE (Z_PK INTEGER PRIMARY KEY, ZPERSONFORFACE INTEGER, ZASSETFORFACE INTEGER, ZSIZE REAL,
                ZQUALITY REAL, ZCENTERX REAL, ZCENTERY REAL);
            CREATE TABLE ZMOMENT (Z_PK INTEGER PRIMARY KEY, ZTITLE TEXT, ZSUBTITLE TEXT, ZSTARTDATE REAL, ZENDDATE REAL,
                ZAPPROXIMATELATITUDE REAL, ZAPPROXIMATELONGITUDE REAL);''')
        for key, pk in person_pk.items():
            uuid = ids.get(f'ph:{key}') or new_uuid(rng)
            name = named.get(key)
            db.execute('INSERT INTO ZPERSON VALUES (?,?,?,?,?,?,?,?,?)',
                       (pk, uuid, f'md:{ids[key]}' if f'ph:{key}' in ids else None, name, (name or '').split(' ')[0] or None,
                        face_count[pk], 1 if name else 0, key_face.get(pk), 0))
        db.executemany('INSERT INTO ZASSET VALUES (?,?,?,?,?,?,?,?,?,?,?)', assets)
        db.executemany('INSERT INTO ZADDITIONALASSETATTRIBUTES VALUES (?,?,?,?,?)', attributes)
        db.executemany('INSERT INTO ZDETECTEDFACE VALUES (?,?,?,?,?,?,?)', faces)
        span = defaultdict(list)
        for asset in assets:
            span[asset[4]].append(asset[2])
        db.executemany('INSERT INTO ZMOMENT VALUES (?,?,?,?,?,?,?)',
                       [(pk, place, day.strftime('%-d %B %Y'), min(span[pk]), max(span[pk]), *PLACES[place])
                        for (place, day), pk in moments.items()])
        db.commit()
    return dict(assets=len(assets), faces=len(faces), people=len(person_pk))


# Putting it together ---------------------------------------------------------------------------
def build(home: Path) -> dict:
    """Write Oliver's Mac into ``home``, laid out like a home folder, and return what was written."""
    home = Path(home)
    if home.exists() and any(home.iterdir()):
        if not (home / MARKER).is_file():
            raise ValueError(f'{home} is not empty and was not written by "apple-graph sample"; choose another folder.')
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / MARKER).write_text('Written by "apple-graph sample": a fictional Mac. Safe to delete.\n')
    ids: dict = {}
    calls, messages = plan_calls(), plan_messages()
    stats = dict(knowledge_graph=write_knowledge_graph(home / HOME_LAYOUT['source'], ids),
                 contacts=write_contacts(home / HOME_LAYOUT['contacts'], ids),
                 knowledge=write_knowledgec(home / HOME_LAYOUT['knowledge'], calls, messages),
                 biome=write_biome(home / HOME_LAYOUT['biome'], calls, messages),
                 safari=write_safari(home / HOME_LAYOUT['safari']),
                 mail=write_mail(home / HOME_LAYOUT['mail']),
                 photos=write_photos(home / HOME_LAYOUT['photos'], ids))
    return stats
