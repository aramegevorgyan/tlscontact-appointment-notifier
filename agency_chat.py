"""Operator-only guided client entry. The global bot gate runs before these handlers."""
import asyncio
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from telegram.ext import CommandHandler, MessageHandler, filters
from config import FRANCE_CENTRE_URL, validate_france_url


def today():
    return datetime.now(ZoneInfo('Asia/Yerevan')).date()


class AgencyChat:
    def __init__(self, store):
        self.store = store
        self.draft = None
        self.timer = None

    def clear(self):
        self.draft = None
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def renew(self):
        if self.timer:
            self.timer.cancel()
        self.timer = asyncio.get_running_loop().call_later(600, self.clear)

    async def erase(self, message):
        try:
            await message.delete()
        except Exception:
            await message.reply_text('I could not remove your last message. Please delete it from this chat yourself.')

    async def guide(self, update, context):
        mode = ('Appointment checks are OFF right now. You can save client details, '
                'but I will not send appointment alerts yet.\n\n'
                if context.bot_data.get('telegram_only') else
                'I check one selected client at a time. Use /status to see the latest check.\n\n')
        await update.effective_message.reply_text(
            'France visa appointments — Yerevan\n\n' + mode +
            'You can give me the client’s email and password right here in this chat. '
            'Only do this with the client’s permission.\n\n'
            '1. Tap the message box, type /addclient, and press Send.\n\n'
            '2. Wait for each question. Send ONE answer, then wait for my next question:\n'
            '• Client name: send a short name or reference so you can recognise them.\n'
            '• Email: send the email they use to sign in to TLScontact.\n'
            '• Password: send their TLScontact password only when I ask for it.\n'
            '• Latest date: send it as year-month-day, for example 2027-06-01. '
            'I will look for appointments BEFORE that date.\n'
            '• Calendar link: open France TLScontact in your browser, choose Yerevan, '
            'sign in to this client’s account, and open their appointment calendar. '
            'Copy the address from the browser’s address bar and send it here.\n\n'
            '3. When I ask you to save, send /confirm. '
            'If you made a mistake, send /cancel and start again with /addclient.\n\n'
            '4. Send /clients. Find the client you want, copy the whole /use command '
            'shown under their name, and send it back to me. '
            'Saving a client does not select them automatically. Send /status to check.\n\n'
            '5. When appointment checks are active, wait for my message. '
            'Keep notifications for this chat turned on. '
            'If I find an appointment, I send the client’s name, date, and a link. '
            'Tap that link, sign in, and book on TLScontact yourself. '
            'I do not reserve a place; someone else may take it before you book.\n\n'
            'For another client, send /addclient again. To switch clients, send /clients '
            'and use the command under the next client’s name. Only the selected client is checked.\n\n'
            '/status — see who is selected and whether checks are working\n'
            '/pause — stop checking; keep saved clients\n'
            '/cancel — discard unfinished answers\n'
            '/delete CLIENT_ID — remove a saved client; I will ask you to confirm\n'
            '/help — read these instructions again\n\n'
            'Never send passports or payment-card details here. Bot chats are not end-to-end '
            'encrypted. I try to delete your account-detail messages and encrypt saved details. '
            'If I cannot delete a message, I will ask you to delete it yourself.')

    async def add(self, update, context):
        self.clear()
        self.draft = {'stage': 'label'}
        self.renew()
        await update.effective_message.reply_text(
            'Only continue with your client’s permission. Send one answer at a time. '
            'Use /cancel to stop. Unfinished entry expires after 10 minutes without an answer.\n\n'
            'First, send a short name or reference for this client. Do not send passport details.')

    async def receive(self, update, context):
        message = update.effective_message
        raw = message.text
        # Do not leave accidental credentials in chat, including after a timeout/restart.
        await self.erase(message)
        if not self.draft or self.draft['stage'] in ('review', 'delete'):
            await message.reply_text('Send /confirm or /cancel if I asked for confirmation. Otherwise send /addclient to enter a client.')
            return
        self.renew()
        stage = self.draft['stage']
        value = raw.strip()
        if stage == 'label':
            if not value or len(value) > 50 or any(ord(char) < 32 for char in value):
                await message.reply_text('Send a short name on one line (up to 50 characters).')
                return
            self.draft.update(label=value, stage='email')
            prompt = 'Send the email address used to sign in to this client’s TLScontact account.'
        elif stage == 'email':
            if len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                await message.reply_text('That does not look like an email address. Please try again.')
                return
            self.draft.update(email=value, stage='password')
            prompt = ('Now send only the TLScontact password. This bot chat is not end-to-end encrypted. '
                      'I will try to delete your message immediately. Use /cancel if you do not want to send it.')
        elif stage == 'password':
            if not raw or len(raw) > 1024 or '\n' in raw:
                await message.reply_text('Send the password on one line, or /cancel to stop.')
                return
            self.draft.update(password=raw, stage='date')
            prompt = 'Send the latest date in YYYY-MM-DD format, for example 2027-06-01. I will look only for appointments before that date.'
        elif stage == 'date':
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or date.fromisoformat(value) <= today():
                    raise ValueError
            except ValueError:
                await message.reply_text('Send a future date in YYYY-MM-DD format.')
                return
            self.draft.update(target_date=value, stage='calendar')
            prompt = (f'Open the France TLScontact Yerevan page: {FRANCE_CENTRE_URL}\n'
                      'Sign in to this client’s account and open their '
                      'appointment calendar. Copy that page’s website address and send it here. '
                      'Do not send the public homepage or a one-time sign-in link.')
        else:
            try:
                value = validate_france_url(value, 'Calendar address')
            except ValueError:
                await message.reply_text('Send this client’s HTTPS calendar address on visas-fr.tlscontact.com, for France in Yerevan.')
                return
            self.draft.update(appointment_url=value, stage='review')
            prompt = (f'Client: {self.draft["label"]}\nAppointments before: {self.draft["target_date"]}\n\n'
                      'Email and password received; I will not display them. '
                      'Send /confirm to save and confirm you have permission to use this account, or /cancel to discard. '
                      'The account has not been checked yet.')
        await message.reply_text(prompt)

    async def confirm(self, update, context):
        if not self.draft or self.draft['stage'] not in ('review', 'delete'):
            await update.effective_message.reply_text('There is nothing to confirm. Send /addclient to begin.')
            return
        draft = self.draft
        self.clear()
        if draft['stage'] == 'delete':
            self.store.delete(draft['id'])
            context.bot_data['client_changed'].set()
            await update.effective_message.reply_text('Client removed from this bot. Existing Telegram messages and backups are not removed.')
        else:
            key = self.store.add(*(draft[name] for name in
                                  ('label', 'email', 'password', 'target_date', 'appointment_url')))
            await update.effective_message.reply_text(
                f'Client saved. To select this client, send:\n/use {key}\n\n'
                'Saving does not verify the login or start checking. Send /status after selecting.')

    async def cancel(self, update, context):
        self.clear()
        await update.effective_message.reply_text('Entry cancelled. No unfinished details were saved. Your selected client is unchanged.')

    async def clients(self, update, context):
        rows = self.store.summaries()
        if not rows:
            await update.effective_message.reply_text('No saved clients. Send /addclient to add the first one.')
            return
        # Small batches stay below Telegram's message limit; never include credentials.
        for start in range(0, len(rows), 15):
            lines = [f'{label} — before {target}' + (' — selected' if key == self.store.selected else '') + f'\n/use {key}'
                     for key, label, target in rows[start:start + 15]]
            await update.effective_message.reply_text('\n\n'.join(lines))

    async def use(self, update, context):
        if len(context.args) != 1 or self.store.get(context.args[0]) is None:
            await update.effective_message.reply_text('Send /clients, then copy the /use command for the client you want.')
            return
        client = self.store.get(context.args[0])
        if date.fromisoformat(client['target_date']) <= today():
            await update.effective_message.reply_text('This client’s latest date has passed. Add a new entry with a future date.')
            return
        self.store.select(context.args[0])
        context.bot_data['client_changed'].set()
        suffix = ('Appointment checks are OFF in this session; no alerts will be sent yet.'
                  if context.bot_data.get('telegram_only') else
                  'The previous client is no longer selected. Checks respect the normal waiting period.')
        await update.effective_message.reply_text(f'Selected: {client["label"]}.\n{suffix}')

    async def pause(self, update, context):
        self.store.select(None)
        context.bot_data['client_changed'].set()
        await update.effective_message.reply_text('Checks paused. Saved clients remain available. Send /clients to select one again.')

    async def delete(self, update, context):
        if len(context.args) != 1 or self.store.get(context.args[0]) is None:
            await update.effective_message.reply_text('Send /delete followed by the client ID shown in /clients.')
            return
        self.clear()
        self.draft = {'stage': 'delete', 'id': context.args[0]}
        self.renew()
        await update.effective_message.reply_text('Remove this client’s saved login and alert history? Send /confirm to remove, or /cancel to keep.')

    def register(self, app):
        for command, handler in [('addclient', self.add), ('clients', self.clients), ('use', self.use),
                                 ('pause', self.pause), ('delete', self.delete),
                                 ('confirm', self.confirm), ('cancel', self.cancel)]:
            app.add_handler(CommandHandler(command, handler))
        app.add_handler(MessageHandler(filters.TEXT, self.receive))
