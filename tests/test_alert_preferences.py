import tempfile
import unittest
from pathlib import Path
from app.alert_preferences import AlertPreferences, category, CATEGORIES

class TestAlertPreferences53(unittest.TestCase):
    def test_default_enabled_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'preferences.sqlite3'
            prefs = AlertPreferences(db)
            for uid in (1111111111, 2222222222, 3333333333):
                for item in CATEGORIES:
                    self.assertTrue(prefs.enabled(uid, item))
            self.assertFalse(prefs.toggle(2222222222, 'billing'))
            self.assertFalse(prefs.enabled(2222222222, 'billing'))
            self.assertTrue(prefs.enabled(1111111111, 'billing'))
            self.assertTrue(prefs.enabled(3333333333, 'billing'))
            again = AlertPreferences(db)
            self.assertFalse(again.enabled(2222222222, 'billing'))
            self.assertTrue(again.toggle(2222222222, 'billing'))
            self.assertTrue(again.enabled(2222222222, 'billing'))

    def test_unknown_category_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = AlertPreferences(Path(tmp) / 'a.sqlite3')
            with self.assertRaises(ValueError):
                prefs.toggle(1111111111, 'reboot')

    def test_message_classification(self):
        self.assertEqual(category('💳 Напоминание об оплате VPS'), 'billing')
        self.assertEqual(category('🔴 VPS: сервис sing-box остановился'), 'services')
        self.assertEqual(category('⚠️ На VPS осталось менее 1 ГБ свободного места'), 'resources')
        self.assertEqual(category('🚨 VPS: проверка не удалась два раза подряд'), 'connection')

if __name__ == '__main__':
    unittest.main()
