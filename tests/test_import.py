import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from apple_graph import export_graph, literal, node_label, property_key, edge_type

SCHEMA = '(subject INTEGER, predicate TEXT, relationshipId INTEGER, relationshipPredicate TEXT, object TEXT, sources INTEGER, confidence REAL, sourceDuplicates TEXT, timestamp REAL)'
PREDICATES = {'PS1': 'is a', 'PS33': 'name', 'PS40': 'published by', 'PS50': 'has attendee', 'PS51': 'attendee identifier',
              'PS52': 'participant obligation', 'PS60': 'identifier', 'PS61': 'identifier type', 'PS62': 'identifier id',
              'PS70': 'same as', 'PS80': 'has contact information', 'PS81': 'phone number', 'PS82': 'contact label',
              'PS90': 'virtual conference URL', 'PS41': 'bundle id', 'PS83': 'has date', 'PS84': 'start time'}
DAY = 788918400.0  # 2026-01-01T00:00:00Z in seconds since 2001.
CLASSES = {'SB104': 'person', 'SB200': 'software', 'SB300': 'calendar event', 'CS38': 'participation relationship type'}


def fact(subject, predicate, obj, relationship=0, relationship_predicate='', timestamp=0.0):
    return (subject, predicate, relationship, relationship_predicate, obj, 32, .75, 'source', timestamp)


