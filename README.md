# TLScontact Appointment Notifier

A private Telegram bot that checks a configured TLScontact appointment calendar and alerts one authorized operator about matching dates. The current application uses Python, Playwright Chromium, and `python-telegram-bot`. Agency mode stores encrypted client accounts and checks one selected client at a time.

**Entry point:** `main.py`, usually launched through `tlsbot.sh`. The older `Dumbh_appointment_bot.py` is a separate Selenium prototype with different behavior; it is not part of the current application.

## How it works

```text
.env → validated configuration → private Telegram command handlers
                   │
                   ├─ Single account: credentials and deadline from .env
                   └─ Agency: credentials and deadline from selected client
                                      │
                                      ↓
                           fresh Chromium context
                                      ↓
                       log in → load calendar → read dates
                                      ↓
                      today ≤ appointment date < deadline
                                      ↓
                     send new dates → persist alert history
                                      ↓
                         wait with jitter / failure backoff
```

The current monitor reads dates without clicking appointment slots, reserving places, making payments, or completing bookings. Alerts contain a date and login link; the operator books manually. Calendar selectors must be configured for the actual account and portal.

## Files

| File | Role |
| --- | --- |
| [main.py](main.py) | CLI entry point, application lifecycle, single-account scheduler, and persistent alert history. |
| [config.py](config.py) | Configuration models, environment loading, input validation, and URL restrictions. |
| [monitor.py](monitor.py) | Browser login, challenge/error detection, calendar reading, and date filtering. |
| [bot.py](bot.py) | Telegram application, access gate, help, status, and error handlers. |
| [clients.py](clients.py) | Encrypted client storage, selection, per-client alert history, and process locking. |
| [agency_chat.py](agency_chat.py) | Guided client-entry conversation and client-management commands. |
| [agency_monitor.py](agency_monitor.py) | Scheduler for the selected client, cancellation on selection changes, and shared cooldown. |
| [check_telegram.py](check_telegram.py) | Checks the configured Telegram token with `get_me()`; sends no messages. |
| [check_browser.py](check_browser.py) | Starts Chromium and renders local HTML without visiting a portal. |
| [tlsbot.sh](tlsbot.sh) | Local command launcher and interactive setup menu. |
| [install_ubuntu.sh](install_ubuntu.sh) | Installs dependencies, project files, browser, and systemd service on Ubuntu. |
| [tlsbot.service](tlsbot.service) | Service user, startup command, state paths, restart policy, and filesystem restrictions. |
| [requirements.txt](requirements.txt) | Pinned direct Python dependencies for the current application. |
| [.env.example](.env.example) | Shareable configuration template containing placeholders and generic defaults. |
| `.env` | Local settings; ignored by Git. Personal values have been replaced with placeholders. |
| [.gitignore](.gitignore) | Excludes `.env`, virtual environments, caches, browser downloads, state, cookies, encryption keys, and locks. |
| [tests/test_core.py](tests/test_core.py) | Date boundaries, backoff, persistence, configuration, authorization, and delivery-failure tests. |
| [tests/test_agency.py](tests/test_agency.py) | Encryption, locking, conversations, confirmation, deletion, access, client switching, and deduplication tests. |
| [tests/test_browser.py](tests/test_browser.py) | Four opt-in Chromium tests using local HTML. |
| [tests/test_release.py](tests/test_release.py) | Configuration edge cases, corrupt state, help text, retry timing, expiration, and lifecycle tests. |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Ubuntu/systemd deployment and operating instructions. Its dated hosting/pricing information is separate from local verification. |
| [Dumbh_appointment_bot.py](Dumbh_appointment_bot.py) | Legacy Selenium prototype; see its limitations below. |
| `README.md` | This setup and implementation guide. |

Runtime directories such as `.venv/`, `browsers/`, and `state/` are not application source. Agency records use `clients.enc`, `clients.key`, and a `clients.lock` file; single-account alert history uses `alerts.json`.

## Setup

Use Python 3.10+ on Linux. The launcher uses Bash, agency locking uses `fcntl`, and the application installs Unix signal handlers; native Windows execution is not implemented by this setup.

From this directory:

```bash
bash tlsbot.sh setup
bash tlsbot.sh edit
```

