import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop
from agency_chat import AgencyChat
from agency_monitor import agency_loop
from bot import build_bot
from clients import ClientStore
from config import load_config
from test_core import config
from test_release import environment


class StoreTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_per_client_alert_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path, key = Path(folder) / 'clients.enc', Path(folder) / 'clients.key'
            store = ClientStore(path, key)
            one = store.add('Client A', 'agency@example.com', 'unique secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar/one')
            two = store.add('Client B', 'agency@example.com', 'another secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar/two')
            store.select(one)
            store.record(one, '2098-01-01')
            self.assertNotIn(b'unique secret', path.read_bytes())
            self.assertNotIn(b'agency@example.com', path.read_bytes())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
            store.close()
            again = ClientStore(path, key)
            self.addCleanup(again.close)
            self.assertEqual(again.selected, one)
            self.assertEqual(again.get(one)['password'], 'unique secret')
            self.assertEqual(again.get(one)['sent'], ['2098-01-01'])
            self.assertEqual(again.get(two)['sent'], [])
            again.delete(one)
            self.assertIsNone(again.selected)
            self.assertIsNone(again.get(one))
            self.assertIsNotNone(again.get(two))

    def test_missing_key_does_not_replace_existing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path, key = Path(folder) / 'clients.enc', Path(folder) / 'clients.key'
            path.write_bytes(b'existing encrypted data')
            with self.assertRaises(ValueError):
                ClientStore(path, key)
            self.assertFalse(key.exists())
            self.assertEqual(path.read_bytes(), b'existing encrypted data')

    def test_single_process_lock_and_failed_save_preserve_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path, key = Path(folder) / 'clients.enc', Path(folder) / 'clients.key'
            store = ClientStore(path, key)
            self.addCleanup(store.close)
            with self.assertRaises(BlockingIOError):
                ClientStore(path, key)
            with patch('clients.os.replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store.add('A', 'a@example.com', 'secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar')
            self.assertEqual(store.summaries(), [])

    def test_agency_config_does_not_require_personal_credentials(self):
        values = environment()
        values['TLS_LOGIN_URL'] = 'https://visas-fr.tlscontact.com/login'
        for key in ('TLS_EMAIL', 'TLS_PASSWORD', 'TARGET_DATE', 'TLS_APPOINTMENT_URL'):
            values.pop(key)
        with patch.dict('os.environ', values, clear=True):
            result = load_config(Path('/nonexistent'), agency=True)
        self.assertEqual(result.email, '')
        self.assertEqual(result.password, '')
        self.assertEqual(result.appointment_url, '')


class AgencyAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = ClientStore(Path(self.folder.name) / 'clients.enc', Path(self.folder.name) / 'clients.key')
        self.chat = AgencyChat(self.store)
        self.context = SimpleNamespace(args=[], bot_data={'client_changed': asyncio.Event(),
                                                         'client_health': {}, 'telegram_only': True})
        self.reply = AsyncMock()

    async def asyncTearDown(self):
        self.chat.clear()
        self.store.close()
        self.folder.cleanup()

    def update(self, text=''):
        return SimpleNamespace(effective_message=SimpleNamespace(text=text, delete=AsyncMock(), reply_text=self.reply),
                               effective_user=SimpleNamespace(id=42), effective_chat=SimpleNamespace(type='private', id=42))

    async def add_draft(self):
        await self.chat.add(self.update(), self.context)
        for value in ['Client One', 'one@example.com', ' password with spaces ', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar/one']:
            update = self.update(value)
            await self.chat.receive(update, self.context)
            update.effective_message.delete.assert_awaited_once()

    async def test_guided_entry_encrypted_only_after_confirmation(self):
        await self.add_draft()
        self.assertEqual(self.store.summaries(), [])
        await self.chat.confirm(self.update(), self.context)
        key = self.store.summaries()[0][0]
        self.assertEqual(self.store.get(key)['password'], ' password with spaces ')
        self.assertIsNone(self.store.selected)
        self.assertIsNone(self.chat.draft)
        replies = str(self.reply.call_args_list)
        self.assertNotIn(' password with spaces ', replies)
        self.assertNotIn('one@example.com', replies)
        self.context.args = [key]
        await self.chat.use(self.update(), self.context)
        self.assertEqual(self.store.selected, key)
        await self.chat.pause(self.update(), self.context)
        self.assertIsNone(self.store.selected)

    async def test_cancel_and_expiry_discard_password(self):
        await self.add_draft()
        await self.chat.cancel(self.update(), self.context)
        self.assertIsNone(self.chat.draft)
        self.assertFalse(self.store.summaries())
        await self.add_draft()
        self.chat.timer._callback()  # Run the same callback used by the inactivity timer.
        self.assertIsNone(self.chat.draft)
        self.assertFalse(self.store.summaries())

    async def test_client_delete_requires_confirmation(self):
        key = self.store.add('One', 'a@example.com', 'secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar')
        self.store.select(key)
        self.context.args = [key]
        await self.chat.delete(self.update(), self.context)
        self.assertIsNotNone(self.store.get(key))
        await self.chat.confirm(self.update(), self.context)
        self.assertIsNone(self.store.get(key))
        self.assertIsNone(self.store.selected)

    async def test_unauthorized_user_blocked_before_agency_commands(self):
        app = build_bot(config(), self.store)
        gate = app.handlers[-100][0].callback
        update = self.update('/addclient')
        update.effective_user.id = 999
        with self.assertRaises(ApplicationHandlerStop):
            await gate(update, self.context)
        self.assertIsNone(app.bot_data['agency_chat'].draft)
        self.assertTrue(any(getattr(h, 'commands', set()) == {'addclient'} for h in app.handlers[0]))

    async def test_failed_password_deletion_warns_without_echoing(self):
        await self.chat.add(self.update(), self.context)
        await self.chat.receive(self.update('One'), self.context)
        await self.chat.receive(self.update('one@example.com'), self.context)
        update = self.update('secret password')
        update.effective_message.delete.side_effect = RuntimeError('delete failed')
        await self.chat.receive(update, self.context)
        self.assertIn('Please delete it', str(self.reply.call_args_list))
        self.assertNotIn('secret password', str(self.reply.call_args_list))

    async def test_selection_switch_cancels_old_check_and_keeps_cooldown(self):
        first = self.store.add('One', 'one@example.com', 'one secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar/one')
        second = self.store.add('Two', 'two@example.com', 'two secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar/two')
        self.store.select(first)
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def check(active_config):
            self.assertEqual(active_config.email, 'one@example.com')
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        app = SimpleNamespace(bot_data=self.context.bot_data, bot=SimpleNamespace(send_message=AsyncMock()))
        checker = AsyncMock(side_effect=check)
        with patch('agency_monitor.check_slots', checker):
            task = asyncio.create_task(agency_loop(replace(config(), login_url='https://visas-fr.tlscontact.com/login'), app, self.store))
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.store.select(second)
                self.context.bot_data['client_changed'].set()
                await asyncio.wait_for(cancelled.wait(), 2)
                await asyncio.sleep(0.02)
                checker.assert_awaited_once()
                app.bot.send_message.assert_not_awaited()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_only_selected_client_receives_deduplicated_alert(self):
        first = self.store.add('One', 'one@example.com', 'one secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar/one')
        second = self.store.add('Two', 'two@example.com', 'two secret', '2099-01-01', 'https://visas-fr.tlscontact.com/calendar/two')
        self.store.select(second)
        app = SimpleNamespace(bot_data=self.context.bot_data, bot=SimpleNamespace(send_message=AsyncMock()))
        checker = AsyncMock(return_value=[date(2098, 1, 1)])
        with patch('agency_monitor.check_slots', checker):
            task = asyncio.create_task(agency_loop(replace(config(), login_url='https://visas-fr.tlscontact.com/login'), app, self.store))
            try:
                for _ in range(100):
                    if self.store.get(second)['sent']:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(self.store.get(second)['sent'], ['2098-01-01'])
                self.assertFalse(self.store.get(first)['sent'])
                self.assertEqual(checker.call_args.args[0].email, 'two@example.com')
                text = app.bot.send_message.call_args.kwargs['text']
                self.assertIn('Two', text)
                self.assertNotIn('two secret', text)
                self.assertNotIn('two@example.com', text)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
