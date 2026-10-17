import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Conflict, RetryAfter
from bot import build_bot
from config import load_config
from main import AlertStore, monitor_loop, run
from test_core import config


def environment():
    values = dict(TLS_EMAIL='test@example.invalid', TLS_PASSWORD=' leading and trailing ',
                  TELEGRAM_BOT_TOKEN='123456:TEST', ALLOWED_TELEGRAM_USER_ID='42',
                  TARGET_DATE='2099-06-01', TLS_LOGIN_URL='https://visas-de.tlscontact.com/login',
                  TLS_APPOINTMENT_URL='https://visas-de.tlscontact.com/calendar')
    values.update({f'TLS_{key}_SELECTOR': '#example' for key in
                   ('EMAIL', 'PASSWORD', 'SUBMIT', 'AUTHENTICATED', 'READY', 'SLOT', 'EMPTY')})
    return values


class ConfigurationTests(unittest.TestCase):
    def test_full_mode_rejects_example_or_bot_id(self):
        for user in ('123456789', '123456'):
            with self.subTest(user=user), patch.dict('os.environ', environment() | {
                    'ALLOWED_TELEGRAM_USER_ID': user}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(Path('/nonexistent/env'))

    def test_preserves_password_whitespace(self):
        with patch.dict('os.environ', environment(), clear=True):
            self.assertEqual(load_config(Path('/nonexistent/env')).password, ' leading and trailing ')

    def test_rejects_incomplete_dates_and_untrusted_urls(self):
        for key, value in [('SLOT_DATE_FORMAT', '%d/%m'),
                           ('TLS_LOGIN_URL', 'https://tlscontact.com.evil.invalid/login'),
                           ('TLS_LOGIN_URL', 'https://visas-de.tlscontact.com:bad/login')]:
            with self.subTest(key=key, value=value), patch.dict('os.environ', environment() | {key: value}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(Path('/nonexistent/env'))

    def test_corrupt_state_fails_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'alerts.json'
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                AlertStore(path)
            self.assertEqual(path.read_text(), '{broken')


class ReleaseAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_help_explain_telegram_only_mode(self):
        app = build_bot(config())
        handlers = [h for h in app.handlers[0] if h.commands & {'start', 'help'}]
        self.assertEqual(len(handlers), 2)
        for handler in handlers:
            reply = AsyncMock()
            await handler.callback(SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply)),
                                   SimpleNamespace(bot_data={'telegram_only': True}))
            text = reply.call_args.args[0]
            self.assertIn('I am not checking for appointments yet', text)
            self.assertIn('/status', text)
            self.assertIn('Check that the website confirms your booking', text)
            for term in ('.env', 'TARGET_DATE', 'selectors', 'EC2', '--telegram-only'):
                self.assertNotIn(term, text)

    async def test_success_is_recorded_and_not_sent_again(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AlertStore(Path(folder) / 'alerts.json')
            app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), bot_data={})
            with patch('main.check_slots', AsyncMock(return_value=[date(2027, 5, 1)])), \
                 patch('main.asyncio.sleep', AsyncMock(side_effect=[None, asyncio.CancelledError])):
                with self.assertRaises(asyncio.CancelledError):
                    await monitor_loop(config(), app, store)
            app.bot.send_message.assert_awaited_once()
            self.assertEqual(store.sent, {'2027-05-01'})

    async def test_telegram_retry_after_is_respected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AlertStore(Path(folder) / 'alerts.json')
            app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=RetryAfter(30000))), bot_data={})
            sleep = AsyncMock(side_effect=asyncio.CancelledError)
            with patch('main.check_slots', AsyncMock(return_value=[date(2027, 5, 1)])), patch('main.asyncio.sleep', sleep):
                with self.assertRaises(asyncio.CancelledError):
                    await monitor_loop(config(), app, store)
            self.assertGreaterEqual(sleep.call_args.args[0], 30000)
            self.assertFalse(store.sent)

    async def test_expired_target_does_not_open_browser(self):
        app = SimpleNamespace(bot_data={})
        check = AsyncMock()
        with patch('main.check_slots', check), patch('main.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await monitor_loop(replace(config(), target_date=date(2000, 1, 1)), app, None)
        check.assert_not_awaited()
        self.assertIn('paused', app.bot_data['status'])

    async def test_unexpected_worker_failure_stops_application(self):
        with tempfile.TemporaryDirectory() as folder:
            app = AsyncMock()
            app.bot_data = {}
            app.updater.running = True
            with patch('main.build_bot', return_value=app), \
                 patch('main.monitor_loop', AsyncMock(side_effect=RuntimeError('test worker failure'))), \
                 patch.object(asyncio.get_running_loop(), 'add_signal_handler'):
                with self.assertRaises(RuntimeError):
                    await run(replace(config(), state_file=Path(folder) / 'alerts.json'))
            app.updater.stop.assert_awaited_once()
            app.stop.assert_awaited_once()

    async def test_polling_conflict_stops_telegram_mode_without_browser(self):
        app = AsyncMock()
        app.bot_data = {}
        app.updater.running = True
        async def start_polling(**kwargs):
            kwargs['error_callback'](Conflict('other process'))
        app.updater.start_polling.side_effect = start_polling
        worker = AsyncMock()
        with patch('main.build_bot', return_value=app), patch('main.monitor_loop', worker), \
             patch.object(asyncio.get_running_loop(), 'add_signal_handler'):
            with self.assertRaises(RuntimeError):
                await run(config(), telegram_only=True)
        worker.assert_not_awaited()
        app.updater.stop.assert_awaited_once()
        app.stop.assert_awaited_once()
