"""Private Telegram interface with a global, first-dispatched access gate."""
import asyncio
import logging

from telegram import Update
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, TypeHandler

from config import Config, TelegramConfig

logger = logging.getLogger(__name__)


def build_bot(config: Config | TelegramConfig, client_store=None) -> Application:
    app = Application.builder().token(config.token).concurrent_updates(False).build()
    agency_chat = None
    if client_store is not None:
        from agency_chat import AgencyChat
        agency_chat = AgencyChat(client_store)
        app.bot_data['agency_chat'] = agency_chat
        app.bot_data['client_changed'] = asyncio.Event()
        app.bot_data['client_health'] = {}

    async def authorize(update, context):
        user, chat = update.effective_user, update.effective_chat
        if (user is None or user.id != config.user_id or chat is None
                or chat.type != 'private' or chat.id != config.user_id):
            raise ApplicationHandlerStop

    async def start(update, context):
        if agency_chat is not None:
            await agency_chat.guide(update, context)
            return
        if context.bot_data.get('telegram_only'):
            availability = (
                'I am not checking for appointments yet. '
                'Please ask the person who set up this bot to turn on appointment checks.\n\n'
            )
        else:
            availability = (
                f'I am set to look for appointments before {config.target_date:%d %B %Y}.\n\n'
            )
        await update.effective_message.reply_text(
            'Hello! Here is how to use this chat.\n\n' + availability +
            '1. Check whether I am working.\n'
            'Tap the message box at the bottom of this chat. '
            'Type /status and press Send. I will tell you about the latest check. '
            'If the reply says checks are not running or have failed, '
            'ask the person who set up this bot for help.\n\n'
            '2. Wait for a message from me.\n'
            'Make sure this chat is not muted so you can hear new messages. '
            'You can close Telegram and use your phone normally. '
            'When appointment checks are working, I will send you a message '
            'if I find a date that matches your settings.\n\n'
            '3. When I send you a date, tap the website link in that message.\n'
            'It will open TLScontact. Sign in there with your TLScontact email and password. '
            'Open the appointment calendar, choose an available date, '
            'and follow the instructions on the website to finish booking. '
            'Check that the website confirms your booking.\n\n'
            'My message does not book an appointment for you. '
            'Someone else may take the date before you open the website.\n\n'
            'What should you send me?\n'
            '/status — to check whether I am working\n'
            '/help — to read these steps again\n'
            '/start — to see the welcome message\n\n'
            'You do not need to send me any documents or passwords. '
            'I cannot change your preferred dates through this chat; '
            'ask the person who set up this bot to change them.')

    async def status(update, context):
        if client_store is not None:
            key = client_store.selected
            client = client_store.get(key) if key else None
            selected = f'Selected client: {client["label"]} ({key}).' if client else 'No client selected. Send /clients to choose one.'
            health = context.bot_data['client_health'].get(key, 'This client has not been checked in this session.') if client else ''
            mode = 'Appointment checks are OFF.' if context.bot_data.get('telegram_only') else 'One client is checked at a time.'
            await update.effective_message.reply_text(f'{mode}\n{selected}\n{health}')
            return
        await update.effective_message.reply_text(context.bot_data.get('status', 'Starting monitor.'))

    async def on_error(update, context):
        # Exception messages may contain bot tokens or private URLs.
        logger.error('Telegram handler failed (%s)', type(context.error).__name__)

    app.add_handler(TypeHandler(Update, authorize), group=-100)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('help', start))
    if agency_chat is not None:
        agency_chat.register(app)
    app.add_error_handler(on_error)
    return app
