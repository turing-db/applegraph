"""Photos library: who appears in photos, where and when."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

from .model import Builder, apple_time, is_placeholder, local_day, snapshot


def add_photos(builder: Builder, path: Path, min_faces: int = 5, min_shared: int = 2) -> dict:
    with tempfile.TemporaryDirectory(prefix='apple-graph-photos-') as tmp:
        copy = Path(tmp) / 'Photos.sqlite'
        snapshot(path, copy)
        with closing(sqlite3.connect(copy)) as db:
            db.row_factory = sqlite3.Row
            persons = [dict(r) for r in db.execute('SELECT Z_PK, ZPERSONUUID, ZFULLNAME, ZDISPLAYNAME, ZFACECOUNT, ZVERIFIEDTYPE FROM ZPERSON')]
            attr_columns = {r[1] for r in db.execute('PRAGMA table_info(ZADDITIONALASSETATTRIBUTES)')}
            offset_column = 'aa.ZTIMEZONEOFFSET' if 'ZTIMEZONEOFFSET' in attr_columns else 'NULL'
            assets = db.execute(f'''SELECT a.Z_PK, a.ZDATECREATED, a.ZKIND, a.ZMOMENT, {offset_column} AS offset
                                   FROM ZASSET a LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON aa.ZASSET = a.Z_PK
                                   WHERE a.ZTRASHEDSTATE = 0 AND a.ZDATECREATED IS NOT NULL''').fetchall()
            faces = db.execute('SELECT ZPERSONFORFACE, ZASSETFORFACE FROM ZDETECTEDFACE WHERE ZPERSONFORFACE IS NOT NULL AND ZASSETFORFACE IS NOT NULL').fetchall()
            moments = {r[0]: (r[1], r[2], r[3]) for r in db.execute("SELECT Z_PK, ZTITLE, ZAPPROXIMATELATITUDE, ZAPPROXIMATELONGITUDE FROM ZMOMENT WHERE ZTITLE IS NOT NULL AND ZTITLE <> ''")}

    person_nodes = {}
    matched = created = 0
    for person in persons:
        uuid = person['ZPERSONUUID'] or ''
        key = builder.identifier_index.get(('phperson', uuid))
        named = bool((person['ZFULLNAME'] or '').strip())
        if key is None and not named and (person['ZFACECOUNT'] or 0) < min_faces:
            continue
        if key is None:
            key = ('contact', 'Photos', uuid or str(person['Z_PK']))
            builder.node(key, ['AppleEntity', 'Person'], name=(person['ZFULLNAME'] or '').strip() or f"Face cluster {person['Z_PK']}",
                         apple_id=f'Photos:{uuid}', entity_type='person', source='Photos')
            created += 1
        else:
            matched += 1
        props = builder.props(key)
        if named and is_placeholder(props.get('name')):
            props['name'] = person['ZFULLNAME'].strip()
        props['photos_faces'] = int(person['ZFACECOUNT'] or 0)
        props['photos_person_uuid'] = uuid
        props['photos_verified'] = person['ZVERIFIEDTYPE'] == 1
        person_nodes[person['Z_PK']] = builder.nodes[key]

    asset_day, asset_moment = {}, {}
    day_counts = defaultdict(lambda: dict(photos=0, videos=0))
    place_days = defaultdict(int)
    for asset in assets:
        day = local_day(asset['ZDATECREATED'], asset['offset'] or 0)
        if day is None:
            continue
        asset_day[asset['Z_PK']] = day
        day_counts[day]['videos' if asset['ZKIND'] == 1 else 'photos'] += 1
        if asset['ZMOMENT'] in moments:
            place_days[moments[asset['ZMOMENT']][0], day] += 1
    for day, counts in day_counts.items():
        builder.props(builder.day(day)).update(photos_taken=counts['photos'], videos_taken=counts['videos'])

    person_days = defaultdict(set)
    asset_people = defaultdict(set)
    for person_pk, asset_pk in faces:
        if person_pk in person_nodes and asset_pk in asset_day:
            person_days[person_nodes[person_pk], asset_day[asset_pk]].add(asset_pk)
            asset_people[asset_pk].add(person_nodes[person_pk])
    for (node, day), photos in sorted(person_days.items(), key=lambda item: (item[0][1], item[0][0])):
        builder.edge(node, builder.day(day), 'PHOTOGRAPHED_ON', dict(photos=len(photos), source='Photos'))
    pairs = defaultdict(lambda: dict(photos=0, first=None, last=None))
    for asset_pk, people in asset_people.items():
        day = asset_day[asset_pk]
        for a in people:
            for b in people:
                if a < b:
                    pair = pairs[a, b]
                    pair['photos'] += 1
                    pair['first'] = day if pair['first'] is None else min(pair['first'], day)
                    pair['last'] = day if pair['last'] is None else max(pair['last'], day)
    appears = 0
    for (a, b), pair in pairs.items():
        if pair['photos'] >= min_shared:
            builder.edge(a, b, 'APPEARS_WITH', dict(photos=pair['photos'], first=pair['first'], last=pair['last'], source='Photos'))
            appears += 1
    for (title, day), photos in sorted(place_days.items(), key=lambda item: (item[0][1], item[0][0])):
        place = builder.place(title, 'Photos')
        props = builder.props(place)
        moment = next((m for m in moments.values() if m[0] == title), None)
        if moment and moment[1] is not None and 'latitude' not in props and moment[1] != -180:
            props.update(latitude=str(moment[1]), longitude=str(moment[2]))
        builder.edge(builder.day(day), place, 'PHOTOGRAPHED_AT', dict(photos=photos, source='Photos'))
    return dict(assets=len(assets), days=len(day_counts), people_matched=matched, people_created=created,
                photographed_on_edges=len(person_days), appears_with_edges=appears, places=len({t for t, _ in place_days}),
                photographed_at_edges=len(place_days),
                first_asset=apple_time(min(a['ZDATECREATED'] for a in assets)) if assets else '')
