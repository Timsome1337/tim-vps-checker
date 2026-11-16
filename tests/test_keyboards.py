"""Callback contract tests using local Telegram markup fakes (no network)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class _Button:
    def __init__(self, text, callback_data=None, url=None):
        self.text = text
        self.callback_data = callback_data
        self.url = url


class _Markup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


def _module():
    telegram = types.ModuleType('telegram')
    telegram.InlineKeyboardButton = _Button
    telegram.InlineKeyboardMarkup = _Markup
    spec = importlib.util.spec_from_file_location('app._keyboards_test', ROOT / 'app/keyboards.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'telegram': telegram}):
        spec.loader.exec_module(module)
    return module


def _buttons(keyboard):
    return [button for row in keyboard.inline_keyboard for button in row]


def _callbacks(keyboard):
    return [button.callback_data for button in _buttons(keyboard)]


class KeyboardContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = _module()

    def _ctrl(self, count=1, enabled=True):
        servers = [SimpleNamespace(id=f'server{i}', name=f'VPS {i}') for i in range(count)]
        preferences = SimpleNamespace(enabled=lambda user_id, name: enabled)
        return SimpleNamespace(config=SimpleNamespace(servers=servers), preferences=preferences)

    def test_single_server_navigation(self):
        callbacks = _callbacks(self.k.root_keyboard(self._ctrl()))
        self.assertEqual(len(callbacks), 10)
        for target in ('status:server0', 'billing', 'specs:server0', 'help',
                       'stats:server0', 'disk:server0', 'control', 'refresh', 'diag', 'alerts'):
            self.assertIn(target, callbacks)

    def test_multi_server_navigation(self):
        callbacks = _callbacks(self.k.root_keyboard(self._ctrl(count=2)))
        self.assertIn('status:server0', callbacks)
        self.assertIn('status:server1', callbacks)
        self.assertNotIn('control', callbacks)

    def test_chart_refresh_preserves_period(self):
        callbacks = _callbacks(self.k.chart_keyboard('server0', '7d'))
        self.assertIn('chart:server0:7d', callbacks)
        self.assertIn('chart:server0:24h', callbacks)
        self.assertIn('chart:server0:30d', callbacks)
        self.assertIn('stats:server0', callbacks)
        self.assertEqual(callbacks.count('chart:server0:7d'), 2)

    def test_control_confirmation_keeps_token(self):
        callbacks = _callbacks(self.k.reboot_confirm_keyboard('exampletoken'))
        self.assertEqual(callbacks, ['ctl:confirm:exampletoken', 'control'])

    def test_renewal_is_external_url_not_payment_api(self):
        buttons = _buttons(self.k.renewal_keyboard())
        self.assertEqual(buttons[0].url, 'https://my.aeza.net/')
        self.assertEqual(buttons[1].callback_data, 'control')

    def test_alert_preferences_are_individual(self):
        for state, symbol in ((True, '🟢'), (False, '⚪')):
            rows = self.k.alert_keyboard(self._ctrl(enabled=state), 123).inline_keyboard
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(row[0].text.startswith(symbol + ' ') for row in rows[:4]))
            self.assertEqual(rows[-2][0].callback_data, 'alerts:test')

    def test_diagnostics_and_action_navigation(self):
        self.assertEqual(_callbacks(self.k.diag_keyboard()), ['diag', 'alerts', 'overview'])
        self.assertEqual(_callbacks(self.k.actions_keyboard()), ['actions', 'control', 'overview'])

    def test_keyboards_not_built_inside_handlers(self):
        bot_source = (ROOT / 'bot.py').read_text(encoding='utf8')
        self.assertNotIn('InlineKeyboardButton(', bot_source)
        self.assertIn('from app.keyboards import', bot_source)


if __name__ == '__main__':
    unittest.main()
