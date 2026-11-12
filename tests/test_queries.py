"""Every catalogue query parses and runs in TuringDB against a synthetic graph."""
import json
from pathlib import Path
import tempfile
import unittest

from apple_graph import queries
from apple_graph.model import Builder

VALUES = dict(app='Ghostty', place='Paris', domain='example.com', apple_id='biome:1', month='2025-12',
              date='2025-12-24', person='Alice')


def synthetic_graph() -> Builder:
    """One of every node label and edge type the catalogue queries touch."""
    b = Builder()
    me = b.node(('me',), ['AppleEntity', 'Person', 'Me'], name='Me', apple_id='me', entity_type='person', source='derived')
    people = [b.node(('p', i), ['AppleEntity', 'Person'], name=n, apple_id=f'biome:{i}', entity_type='person', source='Messages',
                     photos_faces=10, photos_person_uuid=f'uuid{i}') for i, n in enumerate(['Alice', 'Bob', 'Face cluster 12', 'person 99'])]
    corr = b.node(('c',), ['AppleEntity', 'Correspondent'], name='Carol', apple_id='mail:c', entity_type='person', source='Mail')
    days = [b.day(d) for d in ('2025-12-24', '2025-12-27', '2025-12-28')]
    for d in days:
        b.node_records[d]['properties'].update(screen_on_seconds=40000, photos_taken=60, videos_taken=1)
    place = b.node(('pl',), ['AppleEntity', 'Place'], name='Paris', apple_id='Photos:Paris', latitude='55.9', longitude='-3.1')
    song = b.node(('m',), ['MediaItem'], name='Song', title='Song', artist='Artist', album='Album')
    app = b.node(('a',), ['AppleEntity', 'Software'], name='Ghostty', bundle_id='com.ghostty')
    org = b.node(('o',), ['AppleEntity', 'Organization'], name='Acme')
    bt = b.node(('bt',), ['BluetoothDevice'], name='Car', address='x', device_type=1, product_id=1)
    web = b.node(('w',), ['WebDomain'], name='example.com', domain='example.com', safari_visits=40, safari_pages=3,
                 safari_first_visit='2025-12-01T00:00:00', safari_last_visit='2025-12-28T00:00:00')
    chat = b.node(('chat',), ['GroupChat'], name='Chat', app='msg', source='Messages')
    event = b.node(('e',), ['AppleEntity', 'CalendarEvent'], name='Meeting')
    t = '2025-12-24T09:00:00'
    b.edge(app, org, 'PUBLISHED_BY')
    for i, p in enumerate(people):
        b.edge(me, p, 'MESSAGES_WITH', dict(messages=200, sent=150, received=50, first=t, last=t, apps='msg', source='Messages'))
        b.edge(me, p, 'CALLS_WITH', dict(calls=20, incoming=10, outgoing=10, talk_seconds=600, first=t, last=t, apps='tel', source='Calls'))
        b.edge(me, p, 'APPEARS_WITH', dict(photos=30, first=t, last=t))
        b.edge(p, chat, 'MEMBER_OF_CHAT', dict(app='msg', source='Messages'))
        b.edge(event, p, 'HAS_ATTENDEE', dict(via='x'))
        for d in days:
            b.edge(p, d, 'MESSAGED_ON', dict(messages=40, sent=20, received=20, app='msg', source='Messages'))
            b.edge(p, d, 'CALLED_ON', dict(calls=1, incoming=1, outgoing=0, talk_seconds=60, app='tel', source='Calls'))
            b.edge(p, d, 'PHOTOGRAPHED_ON', dict(photos=5, source='Photos'))
    for a in people:
        for c in people:
            if a < c:
                b.edge(a, c, 'APPEARS_WITH', dict(photos=12, first=t, last=t))
    b.edge(me, corr, 'EMAILED', dict(messages=5, to=5, cc=0, bcc=1, conversations=4, first=t, last=t))
    b.edge(corr, me, 'EMAILED', dict(messages=5, to=5, cc=0, bcc=0, conversations=4, first=t, last=t))
    b.edge(corr, people[0], 'EMAILED', dict(messages=5, to=5, cc=0, bcc=0, conversations=4, first=t, last=t))
    b.edge(people[0], me, 'EMAILED', dict(messages=5, to=5, cc=0, bcc=0, conversations=4, first=t, last=t))
    b.edge(people[0], corr, 'EMAILED', dict(messages=5, to=5, cc=0, bcc=0, conversations=4, first=t, last=t))
    for d in days:
        b.edge(d, place, 'PHOTOGRAPHED_AT', dict(photos=10, source='Photos'))
        b.edge(song, d, 'PLAYED_ON', dict(plays=4, app='music', device='x', source='NowPlaying'))
        b.edge(web, d, 'VISITED_ON', dict(visits=25, pages=2, first_visit=t, last_visit=t, source='Safari'))
        b.edge(event, d, 'SCHEDULED_ON', dict(start_time=t, end_time=t, all_day=False, occurrence=1, occurrences=1))
        for device in ('This Mac', 'iPhone'):
            b.edge(app, d, 'USED_ON', dict(first_start=t, last_end=t, seconds=11000, minutes=200.0, sessions=3, share_of_day=0.5, device=device, source='x'))
        b.edge(app, d, 'NOTIFIED_ON', dict(first_start=t, last_end=t, notifications=9, source='knowledgeC'))
        b.edge(bt, d, 'CONNECTED_ON', dict(first_start=t, last_end=t, seconds=600, minutes=10.0, sessions=1, share_of_day=1.0, source='knowledgeC'))
        b.edge(app, d, 'SEND_MESSAGE', dict(verb='send message', intent_class='INSendMessageIntent', app='Ghostty', direction='outgoing',
                                           donated_by_siri=False, timestamp_utc=t, local_time='09:00', device='This Mac', uuid='u'))
    lonely = b.day('2025-12-30')
    b.node_records[lonely]['properties'].update(screen_on_seconds=40000)

    return b


class CatalogueTest(unittest.TestCase):
    def test_structure(self):
        self.assertEqual(set(queries.QUERIES), set(queries.CATEGORIES))
        self.assertEqual(set(queries.CATEGORIES.values()), {'insight', 'inference', 'visual'})
        for name in queries.QUERIES:
            self.assertTrue(set(queries.parameters(name)) <= set(VALUES), name)
        line = queries.one_line('trip', place="O'Hare")
        self.assertNotIn('\n', line)
        self.assertIn("{name: 'O\\'Hare'}", line)
        with self.assertRaises(KeyError):
            queries.one_line('trip')

    def test_every_query_runs(self):
        from turingdb import TuringDB
        builder = synthetic_graph()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            with (root / 'data' / 'synthetic.jsonl').open('w') as stream:
                for record in builder.node_records + builder.edge_records:
                    stream.write(json.dumps(record) + '\n')
            db = TuringDB(type='embedded', data_dir=str(root))
            db.query("LOAD JSONL 'synthetic.jsonl' AS synthetic")
            db.set_graph('synthetic')
            empty = set()
            for name in queries.QUERIES:
                values = {key: VALUES[key] for key in queries.parameters(name)}
                with self.subTest(query=name):
                    rows = len(queries.run(db, name, **values))
                    self.assertEqual(len(db.query(queries.one_line(name, **values))), rows)
                    if rows == 0:
                        empty.add(name)
            # These exclude people you already talk to, and the fixture talks to everyone.
            self.assertEqual(empty, {'unused_apps', 'photo_friends_of_friends', 'calls_not_texts',
                                     'chat_only_people', 'friends_of_friends'})


if __name__ == '__main__':
    unittest.main()