`setup` creates `.venv`, installs the pinned dependencies, and creates `.env` from `.env.example` only when `.env` does not already exist. It preserves an existing file. Replace the `REPLACE_*` strings locally before starting the application. `.env` is data read by python-dotenv; do not execute it as a shell script.

For browser monitoring:

```bash
bash tlsbot.sh browser
bash tlsbot.sh browser-test
```

The browser command downloads Chromium without installing system libraries. The Ubuntu installer uses Playwright's `--with-deps` option for those libraries; the local launcher deliberately omits it. Successful local browser startup does not validate TLScontact login or selectors.

The equivalent basic Python setup is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

The dependencies provide browser automation (`playwright`), browser initialization adjustments (`playwright-stealth`), Telegram polling/handlers (`python-telegram-bot`), environment-file loading (`python-dotenv`), and Fernet encryption (`cryptography`). Stealth initialization does not guarantee portal access.

## Run modes and commands

| Launcher command | Python equivalent or action |
| --- | --- |
| `bash tlsbot.sh` | Opens the interactive menu. |
| `bash tlsbot.sh setup` | Creates the environment and installs dependencies. |
| `bash tlsbot.sh edit` | Opens `.env` in nano or vi, or prints its path. |
| `bash tlsbot.sh check` | Runs `check_telegram.py`; requires a real bot token. |
| `bash tlsbot.sh user-id` | Generates a unique phrase, asks you to send it to your bot, and finds your personal ID from matching updates. |
| `bash tlsbot.sh telegram` | `main.py --telegram-only`: Telegram commands, no portal checks. |
| `bash tlsbot.sh monitor` | `main.py`: single-account monitoring. |
| `bash tlsbot.sh agency` | `main.py --agency --telegram-only`: manage clients without portal checks. |
| `bash tlsbot.sh agency-monitor` | `main.py --agency`: monitor the selected client. |
| `bash tlsbot.sh browser` | Downloads Playwright Chromium. |
| `bash tlsbot.sh browser-test` | Launches Chromium against local HTML. |
| `bash tlsbot.sh validate` | Validates full single-account configuration; does not log in. |
| `bash tlsbot.sh test` | Runs unittest discovery; browser cases skip unless explicitly enabled. |
| `bash tlsbot.sh help` | Lists launcher commands. |

Keep the process running to receive commands and perform checks. Ctrl+C stops it. Use one active process per bot token; duplicate pollers can cause a Telegram conflict. Stop a running poller before using the user-ID lookup tool.

## Environment variables

Existing process environment variables take precedence over `.env` because loading uses `override=False`. This allows systemd to enforce its own state paths and headless mode.

| Variable | Expected value and use |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Your bot token; required in every run mode. |
| `ALLOWED_TELEGRAM_USER_ID` | Your positive numeric personal Telegram ID. The example ID and the bot's own ID are rejected. |
| `TLS_EMAIL` | Single-account TLScontact email; not required for agency or Telegram-only mode. |
| `TLS_PASSWORD` | Single-account password; significant leading/trailing spaces are preserved. |
| `TARGET_DATE` | Future `YYYY-MM-DD` deadline, exclusive. Single-account mode only. |
| `TLS_LOGIN_URL` | HTTPS TLScontact login-form URL; required when monitoring. Agency mode restricts it to the France portal hosts accepted by the validator. |
| `TLS_APPOINTMENT_URL` | Single-account authenticated calendar URL. Avoid one-time authentication links. |
| `TLS_EMAIL_SELECTOR` | Locator for the email input. |
| `TLS_PASSWORD_SELECTOR` | Locator for the password input. |
| `TLS_SUBMIT_SELECTOR` | Locator for the login submission control. |
| `TLS_COOKIE_SELECTOR` | Optional cookie-consent control; blank disables this step. |
| `TLS_AUTHENTICATED_SELECTOR` | Visible element proving successful login. |
| `TLS_READY_SELECTOR` | Visible indication that calendar data has finished loading. |
| `TLS_SLOT_SELECTOR` | Locator identifying available slots containing a complete date. |
| `TLS_EMPTY_SELECTOR` | Explicit no-appointments indication. |
| `SLOT_DATE_ATTRIBUTE` | Attribute containing the date, default `data-date`; blank reads the slot's text. |
| `SLOT_DATE_FORMAT` | Python `strptime` format containing a complete date, default `%Y-%m-%d`. |
| `CHECK_INTERVAL` | Minimum wait in seconds, at least `900`; default 15 minutes. |
| `MAX_BACKOFF` | Delay cap in seconds, at least twice the interval; default `21600` (6 hours). |
| `BROWSER_TIMEOUT_MS` | Browser operation/calendar wait timeout, minimum `1000`, default `45000`. |
| `HEADLESS` | `true` or `false`; default `true`. Use a desktop for visible-browser checks. |
| `STATE_FILE` | Single-account date history path; template uses `state/alerts.json`. |
| `CLIENTS_FILE` | Encrypted agency store; template uses `state/clients.enc`. |
| `CLIENTS_KEY_FILE` | Agency encryption key; template uses `state/clients.key`. |

