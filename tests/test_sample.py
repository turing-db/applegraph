"""The fictional Mac from ``apple-graph sample`` imports cleanly and still tells its story."""
from collections import defaultdict
import json
from pathlib import Path
import tempfile
import unittest

from apple_graph import export_graph
from apple_graph import sample
from apple_graph.model import HOME_LAYOUT


class SampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.home = root / 'home'
        cls.built = sample.build(cls.home)
        sources = {name: cls.home / HOME_LAYOUT[name] for name in ('safari', 'contacts', 'mail', 'photos', 'biome')}
        cls.stats = export_graph(cls.home / HOME_LAYOUT['source'], root / 'oliver.jsonl', entities_only=True,
                                 knowledge=cls.home / HOME_LAYOUT['knowledge'], sources=sources)
        records = [json.loads(line) for line in (root / 'oliver.jsonl').read_text().splitlines()]
        cls.nodes = {r['id']: r for r in records if r['type'] == 'node'}
        cls.edges = defaultdict(list)
        for r in records:
            if r['type'] == 'relationship':
                cls.edges[r['label']].append((cls.nodes[r['start']['id']]['properties'], cls.nodes[r['end']['id']]['properties'],
                                              r.get('properties', {})))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def named(self, label, start=None, end=None):
        return [(a, b, p) for a, b, p in self.edges[label] if (start is None or a['name'] == start) and (end is None or b['name'] == end)]

    def test_every_source_merges_onto_one_oliver(self):
        me = [n for n in self.nodes.values() if 'Me' in n['labels']]
        self.assertEqual([n['properties']['name'] for n in me], ['Oliver Dunmore'])
        self.assertEqual(self.stats['sources']['contacts']['matched'], 13)
        self.assertEqual(self.stats['unresolved_references'], {})
        self.assertTrue(all(n['properties'].get('name') for n in self.nodes.values()))
        for source in ('safari', 'mail', 'photos', 'biome'):
            self.assertIn(source, self.stats['sources'])
        self.assertGreater(self.stats['knowledge']['events'], 1000)

    def test_the_padel_line(self):
        calls, = self.named('CALLS_WITH', end='Padel Club Bookings')
        self.assertGreater(calls[2]['calls'], 150)
        self.assertGreater(calls[2]['talk_seconds'], 50 * 3600)
        self.assertFalse(self.named('MESSAGES_WITH', end='Padel Club Bookings'))
        self.assertTrue(self.named('MEMBER_OF_CHAT', start='Padel Club Bookings', end='Pebbles Room parents 🍎'))

    def test_bristol_weekends(self):
        car = self.named('CONNECTED_ON', start='Volvo XC90')
        self.assertTrue(car)
        self.assertEqual({b['weekday'] for _, b, _ in car} - {'Friday', 'Saturday', 'Sunday'}, set())
        photo_days = defaultdict(int)
        for _, place, _ in self.edges['PHOTOGRAPHED_AT']:
            photo_days[place['name']] += 1
        self.assertEqual(max(photo_days, key=photo_days.get), 'Clifton, Bristol')
        unnamed = [b['name'] for a, b, p in self.edges['APPEARS_WITH'] if a['name'] == 'Oliver Dunmore' and p['photos'] >= 100]
        self.assertTrue(any(name.startswith('person ') for name in unnamed))
        triangle = {b['name'] for a, b, _ in self.named('EMAILED', start='Little Acorns Nursery')}
        self.assertIn('Katie Rowe', triangle)

    def test_rebuild_is_identical_and_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            again = Path(directory) / 'home'
            self.assertEqual(sample.build(again), self.built)
            self.assertEqual(sample.build(again), self.built)  # Rebuilding over its own output is allowed.
            for relative in ('Library/Biome/streams/restricted/Siri.Remembers.CallHistory',):
                ours = sorted(p.read_bytes() for p in (self.home / relative).rglob('*') if p.is_file())
                theirs = sorted(p.read_bytes() for p in (again / relative).rglob('*') if p.is_file())
                self.assertEqual(ours, theirs)
            stranger = Path(directory) / 'someone-else'
            stranger.mkdir()
            (stranger / 'notes.txt').write_text('keep me')
            with self.assertRaises(ValueError):
                sample.build(stranger)
            self.assertEqual((stranger / 'notes.txt').read_text(), 'keep me')


if __name__ == '__main__':
    unittest.main()
