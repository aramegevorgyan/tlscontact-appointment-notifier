"""Check the configured bot token without starting monitoring or sending messages."""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot


async def check():
    load_dotenv(Path(__file__).with_name('.env'))
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token or token.startswith('REPLACE_'):
        print('Set TELEGRAM_BOT_TOKEN in .env first. Keep the token private.')
        return 2
    try:
        async with Bot(token) as bot:
            info = await bot.get_me()
            print(f'Telegram connection OK: @{info.username}')
            print(f'Open https://t.me/{info.username} and press Start.')
            print('This check does not run command handlers. Run main.py after configuring TLScontact.')
        return 0
    except Exception as exc:
        print(f'Telegram connection failed ({type(exc).__name__}). Check token and network access.')
        return 1


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(check()))
