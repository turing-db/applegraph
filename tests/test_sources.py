"""Synthetic fixtures for the Safari, AddressBook, Mail, Photos and Biome importers."""
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from apple_graph.biome import add_biome
from apple_graph.contacts import add_contacts
from apple_graph.mail import add_mail
from apple_graph.model import Builder, UNIX_TO_APPLE, is_placeholder, normalize_handle
from apple_graph.photos import add_photos
from apple_graph.safari import add_safari
from apple_graph.segb import parse_message, read_segb, stream_events

DAY = 788918400.0  # 2026-01-01T00:00:00Z in seconds since 2001; tests run with TZ=UTC semantics via noon timestamps.
NOON = DAY + 12 * 3600


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field(number: int, value) -> bytes:
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, float):
        return varint(number << 3 | 1) + struct.pack('<d', value)
    if isinstance(value, str):
        value = value.encode()
    return varint(number << 3 | 2) + varint(len(value)) + value


def write_segb(path: Path, payloads: list[bytes]) -> None:
    body = bytearray()
    index = bytearray()
    for payload in payloads:
        body += struct.pack('<II', 0, 0) + payload
        index = struct.pack('<IId', len(body), 1, DAY) + index
        while len(body) % 4:
            body += b'\0'
    header = struct.pack('<4sIdIIII', b'SEGB', len(payloads), DAY, 10, 0xFFFFFFFF, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(body) + b'\0' * 64 + bytes(index))


def participant(role: str, handle: str, name: str) -> bytes:
    return field(2, field(1, role) + field(2, field(1, handle) + field(2, 'PersonHandle') + field(4, json.dumps({'name': name}))))


