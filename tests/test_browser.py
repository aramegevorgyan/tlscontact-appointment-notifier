"""Opt-in Chromium tests against local HTML only: RUN_BROWSER_TESTS=1."""
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from monitor import PortalError, read_calendar
from test_core import config


@unittest.skipUnless(os.getenv('RUN_BROWSER_TESTS') == '1', 'Set RUN_BROWSER_TESTS=1 to launch Chromium')
class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = Stealth().use_async(async_playwright())
        self.playwright = await self.manager.__aenter__()
        self.addAsyncCleanup(self.manager.__aexit__, None, None, None)
        self.browser = await self.playwright.chromium.launch()
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        self.page.set_default_timeout(1000)
        self.today = datetime.now(ZoneInfo('Asia/Yerevan')).date()
        self.config = replace(config(), timeout=20, target_date=self.today + timedelta(days=30),
                              selectors={'READY': '#ready', 'SLOT': '.available', 'EMPTY': '#empty'})

    async def test_only_visible_enabled_available_dates_without_clicking(self):
        await self.page.set_content(f'''
            <div id="ready">Loaded</div>
            <button class="available" data-date="{self.today}" onclick="window.clicked=true">Available</button>
            <button class="available" disabled data-date="bad">Disabled</button>
            <button class="available" style="display:none" data-date="bad">Hidden</button>
            <button class="available" aria-disabled="true" data-date="bad">Unavailable</button>
        ''')
        self.assertEqual(await read_calendar(self.page, self.config, AsyncMock()), [self.today])
        self.assertIsNone(await self.page.evaluate('window.clicked'))

    async def test_explicit_empty_calendar(self):
        await self.page.set_content('<div id="ready">Loaded</div><div id="empty">No appointments</div>')
        self.assertEqual(await read_calendar(self.page, self.config, AsyncMock()), [])

    async def test_unknown_calendar_is_error_not_empty(self):
        await self.page.set_content('<div id="ready">Loaded</div><div>Unexpected page</div>')
        with self.assertRaises(PortalError):
            await read_calendar(self.page, self.config, AsyncMock())

    async def test_unparseable_slot_date_is_error(self):
        await self.page.set_content('<div id="ready">Loaded</div><button class="available" data-date="Monday">Available</button>')
        with self.assertRaises(PortalError):
            await read_calendar(self.page, self.config, AsyncMock())
