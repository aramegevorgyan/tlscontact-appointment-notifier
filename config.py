"""Validated configuration; secrets are never included in repr/log output."""
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

FRANCE_PORTAL_HOST = 'visas-fr.tlscontact.com'
FRANCE_CENTRE_URL = 'https://visas-fr.tlscontact.com/en-us/country/am/vac/amEVN2fr'


@dataclass(frozen=True, repr=False)
class Config:
    email: str
    password: str
    token: str
    user_id: int
    login_url: str
    appointment_url: str
    target_date: date
    selectors: dict[str, str]
    interval: int = 900
    max_backoff: int = 21600
    timeout: int = 45000
    date_attribute: str = 'data-date'
    date_format: str = '%Y-%m-%d'
    headless: bool = True
    state_file: Path = field(default_factory=lambda: Path('state/alerts.json'))


def load_config(env_file: Path | None = None, *, agency: bool = False) -> Config:
    load_dotenv(env_file or Path(__file__).with_name('.env'), override=False)
    telegram = load_telegram_config(env_file)

    def required(key):
        raw = os.getenv(key, '')
        value = raw if key == 'TLS_PASSWORD' else raw.strip()
        if not value or value.startswith('REPLACE_'):
            raise ValueError(f'{key} must be configured')
        return value

    def integer(key, default, minimum):
        try:
            value = int(os.getenv(key, str(default)))
        except ValueError:
            raise ValueError(f'{key} must be an integer') from None
        if value < minimum:
            raise ValueError(f'{key} must be >= {minimum}')
        return value

    def url(key):
        return validate_portal_url(required(key), key)

    target = date.max
    if not agency:
        try:
            target = date.fromisoformat(required('TARGET_DATE'))
        except ValueError:
            raise ValueError('TARGET_DATE must be YYYY-MM-DD') from None
        if target <= datetime.now(ZoneInfo('Asia/Yerevan')).date():
            raise ValueError('TARGET_DATE must be in the future')
    interval = integer('CHECK_INTERVAL', 900, 900)
    headless = os.getenv('HEADLESS', 'true').lower()
    if headless not in ('true', 'false'):
        raise ValueError('HEADLESS must be true or false')
    date_format = os.getenv('SLOT_DATE_FORMAT', '%Y-%m-%d')
    sample = datetime(2031, 11, 23)
    try:
        if not any(code in date_format for code in ('%Y', '%y', '%G')):
            raise ValueError
        if datetime.strptime(sample.strftime(date_format), date_format).date() != sample.date():
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('SLOT_DATE_FORMAT must parse a complete year, month and day') from None
    selectors = {name: required(f'TLS_{name}_SELECTOR') for name in
                 ('EMAIL', 'PASSWORD', 'SUBMIT', 'AUTHENTICATED', 'READY', 'SLOT', 'EMPTY')}
    selectors['COOKIE'] = os.getenv('TLS_COOKIE_SELECTOR', '').strip()
    login_url = url('TLS_LOGIN_URL')
    if agency:
        validate_france_url(login_url, 'TLS_LOGIN_URL')
    return Config(
        email='' if agency else required('TLS_EMAIL'),
        password='' if agency else required('TLS_PASSWORD'),
        token=telegram.token, user_id=telegram.user_id,
        login_url=login_url,
        appointment_url='' if agency else url('TLS_APPOINTMENT_URL'),
        target_date=target, selectors=selectors, interval=interval,
        max_backoff=integer('MAX_BACKOFF', 21600, interval * 2),
        timeout=integer('BROWSER_TIMEOUT_MS', 45000, 1000),
        date_attribute=os.getenv('SLOT_DATE_ATTRIBUTE', 'data-date'),
        date_format=date_format,
        headless=headless == 'true',
        state_file=Path(os.getenv('STATE_FILE', str(Path(__file__).parent / 'state/alerts.json'))),
    )


@dataclass(frozen=True, repr=False)
class TelegramConfig:
    token: str
    user_id: int


def load_telegram_config(env_file: Path | None = None) -> TelegramConfig:
    """Validate Telegram independently of browser and portal configuration."""
    load_dotenv(env_file or Path(__file__).with_name('.env'), override=False)
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token or token.startswith('REPLACE_'):
        raise ValueError('TELEGRAM_BOT_TOKEN must be configured')
    try:
        user_id = int(os.getenv('ALLOWED_TELEGRAM_USER_ID', '0'))
    except ValueError:
        raise ValueError('ALLOWED_TELEGRAM_USER_ID must be your numeric Telegram user ID') from None
    if user_id <= 0 or user_id == 123456789 or str(user_id) == token.partition(':')[0]:
        raise ValueError('ALLOWED_TELEGRAM_USER_ID must be your own user ID, not the example value or bot ID')
    return TelegramConfig(token, user_id)


def validate_portal_url(value: str, name: str = 'URL') -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError(f'{name} must be a valid HTTPS TLScontact URL') from None
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not host.endswith('.tlscontact.com')
            or parsed.username or parsed.password or port not in (None, 443)
            or any(char.isspace() for char in value)):
        raise ValueError(f'{name} must be an HTTPS TLScontact URL')
    return value


def validate_france_url(value: str, name: str = 'URL') -> str:
    value = validate_portal_url(value, name)
    if urlsplit(value).hostname not in (FRANCE_PORTAL_HOST, 'public.' + FRANCE_PORTAL_HOST):
        raise ValueError(f'{name} must belong to the France TLScontact portal')
    return value
