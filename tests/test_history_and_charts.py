"""Historical metrics and chart generation tests."""
import tempfile
import unittest
from pathlib import Path
from app.history import HistoryStore, summary, PERIODS, fmt_time
from app.charts import draw_chart

SAMPLE = {
    'ok': True, 'data': {
        'cpu_percent': 12.5,
        'memory': {'used': 800_000_000, 'total': 4_000_000_000, 'percent': 20},
        'disk': {'used': 7_000_000_000, 'total': 10_000_000_000, 'percent': 70},
        'uptime_seconds': 30000,
    }
}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = HistoryStore(Path(self.temp.name) / 'data' / 'history.sqlite3')
        self.t = 1_794_710_800  # sample Unix timestamp

    def test_first_sample_and_summary(self):
        self.db.record('sweden', SAMPLE, timestamp=self.t)
        rows = self.db.samples('sweden', '24h', timestamp=self.t + 60)
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary(rows)['cpu_avg'], 12.5)
        self.assertEqual(summary(rows)['disk_max'], 70)

    def test_failed_probe_creates_gap_in_chart(self):
        self.db.record('sweden', SAMPLE, timestamp=self.t)
        self.db.record('sweden', {'ok': False}, timestamp=self.t + 300)
        rows = self.db.samples('sweden', '24h', timestamp=self.t + 330)
        self.assertEqual([r['ok'] for r in rows], [1, 0])
        self.assertEqual(summary(rows)['count'], 1)
        self.assertIsNone(rows[1]['cpu'])

    def test_events_and_reboot(self):
        self.db.record('sweden', SAMPLE, timestamp=self.t)
        smaller = {'ok': True, 'data': dict(SAMPLE['data'], uptime_seconds=100)}
        self.db.record('sweden', smaller, notifications=['🔴 sing-box остановлен'], timestamp=self.t + 300)
        events = self.db.recent_events('sweden')
        self.assertEqual({e['kind'] for e in events}, {'reboot', 'alert'})
        self.assertTrue(any('перезагрузка' in e['description'] for e in events))

    def test_gap_not_server_outage(self):
        self.db.record('sweden', SAMPLE, timestamp=self.t)
        self.db.record('sweden', SAMPLE, timestamp=self.t + 2400)
        events = self.db.recent_events('sweden')
        self.assertEqual(len(events), 1)
        self.assertIn('Пауза в сборе показателей', events[0]['description'])
        self.assertIn('Причина неизвестна', events[0]['description'])

    def test_retention_prunes_old_data(self):
        self.db.record('sweden', SAMPLE, timestamp=self.t)
        self.db.record('sweden', SAMPLE, timestamp=self.t + 91 * 86400)
        self.assertEqual(len(self.db.samples('sweden', '30d', timestamp=self.t + 91*86400)), 1)
        with self.db.connect() as con:
            self.assertEqual(con.execute('select count(*) from samples').fetchone()[0], 1)

    def test_other_server_is_isolated(self):
        self.db.record('sweden', SAMPLE, timestamp=self.t)
        self.assertEqual(self.db.samples('other', '24h', timestamp=self.t), [])
        self.assertEqual(self.db.recent_events('other'), [])

    def test_periods_and_timestamp(self):
        self.assertEqual(PERIODS['24h'], 86400)
        self.assertEqual(PERIODS['7d'], 604800)
        self.assertEqual(PERIODS['30d'], 2592000)
        with self.assertRaises(ValueError):
            self.db.samples('sweden', 'forever')
        self.assertRegex(fmt_time(self.t), r'^\d\d\.\d\d \d\d:\d\d$')

    def test_duplicate_timestamp_is_replaced(self):
        self.db.record('sweden', SAMPLE, timestamp=self.t)
        other = {'ok': True, 'data': dict(SAMPLE['data'], cpu_percent=80)}
        self.db.record('sweden', other, timestamp=self.t)
        rows = self.db.samples('sweden','24h',timestamp=self.t)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['cpu'], 80)

    def test_chart_png(self):
        rows = [{'ts': self.t + i*300, 'ok': 1, 'cpu': 10+i,
                 'memory': 19+i/5, 'disk': 65} for i in range(6)]
        png = draw_chart(rows, '24h', 'Sweden')
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertLess(len(png), 2_000_000)

    def test_chart_with_missing_sample(self):
        rows = [{'ts': self.t + i*300, 'ok': 0 if i == 2 else 1,
                 'cpu': None if i == 2 else 20,
                 'memory': None if i == 2 else 30,
                 'disk': None if i == 2 else 60} for i in range(5)]
        self.assertTrue(draw_chart(rows, '7d', 'Sweden').startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
