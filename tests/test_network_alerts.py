"""Daily network deltas, per-user alert history and safe recovery."""
import ast
from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

from app.history import HistoryStore
from app.alert_preferences import AlertPreferences, CATEGORIES
from app.recovery import recovery_status
from app.probe import PROBE_SCRIPT

TZ = ZoneInfo('Europe/Samara')

def sample(rx=0, tx=0, iface='enp0s3'):
    return {'ok': True, 'data': {
        'cpu_percent': 2.5,
        'memory': {'percent': 20, 'used': 800_000_000, 'total': 4_000_000_000},
        'disk': {'percent': 50, 'used': 5_000_000_000, 'total': 10_000_000_000},
        'uptime_seconds': 120000,
        'services': {'sing-box':'active', 'x-ui':'active', 'xray':'inactive'},
        'network': {'interface':iface, 'rx':rx, 'tx':tx, 'rx_mbps':1.2,'tx_mbps':0.8},
    }}

def timestamp(day, hour, minute):
    return int(datetime(2026,11,day,hour,minute,tzinfo=TZ).timestamp())

class NetworkAlertTests(unittest.TestCase):
    def test_today_delta_only_from_samara_midnight(self):
        with tempfile.TemporaryDirectory() as t:
            h = HistoryStore(Path(t)/'db.sqlite')
            h.record('a', sample(100, 100), timestamp=timestamp(14,23,55))
            h.record('a', sample(200, 250), timestamp=timestamp(15,0,5))
            h.record('a', sample(800, 550), timestamp=timestamp(15,0,10))
            v = h.traffic_today('a',timestamp=timestamp(15,0,11))
            self.assertEqual((v['rx'],v['tx']), (600,300))
            self.assertEqual(v['intervals'],1)

    def test_counter_reset_and_interface_switch_are_excluded(self):
        with tempfile.TemporaryDirectory() as t:
            h=HistoryStore(Path(t)/'db.sqlite')
            b=timestamp(15,10,0)
            for i, (rx,tx,iface) in enumerate([(1000,1000,'eth0'),(1500,1600,'eth0'),
                   (10,20,'eth0'),(500,600,'eth1'),(800,1000,'eth1')]):
                h.record('a', sample(rx,tx,iface),timestamp=b+300*i)
            v=h.traffic_today('a',timestamp=b+1400)
            self.assertEqual((v['rx'],v['tx']), (800,1000))
            self.assertEqual(v['intervals'],2)
            self.assertEqual(v['gaps'],2)

    def test_gaps_not_counted(self):
        with tempfile.TemporaryDirectory() as t:
            h=HistoryStore(Path(t)/'db.sqlite')
            b=timestamp(15,10,0)
            h.record('a', sample(100,100),timestamp=b)
            h.record('a', sample(100000,100000),timestamp=b+2000)
            v=h.traffic_today('a',timestamp=b+2100)
            self.assertEqual(v['intervals'],0)
            self.assertEqual(v['gaps'],1)

    def test_existing_history_survives_schema(self):
        with tempfile.TemporaryDirectory() as t:
            h=HistoryStore(Path(t)/'db.sqlite')
            b=timestamp(15,8,0)
            h.record('a',sample(100,200),timestamp=b)
            h2=HistoryStore(Path(t)/'db.sqlite')
            self.assertEqual(len(h2.samples('a','24h',timestamp=b)),1)
            h2.record('a',sample(200,300),timestamp=b+300)
            self.assertEqual(h2.traffic_today('a',timestamp=b+400)['rx'],100)

    def test_delivery_markers_are_per_account(self):
        with tempfile.TemporaryDirectory() as t:
            db=Path(t)/'p.sqlite3'
            p=AlertPreferences(db)
            p.mark_sent(1111111111,'resources',timestamp=100)
            self.assertEqual(AlertPreferences(db).last_sent(1111111111,'resources'),100)
            self.assertIsNone(p.last_sent(2222222222,'resources'))
            self.assertIsNone(p.last_sent(1111111111,'billing'))
            self.assertTrue(p.enabled(2222222222,'resources'))

    def test_record_startup_event_not_reboot(self):
        with tempfile.TemporaryDirectory() as t:
            h=HistoryStore(Path(t)/'db.sqlite')
            h.add_event('a','startup','Запущен процесс бота',timestamp=timestamp(15,10,0))
            events=h.recent_events('a')
            self.assertEqual(events[0]['kind'],'startup')
            self.assertIn('Запущен',events[0]['description'])

    def test_systemd_readonly_enabled(self):
        def mocked(args, **kw):
            return Mock(returncode=0, stdout='enabled\n' if 'is-enabled' in args else 'on-failure\n')
        with patch('app.recovery.subprocess.run', side_effect=mocked) as cmd:
            self.assertEqual(recovery_status(),(True,'Автозапуск включён; Restart=on-failure'))
            self.assertEqual(cmd.call_count,2)
            for call in cmd.call_args_list:
                self.assertNotIn('restart',call.args[0])

    def test_systemd_inactive(self):
        def mocked(args, **kw):
            return Mock(returncode=0, stdout='disabled\n' if 'is-enabled' in args else 'no\n')
        with patch('app.recovery.subprocess.run', side_effect=mocked):
            self.assertFalse(recovery_status()[0])

    def test_probe_network_speed_no_actions(self):
        tree=ast.parse(PROBE_SCRIPT.replace('__SERVICES_LITERAL__','[]'))
        names={f.name for f in tree.body if isinstance(f,ast.FunctionDef)}
        self.assertIn('network_live',names)
        self.assertIn('net_totals',names)
        self.assertNotIn('docker',PROBE_SCRIPT.lower())

    def test_no_scheduled_daily_reports_and_handlers_present(self):
        src=(Path(__file__).resolve().parents[1]/'bot.py').read_text()
        self.assertIn("CommandHandler('network', network_cmd)",src)
        self.assertIn('ctrl.preferences.mark_sent',src)
        self.assertNotIn('run_daily(',src)
        self.assertNotIn('run_monthly(',src)
        self.assertIn('ctrl.history.add_event',src)

if __name__ == '__main__': unittest.main()
