"""Serial checks for one selected client, with a shared cooldown across switches."""
import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from telegram.error import RetryAfter
from monitor import PortalError, check_slots

logger = logging.getLogger(__name__)


async def agency_loop(config, app, clients):
    from main import retry_delay
    changed = app.bot_data['client_changed']
    health = app.bot_data['client_health']
    loop = asyncio.get_running_loop()
    next_due = 0.0
    failures = 0
    while True:
        changed.clear()
        key = clients.selected
        client = clients.get(key) if key else None
        if client is None:
            await changed.wait()
            continue
        if date.fromisoformat(client['target_date']) <= datetime.now(ZoneInfo('Asia/Yerevan')).date():
            health[key] = 'The latest date has passed. Add a new entry with a future date.'
            await changed.wait()
            continue
        remaining = next_due - loop.time()
        if remaining > 0:
            health.setdefault(key, 'Waiting for the next scheduled check.')
            try:
                await asyncio.wait_for(changed.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
            continue
        version = clients.revision
        # Switching/pausing cannot bypass the global interval, including a cancelled check.
        next_due = loop.time() + retry_delay(config.interval, config.max_backoff, failures)
        check_task = change_task = None
        try:
            if urlsplit(client['appointment_url']).hostname != urlsplit(config.login_url).hostname:
                raise PortalError('Client calendar must belong to the configured destination portal')
            active_config = replace(config, email=client['email'], password=client['password'],
                                    target_date=date.fromisoformat(client['target_date']),
                                    appointment_url=client['appointment_url'])
            health[key] = 'Checking appointments now.'
            check_task = asyncio.create_task(asyncio.wait_for(check_slots(active_config), timeout=300))
            change_task = asyncio.create_task(changed.wait())
            await asyncio.wait([check_task, change_task], return_when=asyncio.FIRST_COMPLETED)
            if changed.is_set() or clients.selected != key or clients.revision != version:
                health[key] = 'Previous check cancelled after the selected client changed.'
                continue
            days = await check_task
            for day in days:
                if clients.selected != key or clients.revision != version:
                    break
                current = clients.get(key)
                if current is None or day.isoformat() in current['sent']:
                    continue
                await app.bot.send_message(
                    chat_id=config.user_id,
                    text=f'Appointment available for {client["label"]} ({key}): {day.isoformat()}.\n'
                         f'Before {client["target_date"]}.\nLog in and book manually: {config.login_url}\n'
                         'Open the link promptly to book it yourself. Availability may change; nothing has been reserved.',
                    disable_web_page_preview=True,
                )
                clients.record(key, day.isoformat())
            failures = 0
            health[key] = (f'Last successful check: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. '
                           f'Matching dates: {len(days)}.')
            next_due = loop.time() + retry_delay(config.interval, config.max_backoff, failures)
            logger.info('Client check completed; matching dates=%d', len(days))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            retry_after = 0.0
            if isinstance(exc, RetryAfter):
                value = exc.retry_after
                retry_after = value.total_seconds() if hasattr(value, 'total_seconds') else float(value)
            next_due = loop.time() + max(retry_after, retry_delay(config.interval, config.max_backoff, failures))
            health[key] = ('The appointment check or alert delivery failed. I will retry later. '
                           'If it keeps failing, check the account on TLScontact and contact the person who set up this bot.')
            logger.warning('Client check or delivery failed (%s)', type(exc).__name__)
        finally:
            tasks = [task for task in (check_task, change_task) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