Template paths are relative to the working directory; the shell launcher changes to the project directory. Without explicit client paths, the application derives them from the directory containing `STATE_FILE`.

Placeholders intentionally fail validation. Sanitizing `.env` does not leave the bot ready to connect: supply your own local credentials and verified portal settings when you want to run it again.

## Telegram interface

`bot.py` installs an access handler in group `-100`, before normal command dispatch. The sender must match the configured operator ID, and the chat must be that operator's private chat. Unauthorized users, groups, and updates missing the required identity are stopped before reaching command handlers.

| Command | Behavior |
| --- | --- |
| `/start`, `/help` | Explain the current mode and how to use alerts. |
| `/status` | Show the latest health message; in agency mode, also show the selected client. |
| `/addclient` | Agency: begin a new client draft. |
| `/clients` | Agency: list saved labels, deadlines, IDs, and selection commands. |
| `/use CLIENT_ID` | Agency: select a non-expired client. |
| `/pause` | Agency: clear selection while retaining saved clients. |
| `/delete CLIENT_ID` | Agency: request deletion, requiring `/confirm`. |
| `/confirm` | Agency: save a reviewed draft or confirm deletion. |
| `/cancel` | Agency: discard an unfinished operation. |

Client entry asks for a label, email, password, future deadline, and calendar URL, in that order. Drafts live in memory, expire after ten minutes of inactivity, and are not automatically selected when saved. To change stored credentials or a deadline, create a replacement entry, select it, and delete the old entry.

Agency mode restricts calendar URLs to the configured France portal family, and the monitor requires the calendar and login hostnames to match exactly. Host checks do not establish that the URL belongs to the intended Yerevan applicant; verify that yourself during setup.

## Code walkthrough

### `config.py`: loading and validation

`Config` stores full monitoring settings; `TelegramConfig` stores only the bot token and operator ID. Both dataclasses disable generated field representations to avoid exposing credentials through their default `repr`.

`load_telegram_config()` loads dotenv values and validates token presence and personal user ID. `load_config()` extends this with credentials, a future deadline, browser selectors, timing, headless mode, date format, and URLs. Its nested `required()`, `integer()`, and `url()` helpers reject placeholders, malformed numbers, and invalid addresses. In agency mode, account-specific fields are left for the selected client.

`validate_portal_url()` accepts HTTPS TLScontact subdomains, rejects embedded URL credentials, whitespace, and non-HTTPS ports. `validate_france_url()` further limits the hostname to `visas-fr.tlscontact.com` or its `public` subdomain. `FRANCE_CENTRE_URL` supplies the centre link used in client-entry instructions.

### `monitor.py`: browser and calendar

`PortalError` represents a recognized calendar/access failure. `qualifying_dates()` parses complete dates, deduplicates and sorts them, then keeps dates from today in `Asia/Yerevan` through the day before the deadline. Invalid slot dates raise an error instead of silently disappearing.

`check_slots()` creates a new browser context for every check with English locale and Yerevan timezone. It watches document/XHR/fetch responses for HTTP 403 or 429. Its nested `guard()` also checks visible CAPTCHA frames and one-time-code inputs. The browser fills credentials, submits login, waits for proof of authentication, navigates to the calendar, and delegates to `read_calendar()`. A `finally` block closes Chromium even when the check fails.

