"""Scheduler, persistent alert deduplication, and graceful shutdown."""
import argparse
import asyncio
import json
import logging
import os
import random
import signal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram.error import Conflict, InvalidToken, RetryAfter

from config import load_config, load_telegram_config
from bot import build_bot
from monitor import PortalError, check_slots

logger = logging.getLogger(__name__)


def retry_delay(interval: int, cap: int, failures: int) -> float:
    floor = min(cap, interval * 2 ** min(failures, 20))
    return random.uniform(floor, min(cap, floor * 2))


class AlertStore:
    def __init__(self, path):
        self.path = path
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
                raise ValueError('Invalid alert state')
            self.sent = set(data)
        except FileNotFoundError:
            self.sent = set()

    def record(self, day):
        updated = self.sent | {day}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            os.chmod(temporary, 0o600)
            json.dump(sorted(updated), stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        self.sent = updated


async def monitor_loop(config, app, store):
    failures = 0
    while True:
        if datetime.now(ZoneInfo('Asia/Yerevan')).date() >= config.target_date:
            app.bot_data['status'] = 'Target date reached. Monitoring paused; update TARGET_DATE and restart.'
            logger.info('Target date reached; browser checks paused')
            await asyncio.sleep(3600)
            continue
        telegram_delay = 0.0
        try:
            days = await asyncio.wait_for(check_slots(config), timeout=300)
            for day in days:
                key = day.isoformat()
                if key not in store.sent:
                    await app.bot.send_message(
                        chat_id=config.user_id,
                        text=f'TLScontact Yerevan: available appointment date {key} '
                             f'(before {config.target_date}).\n'
                             f'Log in to book manually: {config.login_url}\n'
                             'Availability may change before you log in.',
                        disable_web_page_preview=True,
                    )
                    store.record(key)  # Persist only after Telegram accepts delivery.
            failures = 0
            app.bot_data['status'] = (
                f'Last successful check: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. '
                f'Qualifying dates: {len(days)}.')
            logger.info('Calendar check completed; qualifying dates=%d', len(days))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            category = type(exc).__name__
            if isinstance(exc, RetryAfter):
                retry_after = exc.retry_after
                telegram_delay = (retry_after.total_seconds() if hasattr(retry_after, 'total_seconds')
                                  else float(retry_after))
            if isinstance(exc, PortalError):
                logger.warning('Portal check: %s', exc)
            logger.warning('Check or alert delivery failed (%s); consecutive failures=%d', category, failures)
            app.bot_data['status'] = (
                f'Check or delivery failed ({category}); failures: {failures}. '
                'Verify portal access, selectors, and Telegram connectivity. Retrying with backoff.')
        delay = max(telegram_delay, retry_delay(config.interval, config.max_backoff, failures))
        logger.info('Next check in %.0f seconds', delay)
        await asyncio.sleep(delay)


async def run_application(config, telegram_only=False, client_store=None):
    store = None if telegram_only or client_store is not None else AlertStore(config.state_file)
    app = build_bot(config, client_store)
    app.bot_data['telegram_only'] = telegram_only
    if telegram_only:
        app.bot_data['status'] = 'Telegram-only mode: bot online; visa monitoring is disabled.'
    stop = asyncio.Event()
    fatal_polling_error = False

    def polling_error(error):
        nonlocal fatal_polling_error
        logger.warning('Telegram polling failed (%s)', type(error).__name__)
        if isinstance(error, (Conflict, InvalidToken)):
            fatal_polling_error = True
            stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with app:
        await app.start()
        task = None
        stop_task = None
        try:
            await app.updater.start_polling(drop_pending_updates=True, allowed_updates=['message'],
                                            error_callback=polling_error)
            if not telegram_only:
                if client_store is not None:
                    from agency_monitor import agency_loop
                    task = asyncio.create_task(agency_loop(config, app, client_store))
                else:
                    task = asyncio.create_task(monitor_loop(config, app, store))
            logger.info('Bot is running (%s). Press Ctrl+C to stop.',
                        'Telegram only' if telegram_only else 'visa monitoring enabled')
            logger.info('Open your bot in Telegram and send /start for instructions or /status for health.')
            stop_task = asyncio.create_task(stop.wait())
            watched = [stop_task] + ([task] if task else [])
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if fatal_polling_error:
                raise RuntimeError('Telegram polling stopped; check token and duplicate processes')
            if task is not None and task.done():
                await task
                raise RuntimeError('Monitor stopped unexpectedly')
        finally:
            pending = [item for item in (task, stop_task) if item is not None]
            for item in pending:
                item.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if app.updater.running:
                await app.updater.stop()
            await app.stop()
            if client_store is not None:
                app.bot_data['agency_chat'].clear()


async def run(config, telegram_only=False, agency=False):
    if not agency:
        return await run_application(config, telegram_only)
    from pathlib import Path
    from clients import ClientStore
    state_root = Path(os.getenv('STATE_FILE', str(Path(__file__).parent / 'state/alerts.json'))).parent
    client_store = ClientStore(Path(os.getenv('CLIENTS_FILE', str(state_root / 'clients.enc'))),
                               Path(os.getenv('CLIENTS_KEY_FILE', str(state_root / 'clients.key'))))
    try:
        await run_application(config, telegram_only, client_store)
    finally:
        client_store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telegram-only', action='store_true',
                        help='Run private Telegram commands without accessing TLScontact')
    parser.add_argument('--agency', action='store_true',
                        help='Manage client accounts in chat; check only the selected client')
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    for name in ('httpx', 'httpcore', 'telegram'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        config = load_telegram_config() if args.telegram_only else load_config(agency=args.agency)
    except ValueError as exc:
        logger.error('Configuration error: %s', exc)
        raise SystemExit(2) from None
    try:
        asyncio.run(run(config, telegram_only=args.telegram_only, agency=args.agency))
    except Exception as exc:
        logger.error('Service stopped (%s)', type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
