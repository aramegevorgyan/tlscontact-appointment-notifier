import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop
from bot import build_bot
from config import Config, load_config
from main import AlertStore, monitor_loop, retry_delay
from monitor import PortalError, qualifying_dates


def config():
    return Config('email', 'secret', '123456:ABC', 42,
                  'https://visas-de.tlscontact.com/login',
                  'https://visas-de.tlscontact.com/calendar',
                  date(2027, 6, 1), {})


class CoreTests(unittest.TestCase):
    def test_dates_are_strictly_before_target_and_not_past(self):
        result = qualifying_dates(['2027-05-01', '2027-06-01', '2027-04-30',
                                   '2027-05-01'], config(), date(2027, 5, 1))
        self.assertEqual(result, [date(2027, 5, 1)])
        with self.assertRaises(PortalError):
            qualifying_dates(['Tuesday'], config())

    def test_backoff_bounds_and_cap(self):
        for failures in range(30):
            value = retry_delay(900, 21600, failures)
            floor = min(21600, 900 * 2 ** failures)
            self.assertGreaterEqual(value, floor)
            self.assertLessEqual(value, min(21600, floor * 2))

    def test_persistent_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            store = AlertStore(path)
            store.record('2027-05-01')
            self.assertEqual(AlertStore(path).sent, {'2027-05-01'})
            self.assertEqual(json.loads(path.read_text()), ['2027-05-01'])

    def test_missing_configuration_fails(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                load_config(Path('/nonexistent/env'))


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_gate_rejects_strangers_groups_and_missing_users(self):
        app = build_bot(config())
        gate = app.handlers[-100][0].callback
        for user, chat, kind in [(43, 43, 'private'), (42, -10, 'group'),
                                 (None, 42, 'private'), (42, 43, 'private')]:
            update = SimpleNamespace(effective_user=SimpleNamespace(id=user) if user else None,
                                     effective_chat=SimpleNamespace(id=chat, type=kind))
            with self.assertRaises(ApplicationHandlerStop):
                await gate(update, None)
        await gate(SimpleNamespace(effective_user=SimpleNamespace(id=42),
                                  effective_chat=SimpleNamespace(id=42, type='private')), None)

    async def test_failed_delivery_is_not_recorded(self):
        import asyncio
        with tempfile.TemporaryDirectory() as directory:
            store = AlertStore(Path(directory) / 'state.json')
            app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError)),
                                  bot_data={})
            with patch('main.check_slots', AsyncMock(return_value=[date(2027, 5, 1)])), \
                 patch('main.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    await monitor_loop(config(), app, store)
            self.assertEqual(store.sent, set())
            self.assertIn('failed', app.bot_data['status'])


class TelegramOnlyTests(unittest.TestCase):
    def test_telegram_mode_needs_no_portal_settings(self):
        from config import load_telegram_config
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': '123456:TEST',
                                      'ALLOWED_TELEGRAM_USER_ID': '42'}, clear=True):
            result = load_telegram_config(Path('/nonexistent/env'))
        self.assertEqual(result.user_id, 42)

    def test_example_user_id_is_rejected(self):
        from config import load_telegram_config
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': '123456:TEST',
                                      'ALLOWED_TELEGRAM_USER_ID': '123456789'}, clear=True):
            with self.assertRaises(ValueError):
                load_telegram_config(Path('/nonexistent/env'))