`read_calendar()` waits for the ready selector, then inspects matching slots. A slot must be visible, enabled, and not `aria-disabled="true"`. It reads the configured attribute or text and passes values to `qualifying_dates()`. An explicit empty-calendar marker returns an empty list. Otherwise, it retries every half second until its timeout and raises `PortalError`. It never clicks a slot.

The adapter reads only the loaded calendar. Pagination, embedded frames, intermediate applicant steps, or selecting a date to reveal appointment times require additional implementation for the actual portal. Times of day are not extracted. CAPTCHA/MFA and unexpected pages fail the check; the code does not solve them or reuse a manually authenticated session.

### `main.py`: persistence and scheduling

| Function or class | Implementation |
| --- | --- |
| `retry_delay()` | Computes a random delay between a failure-adjusted floor and twice that floor, bounded by `MAX_BACKOFF`. The floor doubles with consecutive failures. |
| `AlertStore.__init__()` | Loads a JSON list of notified dates into a set. Missing files start empty; corrupt state fails rather than silently resetting history. |
| `AlertStore.record()` | Writes updated history to a restricted temporary file, flushes and fsyncs it, then atomically replaces the destination before updating memory. |
| `monitor_loop()` | Checks the deadline, runs each browser check with a five-minute overall timeout, sends unsent dates, records successful deliveries, updates status, and waits. Telegram `RetryAfter` can extend the wait. |
| `run_application()` | Creates the bot, starts polling, launches the appropriate monitor task, watches shutdown/fatal errors, and cancels tasks and stops polling on exit. |
| `run()` | Chooses single-account operation or opens the agency store, guaranteeing its lock is closed afterward. |
| `main()` | Parses `--telegram-only` and `--agency`, sets restrictive file permissions and logging, loads configuration, and starts asyncio. Configuration errors exit with code 2; runtime failures exit with code 1. |

The first check happens at startup when eligible. Successful checks normally wait 15–30 minutes with default settings. Checks run serially. Once the single-account deadline is reached, browser checks pause until configuration is updated and the process restarted.

Dates are recorded only after Telegram accepts the message. Failed sends remain eligible for retry. A crash between delivery and persistence can still cause a duplicate; a date already recorded will not alert again merely because it disappears and reappears.

`run_application()` drops pending Telegram updates on startup. Its polling error handler treats conflicts and invalid tokens as fatal; an unexpectedly stopped monitor also terminates the application so the service can restart it.

### `bot.py`: handlers

`build_bot()` creates an application with sequential update handling. Its nested `authorize()` implements the global access gate, `start()` serves both help and welcome messages, `status()` reports single-account or selected-client health, and `on_error()` logs exception types without raw exception messages. With a client store, it registers `AgencyChat`, a selection-change event, and per-client health state.

### `clients.py`: encrypted state

| Method | Responsibility |
| --- | --- |
| `__init__()` | Creates restricted directories, takes a nonblocking exclusive process lock, creates a key only for a new store, decrypts existing data, and initializes a revision counter. |
| `_validate()` | Checks format version, selected-client consistency, record types, dates, histories, and the 100-client limit. |
| `_save()` | Encrypts JSON with Fernet, writes/fsyncs a temporary file, atomically replaces the store, then updates in-memory data. |
| `add()` | Generates a 12-character client ID and saves an account with empty alert history. |
| `get()` | Returns a deep copy so callers cannot mutate stored data accidentally. |
| `selected` | Returns the currently selected ID. |
| `summaries()` | Returns IDs, labels, and deadlines without credentials. |
| `select()` | Persists a selection or pause and increments the revision. |
| `delete()` | Removes a client and its history, clears its selection if necessary, and increments the revision. |
| `record()` | Persists a sent date for one client without duplicates. |
| `close()` | Closes the lock file, releasing the process lock. |

Losing the encryption key makes existing records unreadable. The implementation refuses to silently replace a missing key when encrypted data already exists. Encryption does not protect records from someone who can read both the store and its key.

### `agency_chat.py`: conversation state

`today()` returns the current Yerevan date. `AgencyChat.__init__()` holds the store, draft, and expiry timer. `clear()` discards the draft and timer; `renew()` resets the ten-minute expiry; `erase()` attempts to delete incoming details and warns if deletion fails.

