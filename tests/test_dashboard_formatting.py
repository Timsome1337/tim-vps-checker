from datetime import date, datetime, timezone
from dataclasses import replace
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
import unittest

from app.config import load_config
from app.formatting import (samara_time, remaining_text, meter, dashboard_text,
                            full_status, billing_text, services_text, specs, size)


class ValidateHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag in ('b', 'i')
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag
        self.stack.pop()


class TestHybrid42(unittest.TestCase):
    def setUp(self):
        self.s = replace(load_config('config.example.yaml').servers[0],
                         name='🇸🇪 VPS · Швеция',
                         expires_at=date(2027, 1, 7),
                         tariff='SWE-PROMO', price='1,99 € / месяц')
        self.ok = {'ok': True, 'data': {
            'cpu_percent': 2.5, 'vcpus': 1,
            'memory': {'percent': 17.9, 'used': 735600000, 'total': 4100000000},
            'disk': {'percent': 67.0, 'used': 7000000000, 'total': 10500000000},
            'load_avg': [0.01, 0.03, 0.0], 'uptime_seconds': 123456,
            'hostname': 'server', 'os': 'Ubuntu 24.04',
            'kernel': '6.8', 'architecture': 'x86_64', 'cpu_model': 'AMD Ryzen',
            'network': {'rx': 123456, 'tx': 12345678},
            'services': {'sing-box': 'active', 'xray': 'inactive'},
        }}

    def test_samara_offset(self):
        fixed = datetime(2026, 11, 15, 4, 18, tzinfo=timezone.utc)
        self.assertEqual(samara_time(fixed), '08:18 (Самара)')

    def test_samara_date_boundary(self):
        utc_dt = datetime(2026, 11, 15, 21, tzinfo=timezone.utc)
        self.assertEqual(utc_dt.astimezone(ZoneInfo('Europe/Samara')).date(),
                         date(2026, 11, 16))
        self.assertEqual(remaining_text(self.s, date(2026,11,16)), '52 дн.')

    def test_clean_dashboard(self):
        at = datetime(2026, 11, 15, 4, 18, tzinfo=timezone.utc)
        text = dashboard_text(self.s, self.ok, checked_at=at)
        self.assertIn('⚙️ Процессор: <b>2,5%</b>\n🧠 Оперативная память: <b>17,9%</b>\n💾 Диск: <b>67,0%</b>', text)
        self.assertIn('08:18 (Самара)', text)
        self.assertNotIn('UTC', text)
        self.assertNotIn('▰', text)

    def test_short_meter(self):
        for v in (0, 2.5, 25, 67, 95, 100):
            bar = meter(v)
            self.assertEqual(bar.count('▰')+bar.count('▱'), 8)
        self.assertIn('🔴', meter(95))

    def test_full_detail(self):
        text = full_status(self.s, self.ok)
        self.assertIn('735,6 МБ', text)
        self.assertIn('10,5 ГБ', text)
        self.assertIn('Самара', text)
        self.assertNotIn('1,99', text)

    def test_billing_and_secondary(self):
        for text in (billing_text((self.s,), 'Aeza API', 'active', False),
                     specs(self.s, self.ok), services_text(self.s, self.ok)):
            self.assertIn('Самара', text)
            validator = ValidateHTML()
            validator.feed(text)
            self.assertFalse(validator.stack)
            self.assertLess(len(text), 4096)

    def test_html_escaping(self):
        malicious = replace(self.s, name='VPS <x> & A', price='1<2 & more')
        text = dashboard_text(malicious, self.ok)
        self.assertIn('VPS &lt;x&gt; &amp; A', text)
        self.assertIn('1&lt;2 &amp; more', text)

    def test_units_not_renamed(self):
        self.assertEqual(size(2**30), '1,1 ГБ')
        self.assertEqual(size(1_000_000), '1,0 МБ')

if __name__ == '__main__':
    unittest.main()