def interaction(app: str, unix_seconds: float, direction: int, group: bool = False, duration: float = 0.0) -> bytes:
    info = (field(1, 'uuid') + field(2, 'INSendMessageIntent') + field(4, app) + field(5, 1) + field(6, direction)
            + field(8, unix_seconds) + field(9, duration) + field(11, json.dumps({'isGroup': group})))
    return field(1, info)


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.builder = Builder()
        # A pre-existing graph person reachable by phone and Apple identifiers.
        self.alice = self.builder.node(('entity', '42'), ['AppleEntity', 'Person'], name='Alice', apple_id='42', entity_type='person')
        self.builder.register_handle('+1 555 0100', ('entity', '42'))
        self.builder.identifier_index[('cncontact', 'ALICE:ABPerson')] = ('entity', '42')
        self.builder.identifier_index[('phperson', 'PH-ALICE')] = ('entity', '42')

    def names(self, label):
        nodes = self.builder.node_records
        return sorted((nodes[int(e['start']['id'])]['properties']['name'], nodes[int(e['end']['id'])]['properties']['name'], e.get('properties', {}))
                      for e in self.builder.edge_records if e['label'] == label)

    def test_normalize_handle(self):
        self.assertEqual(normalize_handle('+1 (555) 010-0'), '15550100')
        self.assertEqual(normalize_handle('mailto:Bob@Example.com'), 'bob@example.com')
        self.assertEqual(normalize_handle('0015550100'), '15550100')
        self.assertIsNone(normalize_handle('not a handle'))
        self.builder.register_handle('+44 7700 900123', ('entity', '42'))
        self.assertEqual(self.builder.lookup_handle('07700900123'), ('entity', '42'))  # Suffix match.

    def test_placeholders_and_overrides(self):
        self.assertTrue(is_placeholder('person 405112233445566778'))
        self.assertTrue(is_placeholder('Entity 42'))
        self.assertTrue(is_placeholder('Face cluster 7'))
        self.assertFalse(is_placeholder('Remy'))
        self.assertFalse(is_placeholder('Apple'))
        cluster = self.builder.node(('entity', '99'), ['AppleEntity', 'Person'], name='person 99', apple_id='99', photos_person_uuid='PH-99')
        app = self.builder.node(('app', 'com.toyopagroup.picaboo'), ['AppleEntity', 'Software'], name='picaboo', bundle_id='com.toyopagroup.picaboo')
        renamed = self.builder.apply_name_overrides({'PH-99': 'Carol', 'com.toyopagroup.picaboo': 'Snapchat', '42': 'Alice Liddell', 'missing': 'x'})
        self.assertEqual(renamed, 3)
        self.assertEqual(self.builder.props(cluster)['name'], 'Carol')
        self.assertEqual(self.builder.props(app)['name'], 'Snapchat')
        self.assertEqual((self.builder.props(self.alice)['name'], self.builder.props(self.alice)['other_names']), ('Alice Liddell', 'Alice'))

    def test_segb_roundtrip(self):
        path = self.root / 'stream' / 'local' / '1'
        write_segb(path, [field(1, 'hello') + field(2, 7) + field(3, 1.5), field(1, '123456789')])
        records = read_segb(path)
        self.assertEqual(len(records), 2)
        self.assertEqual(parse_message(records[0].data), [(1, 'str', 'hello'), (2, 'varint', 7), (3, 'fixed64', 1.5)])
        events = list(stream_events(self.root / 'stream'))
        self.assertEqual(len(events), 1)  # Numeric field-1 records are sync bookkeeping and skipped.
        self.assertEqual(events[0][:2], ('local', ''))

    def test_safari(self):
        db = sqlite3.connect(self.root / 'History.db')
        db.executescript('CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT); CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, visit_time REAL, title TEXT);')
        db.executemany('INSERT INTO history_items VALUES (?,?)', [(1, 'https://github.com/a'), (2, 'https://github.com/b'), (3, 'https://once.example/')])
        db.executemany('INSERT INTO history_visits VALUES (?,?,?,?)', [(1, 1, NOON, 't'), (2, 2, NOON + 60, 't'), (3, 1, NOON + 86400, 't'), (4, 3, NOON, 't')])
        db.commit(); db.close()
        stats = add_safari(self.builder, self.root / 'History.db')
        self.assertEqual((stats['domains'], stats['skipped_domains'], stats['visited_on_edges']), (1, 1, 2))
        visited = self.names('VISITED_ON')
        self.assertEqual([(a, b, p['visits'], p['pages']) for a, b, p in visited], [('github.com', '2026-01-01', 2, 2), ('github.com', '2026-01-02', 1, 1)])
        self.assertEqual(self.builder.props(('web', 'github.com'))['safari_pages'], 2)

    def test_contacts(self):
        sources = self.root / 'AddressBook' / 'Sources' / 'X'
        sources.mkdir(parents=True)
        db = sqlite3.connect(sources / 'AddressBook-v22.abcddb')
        db.executescript('''CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZUNIQUEID TEXT, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZNICKNAME TEXT, ZORGANIZATION TEXT, ZJOBTITLE TEXT, ZBIRTHDAY REAL, ZCONTAINERWHERECONTACTISME INTEGER);
            CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT, ZLABEL TEXT); CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT, ZLABEL TEXT);
            CREATE TABLE ZABCDPOSTALADDRESS (ZOWNER INTEGER, ZSTREET TEXT, ZCITY TEXT, ZZIPCODE TEXT, ZCOUNTRYNAME TEXT, ZLABEL TEXT);
            INSERT INTO ZABCDRECORD VALUES (1, 'ALICE:ABPerson', 'Alice', 'Liddell', NULL, 'Wonderland Ltd', 'Explorer', 0.0, NULL), (2, 'ME:ABPerson', 'Suhas', NULL, NULL, NULL, NULL, NULL, 7), (3, 'GROUP:ABGroup', NULL, NULL, NULL, NULL, NULL, NULL, NULL);
            INSERT INTO ZABCDPHONENUMBER VALUES (1, '+1 555 0100', '_$!<Mobile>!$_'), (1, '+1 555 0199', 'WhatsApp'), (2, '+1 555 0001', 'iPhone');
            INSERT INTO ZABCDEMAILADDRESS VALUES (2, 'me@example.com', 'Work');
            INSERT INTO ZABCDPOSTALADDRESS VALUES (1, '1 Rabbit Hole', 'Oxford', 'OX1', 'UK', 'Home');''')
        db.commit(); db.close()
        stats = add_contacts(self.builder, self.root / 'AddressBook' / 'AddressBook-v22.abcddb')
        self.assertEqual((stats['contacts'], stats['matched'], stats['created'], stats['me_found']), (2, 1, 1, True))
        alice = self.builder.props(('entity', '42'))
        self.assertEqual((alice['organization'], alice['job_title'], alice['birthday'], alice['phones']), ('Wonderland Ltd', 'Explorer', '2001-01-01', 'Mobile: +1 555 0100 | WhatsApp: +1 555 0199'))
        self.assertEqual(alice['postal_addresses'], 'Home: 1 Rabbit Hole, Oxford, OX1, UK')
        self.assertEqual(self.builder.lookup_handle('+15550199'), ('entity', '42'))
        self.assertIn('Me', self.builder.labels(self.builder.me))
        self.assertEqual(self.builder.props(self.builder.me)['emails'], 'Work: me@example.com')

    def test_mail(self):
        db = sqlite3.connect(self.root / 'Envelope Index')
        db.executescript('''CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT); CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
            CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, sender INTEGER, date_received INTEGER, date_sent INTEGER, mailbox INTEGER, conversation_id INTEGER, deleted INTEGER);
            CREATE TABLE recipients (ROWID INTEGER PRIMARY KEY, message INTEGER, address INTEGER, type INTEGER, position INTEGER);
            INSERT INTO addresses VALUES (1, 'me@example.com', 'Me'), (2, 'bob@example.com', 'Bob Builder'), (3, 'news@list.example', 'Newsletter');
            INSERT INTO mailboxes VALUES (1, 'imap://x/INBOX'), (2, 'imap://x/[Gmail]/Sent'), (3, 'imap://x/[Gmail]/Spam');
            INSERT INTO messages VALUES (1, 2, ?, ?, 1, 100, 0), (2, 1, ?, ?, 2, 100, 0), (3, 3, ?, ?, 1, 101, 0), (4, 3, ?, ?, 3, 102, 0), (5, 2, ?, ?, 1, 103, 1);
            ''' .replace('?, ?', f'{int(NOON + UNIX_TO_APPLE)}, {int(NOON + UNIX_TO_APPLE)}'))
        db.executemany('INSERT INTO recipients VALUES (?,?,?,?,?)', [(1, 1, 1, 0, 0), (2, 2, 2, 0, 0), (3, 2, 3, 1, 1), (4, 3, 1, 0, 0), (5, 4, 1, 0, 0)])
        db.commit(); db.close()
        stats = add_mail(self.builder, self.root / 'Envelope Index', {'me@example.com'})
        self.assertEqual((stats['messages'], stats['skipped']), (3, 1))
        emailed = self.names('EMAILED')
        self.assertEqual([(a, b, p['messages'], p['to'], p['cc']) for a, b, p in emailed],
                         [('Bob Builder', 'Me', 1, 1, 0), ('Me', 'Bob Builder', 1, 1, 0), ('Me', 'Newsletter', 1, 0, 1), ('Newsletter', 'Me', 1, 1, 0)])
        bob = next(r for r in self.builder.node_records if r['properties']['name'] == 'Bob Builder')
        self.assertEqual(bob['labels'], ['AppleEntity', 'Correspondent'])
        self.assertEqual([(a, b, p['messages']) for a, b, p in self.names('EMAILED_ON')], [('Bob Builder', '2026-01-01', 1), ('Me', '2026-01-01', 1), ('Newsletter', '2026-01-01', 1)])

    def test_photos(self):
        db = sqlite3.connect(self.root / 'Photos.sqlite')
        db.executescript('''CREATE TABLE ZPERSON (Z_PK INTEGER PRIMARY KEY, ZPERSONUUID TEXT, ZFULLNAME TEXT, ZDISPLAYNAME TEXT, ZFACECOUNT INTEGER, ZVERIFIEDTYPE INTEGER);
            CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZDATECREATED REAL, ZKIND INTEGER, ZMOMENT INTEGER, ZTRASHEDSTATE INTEGER);
            CREATE TABLE ZADDITIONALASSETATTRIBUTES (Z_PK INTEGER PRIMARY KEY, ZASSET INTEGER, ZTIMEZONEOFFSET INTEGER);
            CREATE TABLE ZDETECTEDFACE (ZPERSONFORFACE INTEGER, ZASSETFORFACE INTEGER);
            CREATE TABLE ZMOMENT (Z_PK INTEGER PRIMARY KEY, ZTITLE TEXT, ZAPPROXIMATELATITUDE REAL, ZAPPROXIMATELONGITUDE REAL);
            INSERT INTO ZPERSON VALUES (1, 'PH-ALICE', NULL, NULL, 40, 0), (2, 'PH-BOB', 'Bob', NULL, 2, 1), (3, 'PH-NOISE', NULL, NULL, 1, 0), (4, 'PH-BIG', NULL, NULL, 9, 0);
            INSERT INTO ZASSET VALUES (1, ?, 0, 1, 0), (2, ?, 0, 1, 0), (3, ?, 1, NULL, 0), (4, ?, 0, 1, 1);
            INSERT INTO ZADDITIONALASSETATTRIBUTES VALUES (1, 1, 0), (2, 2, 0), (3, 3, -3600), (4, 4, 0);
            INSERT INTO ZDETECTEDFACE VALUES (1, 1), (2, 1), (1, 2), (2, 2), (3, 2), (4, 3), (1, 4);
            INSERT INTO ZMOMENT VALUES (1, 'Oxford', 51.75, -1.25);'''.replace('?', str(NOON)))
        db.commit(); db.close()
        stats = add_photos(self.builder, self.root / 'Photos.sqlite', min_faces=5)
        self.assertEqual((stats['assets'], stats['people_matched'], stats['people_created'], stats['places']), (3, 1, 2, 1))
        self.assertEqual(self.builder.props(('entity', '42'))['photos_faces'], 40)
        self.assertEqual([(a, b, p['photos']) for a, b, p in self.names('PHOTOGRAPHED_ON')], [('Alice', '2026-01-01', 2), ('Bob', '2026-01-01', 2), ('Face cluster 4', '2026-01-01', 1)])
        self.assertEqual([(a, b, p['photos']) for a, b, p in self.names('APPEARS_WITH')], [('Alice', 'Bob', 2)])
        self.assertEqual([(a, b, p['photos']) for a, b, p in self.names('PHOTOGRAPHED_AT')], [('2026-01-01', 'Oxford', 2)])
        self.assertEqual(self.builder.props(('place', 'oxford'))['latitude'], '51.75')
        self.assertEqual(self.builder.props(('day', '2026-01-01'))['photos_taken'], 2)
        self.assertEqual(self.builder.props(('day', '2026-01-01'))['videos_taken'], 1)

    def test_biome(self):
        streams = self.root / 'streams'
        unix_noon = NOON + UNIX_TO_APPLE
        write_segb(streams / 'Siri.Remembers.MessageHistory' / 'remote' / 'DEV' / '1', [
            interaction('net.whatsapp.WhatsApp', unix_noon, 1) + participant('senderHandle', '+15550100', 'Alice L'),
            interaction('net.whatsapp.WhatsApp', unix_noon + 60, 2) + participant('recipientHandles', '+15550100', 'Alice L'),
            interaction('net.whatsapp.WhatsApp', unix_noon + 120, 2) + participant('recipientHandles', '+15550177', 'Carol'),
            interaction('net.whatsapp.WhatsApp', unix_noon + 180, 2, group=True) + participant('recipientHandles', '+15550100', 'Alice L')
            + participant('recipientHandles', '+15550177', 'Carol') + field(2, field(1, 'speakableGroupName') + field(2, field(1, 'Book club') + field(2, 'MessageGroup'))),
            field(1, '123456789012345') + field(5, 'biomesyncd'),
        ])
        write_segb(streams / 'Siri.Remembers.CallHistory' / 'remote' / 'DEV' / '1', [
            interaction('com.apple.InCallService', unix_noon, 2, duration=90.0) + participant('contactHandles', '+15550100', 'Alice L'),
            interaction('com.apple.InCallService', unix_noon + 3600, 1, duration=0.0) + participant('contactHandles', '+15550100', 'Alice L'),
        ])
        write_segb(streams / 'Media.NowPlaying' / 'local' / '1', [
            field(2, NOON) + field(3, 1) + field(4, 'Album') + field(5, 'Artist') + field(6, 240) + field(8, 'Song') + field(15, 'com.spotify.client'),
            field(2, NOON + 60) + field(3, 2) + field(4, 'Album') + field(5, 'Artist') + field(6, 240) + field(8, 'Song') + field(15, 'com.spotify.client'),
            field(2, NOON + 7200) + field(3, 1) + field(5, 'Artist') + field(8, 'Song') + field(15, 'com.spotify.client'),
            field(2, NOON) + field(3, 1) + field(8, '') + field(15, 'com.spotify.client'),
        ])
        write_segb(streams / 'App.InFocus' / 'remote' / 'DEV' / '1', [
            field(2, 1) + field(3, 1) + field(4, NOON) + field(6, 'net.whatsapp.WhatsApp'),
            field(2, 1) + field(3, 0) + field(4, NOON + 600) + field(6, 'net.whatsapp.WhatsApp'),
            field(2, 1) + field(3, 1) + field(4, NOON + 700) + field(6, 'com.apple.Maps'),
            field(2, 1) + field(3, 0) + field(4, NOON + 1000) + field(6, 'com.apple.Maps'),
        ])
        write_segb(streams / 'App.InFocus' / 'local' / '1', [field(2, 1) + field(3, 1) + field(4, NOON) + field(6, 'com.apple.Safari')])
        stats = add_biome(self.builder, streams, {'DEV': 'iPhone14,3'})
        self.assertEqual(stats['messages'], dict(messages=4, counterparts=3, messaged_on_edges=3))
        with_edges = self.names('MESSAGES_WITH')
        self.assertEqual([(a, b, p['messages'], p['sent'], p['received']) for a, b, p in with_edges],
                         [('Me', 'Alice', 2, 1, 1), ('Me', 'Book club', 1, 1, 0), ('Me', 'Carol', 1, 1, 0)])
        self.assertEqual(sorted(a for a, b, _ in self.names('MEMBER_OF_CHAT')), ['Alice', 'Carol'])
        self.assertEqual([(a, b, p['received'], p['app']) for a, b, p in self.names('MESSAGED_ON') if a == 'Alice'], [('Alice', '2026-01-01', 1, 'net.whatsapp.WhatsApp')])
        calls, = self.names('CALLS_WITH')
        self.assertEqual((calls[1], calls[2]['calls'], calls[2]['incoming'], calls[2]['outgoing'], calls[2]['talk_seconds']), ('Alice', 2, 1, 1, 90))
        played, = self.names('PLAYED_ON')
        self.assertEqual((played[0], played[1], played[2]['plays'], played[2]['device']), ('Song', '2026-01-01', 2, 'This Mac'))
        self.assertEqual(self.builder.props(('media', 'song', 'artist'))['album'], 'Album')
        used = self.names('USED_ON')
        self.assertEqual([(a, b, p['seconds'], p['device'], p['share_of_day']) for a, b, p in used],
                         [('Maps', '2026-01-01', 300, 'iPhone14,3', 0.3333), ('WhatsApp', '2026-01-01', 600, 'iPhone14,3', 0.6667)])
        self.assertEqual(stats['app_focus']['devices'], 1)


if __name__ == '__main__':
    unittest.main()