class ImportTest(unittest.TestCase):
    def test_naming_helpers(self):
        self.assertEqual(node_label('participation relationship type'), 'Participation')
        self.assertEqual(node_label('nm_entityAliasRelationshipType'), 'EntityAlias')
        self.assertEqual(node_label('calendar event'), 'CalendarEvent')
        self.assertEqual(property_key('virtual conference URL'), 'virtual_conference_url')
        self.assertEqual(property_key('apple id'), 'fact_apple_id')
        self.assertEqual(edge_type('has latitude/longitude'), 'HAS_LATITUDE_LONGITUDE')
        self.assertEqual(literal('{"day":18,"hour":12,"isLeapMonth":false,"minute":45,"month":9,"timeZone":{"identifier":"Europe/London"},"year":2026}'),
                         '2026-09-18 12:45 Europe/London')
        self.assertEqual(literal('{"day":12,"isLeapMonth":false,"month":6}'), '{"day":12,"isLeapMonth":false,"month":6}')
        self.assertEqual(literal('{not json'), '{not json')

    def test_export_models_facts_as_properties_and_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = sqlite3.connect(root / 'graph.db')
            self.addCleanup(graph.close)
            graph.execute('PRAGMA journal_mode=WAL')
            for table in ('stable_graph', 'event_graph', 'expired_stable_graph'):
                graph.execute(f'CREATE TABLE {table} {SCHEMA}')
            stable = [
                fact(42, 'PS1', 'SB104'), fact(42, 'PS33', 'Alice'),
                fact(42, 'PS90', 'https://a.example'), fact(42, 'PS90', 'https://b.example'),
                fact(42, 'PS70', 'cnContact:ALICE-1'),  # Self reference via own identifier group.
                fact(42, 'PS60', 'CNContact', -7, 'PS61'), fact(42, 'PS60', 'ALICE-1', -7, 'PS62'),
                fact(42, 'PS80', '+15550100', -8, 'PS81'), fact(42, 'PS80', 'Mobile', -8, 'PS82'),
                fact(43, 'PS1', 'SB200'), fact(43, 'PS33', 'Calendar'), fact(43, 'PS41', 'com.example.calendar'),
                fact(44, 'PS1', 'SB104'), fact(44, 'PS33', 'Bob'),
                fact(44, 'PS60', 'CNContact', -9, 'PS61'), fact(44, 'PS60', 'BOB-1', -9, 'PS62'),
                fact(45, 'PS1', 'SB300'), fact(45, 'PS33', 'Lunch'), fact(45, 'PS40', 'md:43', timestamp=1.0),
                fact(45, 'PS50', 'CS38', 11, 'PS1'), fact(45, 'PS50', 'md:44', 11, 'PS51'), fact(45, 'PS50', 'required', 11, 'PS52'),
                fact(45, 'PS70', 'cnContact:BOB-1'),  # Cross-entity reference.
                fact(45, 'PS83', '{"day":1,"hour":9,"minute":30,"month":1,"year":2026,"timeZone":{"identifier":"UTC"}}', 12, 'PS84'),
                fact(46, 'PS33', 'Unknown thing'),
            ]
            graph.executemany('INSERT INTO stable_graph VALUES (?,?,?,?,?,?,?,?,?)', stable)
            graph.executemany('INSERT INTO expired_stable_graph VALUES (?,?,?,?,?,?,?,?,?)',
                              [fact(42, 'PS33', 'Old Alice'), fact(45, 'PS40', 'md:42')])
            graph.execute('CREATE TABLE extras (payload BLOB, nullable TEXT, large INTEGER)')
            graph.execute('INSERT INTO extras VALUES (?, NULL, ?)', (b'\x00\xff', 9223372036854775807))
            graph.commit()  # Connection stays open: committed data is still in WAL.
            with sqlite3.connect(root / 'ontology.db') as ont:
                ont.executescript('CREATE TABLE predicate(id TEXT,label TEXT); CREATE TABLE class(id TEXT,label TEXT);')
                ont.executemany('INSERT INTO predicate VALUES (?,?)', PREDICATES.items())
                ont.executemany('INSERT INTO class VALUES (?,?)', CLASSES.items())
            output = root / 'export.jsonl'
            stats = export_graph(root, output)
            records = [json.loads(line) for line in output.read_text().splitlines()]
            nodes = {r['id']: r for r in records if r['type'] == 'node'}
            edges = [r for r in records if r['type'] == 'relationship']
            by_id = {r['properties'].get('apple_id'): r for r in nodes.values() if 'AppleEntity' in r['labels']}

            self.assertEqual(stats['rows'], {'stable_graph': len(stable), 'expired_stable_graph': 2})
            self.assertEqual(stats['entities'], 5)
            self.assertTrue(all(r['properties'].get('name') for r in nodes.values()))
            self.assertEqual(len(nodes), stats['nodes'])
            self.assertFalse(any(r['labels'] == ['AppleFact'] for r in nodes.values()))

            alice = by_id['42']
            self.assertEqual(alice['labels'], ['AppleEntity', 'Person'])
            self.assertEqual(alice['properties']['name'], 'Alice')
            self.assertEqual(alice['properties']['entity_type'], 'person')
            self.assertEqual(alice['properties']['virtual_conference_url'], 'https://a.example | https://b.example')
            self.assertEqual(alice['properties']['phone_number'], '+15550100')
            self.assertEqual(alice['properties']['contact_label'], 'Mobile')
            self.assertEqual(alice['properties']['identifier_id'], 'ALICE-1')
            self.assertEqual(alice['properties']['same_as'], 'cnContact:ALICE-1')
            self.assertNotIn('is_a', alice['properties'])
            self.assertEqual(by_id['46']['labels'], ['AppleEntity'])
            self.assertEqual(by_id['46']['properties']['name'], 'Unknown thing')

            def edges_of(label):
                return [(nodes[e['start']['id']]['properties']['name'], nodes[e['end']['id']]['properties']['name'], e.get('properties', {}))
                        for e in edges if e['label'] == label]
            published = edges_of('PUBLISHED_BY')
            self.assertEqual(sorted((a, b, p['active']) for a, b, p in published), [('Lunch', 'Alice', False), ('Lunch', 'Calendar', True)])
            current = next(p for a, b, p in published if b == 'Calendar')
            self.assertEqual(current['timestamp_utc'], '2001-01-01T00:00:01+00:00')
            self.assertEqual(current['confidence'], .75)
            self.assertEqual(current['raw_object'], 'md:43')
            self.assertEqual(edges_of('SAME_AS')[0][:2], ('Lunch', 'Bob'))
            self.assertEqual(len(edges_of('SAME_AS')), 1)

            self.assertFalse(any('AppleRelationship' in r['labels'] for r in nodes.values()))
            attendee, = edges_of('HAS_ATTENDEE')
            self.assertEqual(attendee[:2], ('Lunch', 'Bob'))
            self.assertEqual(attendee[2]['participant_obligation'], 'required')
            self.assertEqual(attendee[2]['via'], 'attendee identifier')
            self.assertEqual(attendee[2]['relationship_id'], '11')
            self.assertEqual(attendee[2]['relationship_type'], 'participation relationship type')
            self.assertEqual(attendee[2]['raw_object'], 'md:44')
            self.assertEqual(attendee[2]['source_duplicates'], 'source')
            self.assertEqual(stats['relationship_edges'], 1)
            self.assertEqual(stats['flattened_groups'], 4)
            self.assertEqual(stats['knowledge'], {})

            archive = next(r['properties'] for r in nodes.values() if r['labels'] == ['AppleSQLiteRow'] and r['properties']['table_name'] == 'extras')
            raw = json.loads(archive['row_json'])
            self.assertEqual(raw['payload'], {'type': 'blob', 'value': 'AP8='})
            self.assertEqual(raw['nullable'], {'type': 'null', 'value': None})
            self.assertEqual(raw['large']['value'], '9223372036854775807')
            self.assertEqual(stats['tables']['graph.db/extras'], 1)
            self.assertEqual(stats['tables']['graph.db/expired_stable_graph'], 2)

            lean = export_graph(root, root / 'lean.jsonl', entities_only=True)
            lean_records = [json.loads(line) for line in (root / 'lean.jsonl').read_text().splitlines()]
            lean_labels = {label for r in lean_records if r['type'] == 'node' for label in r['labels']}
            self.assertEqual(lean_labels, {'AppleEntity', 'Person', 'Software', 'CalendarEvent'})
            self.assertEqual(lean['nodes'], 5)
            self.assertEqual(lean['sqlite_rows'], 0)
            self.assertEqual(lean['semantic_edges'], stats['semantic_edges'])

            knowledge = root / 'knowledgeC.db'
            with sqlite3.connect(knowledge) as k:
                k.executescript('''
                    CREATE TABLE ZOBJECT (Z_PK INTEGER PRIMARY KEY, ZSTREAMNAME TEXT, ZVALUESTRING TEXT, ZVALUEINTEGER INTEGER,
                        ZSTARTDATE REAL, ZENDDATE REAL, ZSECONDSFROMGMT INTEGER, ZSOURCE INTEGER, ZSTRUCTUREDMETADATA INTEGER, ZUUID TEXT);
                    CREATE TABLE ZSTRUCTUREDMETADATA (Z_PK INTEGER PRIMARY KEY, Z_DKDIGITALHEALTHMETADATAKEY__WEBDOMAIN TEXT,
                        Z_DKBLUETOOTHMETADATAKEY__NAME TEXT, Z_DKINTENTMETADATAKEY__INTENTVERB TEXT, Z_DKINTENTMETADATAKEY__DIRECTION INTEGER,
                        Z_DKNOTIFICATIONUSAGEMETADATAKEY__BUNDLEID TEXT);
                    CREATE TABLE ZSOURCE (Z_PK INTEGER PRIMARY KEY, ZBUNDLEID TEXT, ZDEVICEID TEXT);
                    CREATE TABLE ZSYNCPEER (ZDEVICEID TEXT, ZMODEL TEXT);
                    INSERT INTO ZSYNCPEER VALUES ('PHONE', 'iPhone14,3');
                    INSERT INTO ZSOURCE VALUES (1, 'com.apple.InCallService', 'PHONE');
                    INSERT INTO ZSTRUCTUREDMETADATA VALUES (1, 'github.com', NULL, NULL, NULL, NULL), (2, NULL, 'Buds', NULL, NULL, NULL),
                        (3, NULL, NULL, 'StartCall', 1, NULL), (4, NULL, NULL, NULL, NULL, 'com.example.calendar');
                ''')
                k.executemany('INSERT INTO ZOBJECT (ZSTREAMNAME, ZVALUESTRING, ZVALUEINTEGER, ZSTARTDATE, ZENDDATE, ZSECONDSFROMGMT, ZSOURCE, ZSTRUCTUREDMETADATA, ZUUID) VALUES (?,?,?,?,?,?,?,?,?)', [
                    ('/app/usage', 'com.example.calendar', None, DAY + 3600, DAY + 3900, 0, None, None, 'u1'),
                    ('/app/usage', 'com.example.calendar', None, DAY + 7200, DAY + 7260, 0, None, None, 'u2'),
                    ('/app/usage', 'com.example.calendar', None, DAY + 86400 + 60, DAY + 86400 + 120, 0, None, None, 'u3'),
                    ('/app/usage', 'com.other.editor', None, DAY + 100, DAY + 200, 0, None, None, 'u4'),
                    ('/app/usage', 'com.late.night', None, DAY + 86400 - 1800, DAY + 86400 - 600, -7200, None, None, 'u5'),  # Local day is still Jan 1
                    ('/app/usage', 'com.late.night', None, DAY + 86400 - 600, DAY + 86400 + 600, 0, None, None, 'u6'),  # Splits at midnight
                    ('/app/webUsage', 'com.example.calendar', None, DAY + 10, DAY + 70, 0, None, 1, 'w1'),
                    ('/bluetooth/isConnected', None, 1, DAY, DAY + 600, 0, None, 2, 'b1'),
                    ('/bluetooth/isConnected', None, 1, DAY + 300, DAY + 900, 0, None, 2, 'b2'),  # Overlap counts once
                    ('/display/isBacklit', None, 1, DAY, DAY + 500, 0, None, None, 'd1'),
                    ('/display/isBacklit', None, 0, DAY + 500, DAY + 900, 0, None, None, 'd2'),
                    ('/notification/usage', 'Receive', None, DAY + 5, DAY + 5, 0, None, 4, 'n1'),
                    ('/app/intents', 'Calls', None, DAY + 50, DAY + 50, 0, 1, 3, 'i1'),
                ])
            timeline = root / 'timeline.jsonl'
            tstats = export_graph(root, timeline, entities_only=True, knowledge=knowledge)
            trecords = [json.loads(line) for line in timeline.read_text().splitlines()]
            tnodes = {r['id']: r for r in trecords if r['type'] == 'node'}
            tedges = [r for r in trecords if r['type'] == 'relationship']
            def named(label):
                return [(tnodes[e['start']['id']]['properties']['name'], tnodes[e['end']['id']]['properties']['name'], e.get('properties', {}))
                        for e in tedges if e['label'] == label]
            self.assertEqual(tstats['knowledge']['events'], 13)
            self.assertEqual(tstats['knowledge']['apps_matched'], 1)
            self.assertEqual(tstats['knowledge']['apps_added'], 3)  # editor, night, InCallService
            used = sorted(named('USED_ON'))
            self.assertEqual([(a, b, p['seconds'], p['sessions']) for a, b, p in used],
                             [('Calendar', '2026-01-01', 360, 2), ('Calendar', '2026-01-02', 60, 1), ('editor', '2026-01-01', 100, 1),
                              ('night', '2026-01-01', 1800, 2), ('night', '2026-01-02', 600, 1)])
            self.assertEqual([p['share_of_day'] for a, b, p in used if b == '2026-01-01'], [round(360 / 2260, 4), round(100 / 2260, 4), round(1800 / 2260, 4)])
            self.assertEqual((used[0][2]['minutes'], used[0][2]['device'], used[0][2]['source']), (6.0, 'This Mac', 'knowledgeC'))
            self.assertTrue(any(r['labels'] == ['Month'] and r['properties']['month'] == '2026-01' for r in tnodes.values()))
            self.assertEqual(len([e for e in tedges if e['label'] == 'IN_MONTH']), 2)
            calendar = next(r for r in tnodes.values() if r['properties'].get('bundle_id') == 'com.example.calendar')
            self.assertEqual(calendar['labels'], ['AppleEntity', 'Software'])
            self.assertEqual(calendar['properties']['usage_seconds'], 420)
            self.assertEqual(named('BROWSED_ON'), [('github.com', '2026-01-01', {'first_start': '2026-01-01T00:00:10+00:00', 'last_end': '2026-01-01T00:01:10+00:00', 'seconds': 60, 'minutes': 1.0, 'sessions': 1, 'share_of_day': 1.0, 'source': 'knowledgeC'})])
            browsed, = named('BROWSED')
            self.assertEqual(browsed[:2], ('Calendar', 'github.com'))
            self.assertEqual((browsed[2]['days'], browsed[2]['visits'], browsed[2]['first_seen']), (1, 1, '2026-01-01T00:00:10+00:00'))
            self.assertEqual([(a, b, p['seconds'], p['sessions']) for a, b, p in named('CONNECTED_ON')], [('Buds', '2026-01-01', 900, 2)])
            self.assertEqual([(a, b, p['notifications'], 'seconds' in p) for a, b, p in named('NOTIFIED_ON')], [('Calendar', '2026-01-01', 1, False)])
            day = next(r for r in tnodes.values() if r['labels'] == ['Day'] and r['properties']['date'] == '2026-01-01')
            self.assertEqual(day['properties']['screen_on_seconds'], 500)
            self.assertEqual(day['properties']['weekday'], 'Thursday')
            self.assertFalse(any('Interaction' in r['labels'] or 'Device' in r['labels'] for r in tnodes.values()))
            call, = named('START_CALL')
            self.assertEqual(call[:2], ('InCallService', '2026-01-01'))
            self.assertEqual((call[2]['direction'], call[2]['device'], call[2]['local_time'], call[2]['app']), ('outgoing', 'iPhone14,3', '00:00', 'Calls'))
            self.assertEqual(tstats['knowledge']['interactions'], 1)
            self.assertEqual(named('SCHEDULED_ON'), [('Lunch', '2026-01-01', {'start_time': '2026-01-01 09:30 UTC', 'end_time': '', 'all_day': False, 'occurrence': 1, 'occurrences': 1})])
            self.assertEqual(len({r['id'] for r in trecords if r['type'] == 'node'}), tstats['nodes'])
            self.assertTrue(all(r['properties'].get('name') for r in tnodes.values()))


if __name__ == '__main__':
    unittest.main()
