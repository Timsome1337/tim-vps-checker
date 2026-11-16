from datetime import date
from pathlib import Path
import tempfile
import textwrap
import unittest
from app.smart_alerts import choose_reminder, evaluate_smart
from app.config import load_config
from app.formatting import expiration, size

YAML = '''
servers:
  - id: sweden
    name: Sweden
    enabled: true
    mode: local
    expires_at: '2026-12-31'
    services: [xray]
'''


class TestConfig(unittest.TestCase):
    def config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.yaml'
            p.write_text(YAML, encoding='utf-8')
            return load_config(p)

    def test_load(self):
        c = self.config()
        self.assertEqual(c.servers[0].id, 'sweden')
        self.assertEqual(c.servers[0].expires_at, date(2026, 12, 31))

    def test_invalid_ssh_host(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.yaml'
            p.write_text(YAML.replace('mode: local', 'mode: ssh'), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(p)

    def test_expiration(self):
        self.assertIn('5 дн.', expiration(self.config().servers[0], date(2026, 12, 26)))

    def test_human_sizes(self):
        self.assertEqual(size(2**30), '1,1 ГБ')

    def test_expiry_band(self):
        self.assertIsNone(choose_reminder(25, (14, 7, 3, 1, 0)))
        self.assertEqual(choose_reminder(5, (14, 7, 3, 1, 0)), 7)
        self.assertEqual(choose_reminder(-1, (14, 7, 3, 1, 0)), 0)

    def test_failing_then_recovered(self):
        c = self.config(); s = c.servers[0]
        state = {}
        msgs, state = evaluate_smart(s, {'ok': False}, state, c, date(2026, 12, 1))
        self.assertFalse(any('два раза' in m for m in msgs))
        msgs, state = evaluate_smart(s, {'ok': False}, state, c, date(2026, 12, 1))
        self.assertTrue(any('два раза' in m for m in msgs))
        good = {'ok': True, 'data': {'cpu_percent': 20, 'memory': {'percent': 30},
                                     'disk': {'percent': 40}, 'services': {'xray': 'active'}}}
        msgs, state = evaluate_smart(s, good, state, c, date(2026, 12, 1))
        self.assertTrue(any('восстановлены' in m for m in msgs))

    def test_threshold_only_once(self):
        c = self.config(); s = c.servers[0]
        high = {'ok': True, 'data': {'cpu_percent': 98, 'memory': {'percent': 15},
                                     'disk': {'percent': 15}, 'services': {'xray': 'active'}}}
        first, state = evaluate_smart(s, high, {}, c, date(2026, 12, 1))
        second, state = evaluate_smart(s, high, state, c, date(2026, 12, 1))
        third, state = evaluate_smart(s, high, state, c, date(2026, 12, 1))
        fourth, _ = evaluate_smart(s, high, state, c, date(2026, 12, 1))
        self.assertFalse(any('CPU' in x for x in first + second))
        self.assertTrue(any('CPU' in x for x in third))
        self.assertFalse(any('CPU' in x for x in fourth))

    def test_services_transition(self):
        c = self.config(); s = c.servers[0]
        base = {'ok': True, 'data': {'cpu_percent': 5, 'memory': {'percent': 15},
                                     'disk': {'percent': 15}, 'services': {'xray': 'active'}}}
        _, state = evaluate_smart(s, base, {}, c, date(2026, 12, 1))
        failing = {'ok': True, 'data': dict(base['data'], services={'xray': 'failed'})}
        first, state = evaluate_smart(s, failing, state, c, date(2026, 12, 1))
        second, _ = evaluate_smart(s, failing, state, c, date(2026, 12, 1))
        self.assertFalse(any('xray' in x for x in first))
        self.assertTrue(any('xray' in x and 'failed' in x for x in second))


if __name__ == '__main__':
    unittest.main()
