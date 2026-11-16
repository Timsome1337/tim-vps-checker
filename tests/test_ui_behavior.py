"""Focused offline regression tests for chart navigation and message replacement."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'bot.py').read_text(encoding='utf8')
KEYBOARD_SOURCE = (ROOT / 'app/keyboards.py').read_text(encoding='utf8')


class ChartNavigationTests(unittest.TestCase):
    def test_graph_screen_keeps_selected_period(self):
        keyboard = KEYBOARD_SOURCE.split('def chart_keyboard(', 1)[1].split('\n\ndef events_keyboard(', 1)[0]
        view = SOURCE.split('async def chart_view(', 1)[1].split('\n\nasync def disk_cmd(', 1)[0]
        self.assertIn("callback_data=f'chart:{sid}:{period}'", keyboard)
        self.assertIn('keyboard = chart_keyboard(server.id, period)', view)

    def test_chart_removes_old_message_only_after_successful_send(self):
        view = SOURCE.split('async def chart_view(', 1)[1].split('\n\nasync def disk_cmd(', 1)[0]
        self.assertIn('await context.bot.send_photo(', view)
        self.assertIn('await query.message.delete()', view)
        self.assertLess(view.index('await context.bot.send_photo('),
                        view.index('await query.message.delete()'))
        self.assertIn('except TelegramError:', view)


if __name__ == '__main__':
    unittest.main()
