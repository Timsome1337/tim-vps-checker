import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.history import HistoryStore, summary, russian_measurements
from app.probe import PROBE_SCRIPT


def sample(cpu=12.0, used=3_000_000_000, status='active'):
    return {'ok': True, 'data': {
        'cpu_percent': cpu,
        'memory': {'percent': 15, 'used': 600_000_000, 'total': 4_000_000_000},
        'disk': {'percent': used * 100 / 10_000_000_000, 'used': used,
                 'total': 10_000_000_000},
        'uptime_seconds': 250000,
        'services': {'x-ui': status, 'sing-box': 'active', 'xray': 'inactive'},
    }}


class UpgradeTests(unittest.TestCase):
    def test_plural(self):
        for n, ending in [(0, 'измерений'), (1, 'измерение'), (2, 'измерения'),
                          (3, 'измерения'), (4, 'измерения'), (5, 'измерений'),
                          (11, 'измерений'), (21, 'измерение'), (22, 'измерения'),
                          (112, 'измерений')]:
            self.assertEqual(russian_measurements(n), f'{n} {ending}')

    def test_history_summary_min_max_delta(self):
        with tempfile.TemporaryDirectory() as t:
            history = HistoryStore(Path(t) / 'history.sqlite3')
            history.record('sweden', sample(cpu=10, used=3_000_000_000), timestamp=100_000)
            history.record('sweden', sample(cpu=40, used=3_250_000_000), timestamp=100_300)
            rows = history.samples('sweden', '24h', timestamp=100_500)
            stats = summary(rows)
            self.assertEqual(stats['count'], 2)
            self.assertEqual(stats['cpu_min'], 10.0)
            self.assertEqual(stats['cpu_max'], 40.0)
            self.assertEqual(stats['cpu_avg'], 25.0)
            self.assertEqual(stats['disk_used_delta_bytes'], 250_000_000)

    def test_service_transition_event_after_baseline(self):
        with tempfile.TemporaryDirectory() as t:
            history = HistoryStore(Path(t) / 'history.sqlite3')
            history.record('sweden', sample(status='active'), timestamp=100_000)
            self.assertEqual(history.recent_events('sweden'), [])
            history.record('sweden', sample(status='inactive'), timestamp=100_300)
            events = history.recent_events('sweden')
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['kind'], 'service')
            self.assertIn('x-ui', events[0]['description'])

    def test_service_from_config_can_be_recorded(self):
        with tempfile.TemporaryDirectory() as t:
            store = HistoryStore(Path(t) / 'history.sqlite3')
            baseline = sample()
            baseline['data']['services'] = {'nginx': 'active'}
            failed = sample()
            failed['data']['services'] = {'nginx': 'failed'}
            store.record('sweden', baseline, timestamp=100_000)
            store.record('sweden', failed, timestamp=100_300)
            events = store.recent_events('sweden')
            self.assertEqual(len(events), 1)
            self.assertIn('nginx', events[0]['description'])

    def test_existing_sqlite_data_survive_upgrade(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / 'history.sqlite3'
            store = HistoryStore(path)
            store.record('sweden', sample(), timestamp=100_000)
            store2 = HistoryStore(path)
            self.assertEqual(len(store2.samples('sweden', '24h', timestamp=100_000)), 1)
            with closing(sqlite3.connect(path)) as con:
                self.assertIn('service_states',
                    {row[0] for row in con.execute("select name from sqlite_master where type='table'")})

    def test_chart_one_sample(self):
        try:
            from app.charts import draw_chart
            import matplotlib
        except ImportError:
            self.skipTest('matplotlib is not installed in test environment')
        png = draw_chart([{'ts': 1_700_000_000, 'ok': 1,
                           'cpu': 0, 'memory': 15, 'disk': 63}], '24h', 'VPS')
        self.assertTrue(png.startswith(b'\x89PNG'))

if __name__ == '__main__':
    unittest.main()
