"""Durable state, private SQLite snapshots and safe operation invariants."""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from app.maintenance import sqlite_snapshot, verify_sqlite
from app.state import StateStore
from app.history import HistoryStore
from app.alert_preferences import AlertPreferences


class StateDurability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'data/state.json'

    def test_missing_file_starts_empty(self):
        self.assertEqual(StateStore(self.path).get('sweden'), {})

    def test_round_trip_preserves_nested_alert_state(self):
        value = {'smart_alerts': {'resources': {'cpu_percent': {'active': True}}}, 'available': False}
        store = StateStore(self.path)
        store.set('sweden', value)
        self.assertEqual(StateStore(self.path).get('sweden'), value)

    def test_private_mode(self):
        StateStore(self.path).set('sweden', {'ok': True})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_corrupt_json_is_never_silently_reset(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{broken json')
        with self.assertRaises(RuntimeError): StateStore(self.path)
        self.assertEqual(self.path.read_text(), '{broken json')

    def test_wrong_root_type_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('[]')
        with self.assertRaises(RuntimeError): StateStore(self.path)

    def test_symlink_is_refused(self):
        self.path.parent.mkdir(parents=True)
        original = self.path.parent / 'original'
        original.write_text('{}')
        self.path.symlink_to(original)
        with self.assertRaises(RuntimeError): StateStore(self.path)

    def test_failed_rename_leaves_memory_and_file_untouched(self):
        store = StateStore(self.path)
        store.set('sweden', {'x': 1})
        before = self.path.read_bytes()
        with patch('app.state.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): store.set('sweden', {'x': 2})
        self.assertEqual(store.get('sweden'), {'x': 1})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.state-*.tmp')), [])

    def test_no_nan_or_infinity_corrupts_json(self):
        store = StateStore(self.path)
        with self.assertRaises(ValueError): store.set('sweden', {'bad': float('nan')})
        self.assertFalse(self.path.exists())

    def test_multiple_servers_survive_update(self):
        store = StateStore(self.path)
        store.set('one', {'a': 1})
        store.set('two', {'b': 2})
        self.assertEqual(StateStore(self.path).get('one'), {'a': 1})
        self.assertEqual(StateStore(self.path).get('two'), {'b': 2})


class BackupAndIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_missing_database_is_not_created(self):
        path = self.base / 'missing.sqlite3'
        self.assertFalse(verify_sqlite(path)[0])
        self.assertFalse(path.exists())

    def test_corrupt_database_is_rejected(self):
        path = self.base / 'bad.sqlite3'
        path.write_bytes(b'not sqlite')
        self.assertFalse(verify_sqlite(path)[0])
        with self.assertRaises(RuntimeError): sqlite_snapshot(path, self.base/'backup.sqlite3')

    def test_history_snapshot_survives_schema_and_data(self):
        src = self.base / 'history.sqlite3'
        h = HistoryStore(src)
        h.add_event('sweden', 'startup', 'test', timestamp=100)
        action = h.start_control_action('sweden', 1111111111, 'reboot', timestamp=101)
        h.finish_control_action(action, 'accepted', timestamp=102)
        dst = self.base / 'backups/history.sqlite3'
        sqlite_snapshot(src, dst)
        self.assertTrue(verify_sqlite(dst)[0])
        self.assertEqual(stat.S_IMODE(dst.stat().st_mode), 0o600)
        original = h.recent_events('sweden')
        copied = HistoryStore(dst).recent_events('sweden')
        self.assertEqual(original, copied)
        self.assertEqual(len(HistoryStore(dst).recent_control_actions('sweden')), 1)

    def test_preferences_snapshot_keeps_three_account_settings(self):
        source = self.base/'preferences.sqlite3'
        p = AlertPreferences(source)
        for uid in (1111111111, 2222222222, 3333333333):
            p.toggle(uid, 'billing')
        copied = self.base/'snapshot.sqlite3'
        sqlite_snapshot(source, copied)
        new_p = AlertPreferences(copied)
        for uid in (1111111111, 2222222222, 3333333333):
            self.assertFalse(new_p.enabled(uid, 'billing'))

    def test_snapshot_will_not_overwrite_existing_file(self):
        source = self.base/'history.sqlite3'
        HistoryStore(source)
        dest = self.base/'existing.sqlite3'
        dest.write_text('do not overwrite')
        with self.assertRaises(FileExistsError): sqlite_snapshot(source, dest)
        self.assertEqual(dest.read_text(), 'do not overwrite')

    def test_snapshot_does_not_modify_source(self):
        source = self.base/'history.sqlite3'
        HistoryStore(source)
        before = source.read_bytes()
        sqlite_snapshot(source, self.base/'backup.sqlite3')
        self.assertEqual(source.read_bytes(), before)

    def test_snapshot_rejects_symlink_source(self):
        source = self.base/'history.sqlite3'
        HistoryStore(source)
        link = self.base/'link.sqlite3'
        link.symlink_to(source)
        self.assertFalse(verify_sqlite(link)[0])


class ArchitectureChecks(unittest.TestCase):
    def test_no_external_monitoring_or_daily_jobs(self):
        root = Path(__file__).resolve().parents[1]
        src = (root/'bot.py').read_text()
        self.assertNotIn('run_daily(', src)
        self.assertNotIn('HetrixTools', src)
        self.assertIn('except Exception:', src)
        self.assertIn("CommandHandler('actions', actions_view)", src)

    def test_three_accounts_remain_allowlisted(self):
        src = (Path(__file__).resolve().parents[1]/'bot.py').read_text()
        self.assertIn('update.effective_user.id in ctrl.allowed_user_ids', src)
        self.assertIn("update.effective_chat.type == 'private'", src)

    def test_no_vpn_commands_in_public_bot(self):
        src = (Path(__file__).resolve().parents[1]/'bot.py').read_text()
        self.assertNotIn("CommandHandler('vpn'", src)
        self.assertNotIn('vpn_view', src)

    def test_per_server_loop_isolated(self):
        tree = ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
        func = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'scheduled_check')
        self.assertTrue(any(isinstance(n, ast.Try) for n in ast.walk(func)))
        self.assertTrue(any(isinstance(n, ast.Await) for n in ast.walk(func)))

class TelegramAlertPrivacy(unittest.TestCase):
    def test_test_notification_is_only_on_request(self):
        """The manual test alert targets its requesting account, not all allowed IDs."""
        source = (Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf8')
        handler = source.split('async def test_alerts_view(', 1)[1].split('\n\nasync def diag_view(', 1)[0]
        self.assertIn('if not authorized(update, ctrl):', handler)
        self.assertIn('uid = update.effective_user.id', handler)
        self.assertIn('chat_id=uid', handler)
        self.assertNotIn('ctrl.allowed_user_ids', handler)
        self.assertIn("CommandHandler('testalerts', testalerts_cmd)", source)
        keyboards = (Path(__file__).resolve().parents[1] / 'app/keyboards.py').read_text(encoding='utf8')
        self.assertIn("callback_data='alerts:test'", keyboards)
        self.assertIn("elif data == 'alerts:test':", source)


if __name__ == '__main__':
    unittest.main()
