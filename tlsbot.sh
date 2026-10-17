#!/usr/bin/env bash
# Run with: bash tlsbot.sh (no compilation or activation needed).
set -Eeuo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
PYTHON="$PWD/.venv/bin/python"

ensure_env() {
    if [[ ! -e .env ]]; then
        cp -- .env.example .env
        printf '%s\n' 'Created .env from the template. Fill in your own values.'
    fi
    chmod 600 .env
}

require_python() {
    if [[ ! -x "$PYTHON" ]]; then
        printf '%s\n' 'Run installation first: bash tlsbot.sh setup' >&2
        return 1
    fi
}

setup() {
    command -v python3 >/dev/null || { printf '%s\n' 'Install Python 3.10+ first.' >&2; return 1; }
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required")'
    if [[ ! -x "$PYTHON" ]]; then
        python3 -m venv .venv
    fi
    "$PYTHON" -m pip install -r requirements.txt
    ensure_env
    printf '%s\n' 'Python setup complete. Next: edit configuration and check Telegram.'
}

browser_install() {
    require_python
    # Deliberately omit --with-deps: it invokes apt-get and fails on Arch Linux.
    "$PYTHON" -m playwright install chromium
    printf '%s\n' 'Browser downloaded. Run browser-test to check system libraries.'
}

browser_test() {
    require_python
    "$PYTHON" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    try:
        page = browser.new_page()
        page.set_content('<title>Browser works!</title>')
        print(page.title())
    finally:
        browser.close()
PY
}

edit_env() {
    ensure_env
    # Do not source .env: dotenv values are data, not shell commands.
    if command -v nano >/dev/null; then
        nano .env
    elif command -v vi >/dev/null; then
        vi .env
    else
        printf 'Open this file in your editor: %s/.env\n' "$PWD"
    fi
}

find_id() {
    require_python
    ensure_env
    "$PYTHON" - <<'PY'
import asyncio
import logging
import os
import secrets
from dotenv import load_dotenv
from telegram import Bot

logging.disable(logging.CRITICAL)
load_dotenv('.env')
token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
if not token or token.startswith('REPLACE_'):
    raise SystemExit('Save TELEGRAM_BOT_TOKEN in .env first.')
phrase = 'visa-setup-' + secrets.token_hex(8)
print('Stop any running bot instance first (Ctrl+C in its terminal).')
print('Send this exact message to your own bot in a PRIVATE Telegram chat:')
print(phrase)
input('After sending it, press Enter here: ')

async def main():
    async with Bot(token) as bot:
        # No offset: do not confirm/discard unrelated pending updates.
        updates = await bot.get_updates(timeout=5, limit=100)
        ids = {u.message.from_user.id for u in updates
               if u.message and u.message.from_user
               and u.message.chat.type == 'private'
               and u.message.text == phrase}
        if len(ids) == 1:
            user_id = ids.pop()
            print(f'Your Telegram user ID: {user_id}')
            print(f'Save this line in .env: ALLOWED_TELEGRAM_USER_ID={user_id}')
        else:
            print('No unique matching message found. Check the bot/chat and retry.')
            return 1
    return 0

try:
    result = asyncio.run(asyncio.wait_for(main(), timeout=30))
except Exception as exc:
    print(f'ID check failed ({type(exc).__name__}). Check token, network, and other bot instances.')
    result = 1
raise SystemExit(result)
PY
}

usage() {
    cat <<'HELP'
Usage: bash tlsbot.sh [command]

No command opens the menu.
  setup          Create .venv, install Python packages, create .env if absent
  edit           Edit private configuration
  check          Verify Telegram token (no messages sent)
  user-id        Find your personal Telegram ID using a unique message
  telegram       Run Telegram-only mode (/start and /status)
  agency         Save/select clients in chat; appointment checks disabled
  agency-monitor Check the selected client (requires verified portal settings)
  browser        Download Playwright Chromium (Arch-compatible install command)
  browser-test   Launch a local test page; no TLScontact login
  validate       Validate full monitor configuration
  monitor        Run full visa monitoring (requires verified portal settings)
  test           Run automated tests
  help           Show this help

Keep the terminal open while running telegram/monitor. Ctrl+C stops the bot.
Run only one bot instance at a time. Never put credentials in this script.
HELP
}

run_command() {
    case "$1" in
        setup) setup ;;
        edit) edit_env ;;
        browser) browser_install ;;
        browser-test) browser_test ;;
        user-id) find_id ;;
        check) require_python; "$PYTHON" check_telegram.py ;;
        telegram) require_python; "$PYTHON" main.py --telegram-only ;;
        agency) require_python; "$PYTHON" main.py --agency --telegram-only ;;
        agency-monitor) require_python; "$PYTHON" main.py --agency ;;
        monitor) require_python; "$PYTHON" main.py ;;
        test) require_python; "$PYTHON" -m unittest discover -s tests -v ;;
        validate)
            require_python
            "$PYTHON" - <<'PY'
from config import load_config
try:
    load_config()
except ValueError as exc:
    raise SystemExit(f'Configuration error: {exc}')
print('Configuration validates. Live portal selectors still need browser verification.')
PY
            ;;
        help|-h|--help) usage ;;
        *) printf 'Unknown command: %s\n' "$1" >&2; usage; return 2 ;;
    esac
}

if (( $# > 1 )); then
    usage
    exit 2
elif (( $# == 1 )); then
    run_command "$1"
    exit
fi

while true; do
    cat <<'MENU'

TLScontact bot — local setup and testing
  1) Install Python dependencies
  2) Edit .env
  3) Check Telegram connection
  4) Find my Telegram user ID
  5) Run Telegram-only mode
  6) Download Chromium
  7) Test Chromium
  8) Validate full monitor configuration
  9) Run full visa monitor
 10) Run automated tests
 11) Agency: enter and select clients (no appointment checks)
 12) Agency: check the selected client
  0) Exit
MENU
    read -r -p 'Choose an option: ' choice || exit 0
    case "$choice" in
        1) action=setup ;; 2) action=edit ;; 3) action=check ;;
        4) action=user-id ;; 5) action=telegram ;; 6) action=browser ;;
        7) action=browser-test ;; 8) action=validate ;; 9) action=monitor ;;
        10) action=test ;; 11) action=agency ;; 12) action=agency-monitor ;; 0) exit 0 ;;
        *) printf '%s\n' 'Choose a number from the menu.'; continue ;;
    esac
    # A separate Bash process retains fail-fast behavior, while the menu survives errors.
    if bash "$PWD/tlsbot.sh" "$action"; then
        :
    else
        status=$?
        printf 'Command stopped (exit %s). Read the message above.\n' "$status"
    fi
 done
