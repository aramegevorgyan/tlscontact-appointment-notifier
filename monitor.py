"""Read-only calendar adapter. Selectors must be verified for the user's portal."""
import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from config import Config


class PortalError(RuntimeError):
    pass


def qualifying_dates(values: list[str], config: Config, today: date | None = None) -> list[date]:
    today = today or datetime.now(ZoneInfo('Asia/Yerevan')).date()
    dates = set()
    for value in values:
        try:
            parsed = datetime.strptime(value.strip(), config.date_format).date()
        except ValueError:
            raise PortalError('Slot date format changed; check date extraction settings') from None
        if today <= parsed < config.target_date:
            dates.add(parsed)
    return sorted(dates)


async def check_slots(config: Config) -> list[date]:
    # A fresh context each check avoids retaining credentials/cookies on disk.
    async with Stealth().use_async(async_playwright()) as playwright:
        browser = await playwright.chromium.launch(headless=config.headless)
        try:
            context = await browser.new_context(locale='en-US', timezone_id='Asia/Yerevan')
            page = await context.new_page()
            page.set_default_timeout(config.timeout)
            blocked = asyncio.Event()

            def response_received(response):
                if response.status in (403, 429) and response.request.resource_type in ('document', 'xhr', 'fetch'):
                    blocked.set()

            page.on('response', response_received)

            async def guard():
                if blocked.is_set():
                    raise PortalError('Portal blocked or rate limited the request')
                if await page.locator('iframe[src*="captcha"]:visible, input[autocomplete="one-time-code"]:visible').count():
                    raise PortalError('Manual CAPTCHA or MFA attention required')

            await page.goto(config.login_url, wait_until='domcontentloaded')
            await guard()
            s = config.selectors
            if s['COOKIE']:
                cookie = page.locator(s['COOKIE'])
                if await cookie.is_visible():
                    await cookie.click()
            await page.locator(s['EMAIL']).fill(config.email)
            await page.locator(s['PASSWORD']).fill(config.password)
            await page.locator(s['SUBMIT']).click()
            await guard()
            await page.locator(s['AUTHENTICATED']).wait_for(state='visible')
            await page.goto(config.appointment_url, wait_until='domcontentloaded')
            await guard()
            return await read_calendar(page, config, guard)
        finally:
            await browser.close()


async def read_calendar(page, config: Config, guard) -> list[date]:
    """Read available dates without clicking slots; require explicit empty evidence."""
    # READY must mean calendar data finished loading, not just its container.
    await page.locator(config.selectors['READY']).wait_for(state='visible')
    slots = page.locator(config.selectors['SLOT'])
    empty = page.locator(config.selectors['EMPTY'])
    # Positive evidence is required for either availability or no availability.
    deadline = asyncio.get_running_loop().time() + config.timeout / 1000
    while True:
        await guard()
        values = []
        for slot in await slots.all():
            if await slot.is_visible() and await slot.is_enabled():

                if await slot.get_attribute('aria-disabled') == 'true':
                    continue
                value = (await slot.get_attribute(config.date_attribute)
                         if config.date_attribute else await slot.inner_text())
                if not value:
                    raise PortalError('Available slot has no full date')
                values.append(value)
        if values:
            return qualifying_dates(values, config)
        if await empty.is_visible():
            return []
        if asyncio.get_running_loop().time() >= deadline:
            raise PortalError('Calendar not recognized; verify selectors or resolve login challenge')
        await asyncio.sleep(0.5)