`guide()` displays instructions. `add()` starts the draft, and `receive()` validates each field and advances its stage while preserving password whitespace. `confirm()` saves a reviewed draft or deletes a confirmed client; `cancel()` discards the draft. `clients()` sends summaries in batches of 15. `use()` selects a valid, unexpired entry; `pause()` clears selection; `delete()` creates a confirmation draft. Selection/deletion actions signal the monitoring loop. `register()` installs these command handlers and the text-message receiver.

Telegram bot chats are not end-to-end encrypted. Message deletion is best-effort and does not erase notifications, screenshots, backups, or provider-side copies. Only enter client details with permission; removing a client removes its local record, not all external copies. Details sent while the bot is offline may be discarded on startup without being deleted from the chat.

### `agency_monitor.py`: selected-client scheduler

`agency_loop()` waits when no client is selected or its deadline has expired. For an eligible client, it uses `dataclasses.replace()` to combine shared portal settings with that client's credentials, calendar URL, and deadline.

It runs the browser check alongside a wait for the selection-change event. Switching, pausing, or deleting changes the selection/revision, cancels obsolete checks, and prevents subsequent alerts from those results. An alert already being sent can still arrive. The cooldown and failure backoff are shared across switches, preventing selection changes from forcing rapid polling. Each client retains separate notification history.

### Helper scripts, launcher, and service

`check_telegram.check()` loads the token and calls Telegram's `get_me()`, reporting success or an exception type; it does not start handlers. `check_browser.main()` launches Chromium, sets a local page title, asserts the title, and closes the browser.

The launcher's `ensure_env()` creates missing configuration and sets mode 0600; `require_python()` verifies `.venv`; `setup()` installs packages; `browser_install()` and `browser_test()` handle browser setup; `edit_env()` opens an editor; `find_id()` matches a random setup phrase to one private sender; `usage()` prints help; `run_command()` dispatches commands. The interactive menu runs each action in a child Bash process so an action failure does not close the menu.

`install_ubuntu.sh` checks OS/architecture, refuses to overwrite a running installation, installs dependencies and files under `/opt/tlsbot`, creates the `tlsbot` service user, preserves existing configuration/state, installs Chromium, writes mode overrides, and validates the service. It starts/enables the service only with `--start`. Running it changes the host; it is not a local verification command.

`tlsbot.service` runs as `tlsbot`, forces headless mode, stores state under `/var/lib/tlsbot`, restricts filesystem access, and restarts failed processes except configuration failures with exit code 2. See [DEPLOYMENT.md](DEPLOYMENT.md) for deployment commands. This review did not install or start a service.

### Legacy `Dumbh_appointment_bot.py`

This file imports Selenium, uses a hard-coded ChromeDriver path and a different portal URL, and has empty credential/token fields. `human_like_delay()` blocks with random sleeps; `start()` and `info()` reply with text; `echo()` repeats messages; `search_appointments()` repeatedly opens Chrome, logs in, writes cookies to `cookies.pkl`, navigates booking controls, and may click an available appointment. `main()` registers `/search` and starts polling.

It lacks the current operator access gate, encrypted agency store, date filtering, and scheduler. Its blocking Selenium/sleep calls run inside an async handler, and Selenium is not included in the current requirements. Treat it as historical source, not an alternative launcher for the documented notification-only application.

## Verification

```bash
bash -n tlsbot.sh install_ubuntu.sh
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
RUN_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_browser.py -v
```

Local review on **2026-10-17**:

- 29 non-browser tests passed; default discovery skipped the four opt-in browser tests.
- All four browser tests passed separately against local HTML in Chromium.
- Dependency consistency, launcher/installer shell syntax, and CLI help checks passed.
- Fixed a misspelled `config` import and extracted the calendar reader used by both production code and browser tests.
- Replaced personal configuration with descriptive placeholders in `.env` and `.env.example`.

The browser tests verify visible/enabled slot filtering without clicks, explicit empty calendars, unknown-page errors, and invalid-date errors. They do not log into TLScontact or send Telegram messages. Live token delivery, real portal selectors, account authentication, and Ubuntu service deployment remain unverified by these local checks.
