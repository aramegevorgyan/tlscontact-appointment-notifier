# Ubuntu EC2 deployment

## Release status

Telegram-only deployment is supported. Live appointment monitoring is **not yet
validated**: the current local configuration still has placeholder TLScontact
credentials, URLs, and calendar selectors. The bot must complete a real calendar
check with verified selectors before relying on appointment alerts. Local HTML
browser tests are not a substitute for that check. No software review can rule
out every future portal change, WAF block, or network failure.

## Instance and Free Tier choice (checked 2026-10-15)

For an account with an active new Free plan, AWS lists both t3.micro and t3.small
as eligible. Choose Ubuntu Server 24.04 LTS, x86_64, with a Free Tier eligible AMI.
Prefer **t3.small (2 vCPU, 2 GiB RAM)** for Chromium. t3.micro has 1 GiB: one browser
at 15–30 minute intervals may work, but memory headroom is tight and has not been
benchmarked against your real calendar. Telegram-only mode is much lighter.

The new Free plan ends six months after account creation or when credits run
out, whichever is earlier. Eligibility is not permanent free hosting. Check
Billing → Free Tier and Credits for your exact remaining time/balance. t3.small
uses credits faster than t3.micro. Include EBS storage, public IPv4, and data
transfer in your estimate. Consider Standard CPU credit mode to avoid Unlimited
surplus-credit costs if switching to a paid plan. Set billing notifications before
launching; a budget notification does not stop resources automatically.

Sources:
- https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html
- https://aws.amazon.com/ec2/instance-types/t3/
- https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-launch-parameters.html

A practical starting disk size is 16 GiB gp3. Check its cost against your credits.
Allow inbound SSH (22) only from your IP. No web server, public HTTP/HTTPS listener,
load balancer, or inbound Telegram port is needed. The instance needs working
outbound HTTPS and DNS and an internet route. Avoid a NAT Gateway just for this
single bot; it adds a separate ongoing cost.

## Upload

From your LOCAL project directory, create an archive without the virtual environment,
browser downloads, Git history, alert state, or credentials:

```bash
tar -czf /tmp/tlsbot-release.tar.gz config.py monitor.py bot.py main.py clients.py agency_chat.py agency_monitor.py check_telegram.py check_browser.py requirements.txt .env.example tlsbot.service tlsbot.sh install_ubuntu.sh README.md DEPLOYMENT.md tests
scp -i /path/to/key.pem /tmp/tlsbot-release.tar.gz ubuntu@YOUR_EC2_IP:~/
ssh -i /path/to/key.pem ubuntu@YOUR_EC2_IP
```

On EC2:

```bash
mkdir -p ~/tlsbot-release
tar -xzf ~/tlsbot-release.tar.gz -C ~/tlsbot-release
cd ~/tlsbot-release
sudo bash install_ubuntu.sh --telegram-only
sudoedit /opt/tlsbot/.env
```

Set the real TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_USER_ID. Do not post the file
or token in chat. The installer preserves an existing /opt/tlsbot/.env. If a source
.env is present on the server on first install, it is copied with restricted
permissions; otherwise the template is used. State remains in /var/lib/tlsbot.

Stop your local bot with Ctrl+C before starting the EC2 copy. Then on EC2:

```bash
sudo bash install_ubuntu.sh --telegram-only --start
sudo journalctl -u tlsbot -f
```

The installer installs Python/venv, pinned direct Python dependencies, Chromium,
Ubuntu browser libraries, an unprivileged service account, and the systemd unit.
It validates the selected mode before starting and performs a local browser smoke
test as the service user. It refuses to overwrite a running service installation.
It does not change swap, firewall rules, EC2 infrastructure, or billing settings.

Startup is not a proof of token validity or successful monitoring. Send /start to
the bot for the guide, then /status. Telegram-only mode must explicitly say that
visa monitoring is disabled. Commands sent while the service was stopped are
cleared at startup; send them after it starts. /help repeats the guide.

## Enable full monitoring

After verifying portal URLs/selectors on your desktop, stop the EC2 service and
fill in all remaining settings:

```bash
sudo systemctl stop tlsbot
sudoedit /opt/tlsbot/.env
cd ~/tlsbot-release
sudo bash install_ubuntu.sh --start
sudo journalctl -u tlsbot -f
```

Omitting --telegram-only removes the installer-managed mode override and enables
monitoring. The service enforces HEADLESS=true and uses /var/lib/tlsbot/alerts.json,
regardless of the corresponding .env settings. Keep code/root installation separate
from writable state; the app runs as tlsbot. Confirm /status shows a successful
calendar check and compare its result with your manual portal view. CAPTCHA/MFA
or an EC2 IP block may still prevent login; there is no bypass or session import.

## Operation and updates

```bash
sudo systemctl status tlsbot --no-pager
sudo journalctl -u tlsbot --since '1 hour ago' --no-pager
sudo systemctl restart tlsbot
sudo systemctl stop tlsbot
sudo systemctl disable tlsbot
free -h
sudo systemctl show tlsbot -p MemoryCurrent -p MemoryPeak
```

The service restarts after unexpected process failures, including a stopped
monitor worker, and starts after reboot when enabled. Configuration errors exit
without a restart loop. Polling errors are logged without raw exception contents.
Telegram conflicts generally mean another copy is running: stop that copy.
The guide is displayed only in response to /start or /help, not pushed on every reboot.

Keep journal retention appropriate to disk capacity and monitor EC2 memory/CPU
credits. If memory is exhausted, move to a larger eligible instance within your
remaining credit budget; a real workload measurement is necessary before choosing
1 GiB for production. Once the target date is reached, checks pause; update the
date and restart. Alert history persists over restarts and service reinstalls.
Corrupt history causes startup failure instead of silently losing deduplication:
inspect/restore the file from a backup while the service is stopped.

For updates, upload the new source, stop the service, and rerun the installer with
the desired mode and --start. A running Python process does not load file changes
until restarted. The installer has been syntax-checked locally; its apt/systemd
installation still needs to be exercised on your Ubuntu EC2 instance.

## Agency deployment

Use the same upload procedure, which includes the agency modules. On EC2:

```bash
sudo bash install_ubuntu.sh --agency --telegram-only
sudoedit /opt/tlsbot/.env
# Stop your local copy before starting this service:
sudo bash install_ubuntu.sh --agency --telegram-only --start
```

Send /addclient in your private chat to enter accounts, then /clients and /use.
This mode stores clients but does not access TLScontact. After verifying the shared
portal login URL/selectors, enable checks for the selected client:

```bash
sudo systemctl stop tlsbot
sudo bash install_ubuntu.sh --agency --start
```

Always include --agency when reinstalling or updating an agency service. The
installer preserves /var/lib/tlsbot/clients.enc and clients.key. Both files are
needed to migrate saved clients; the upload archive deliberately excludes them.
For migration, stop both copies, transfer data and key through a private SSH
connection to /var/lib/tlsbot, and set ownership tlsbot:tlsbot and mode 0600 before
starting. Do not mix encrypted data from one installation with a different key.
A previously selected client resumes when monitoring starts; check /status.

The bot is operator-only, even in agency mode. Do not put the client's password in
shell commands, logs, Git, or an uploaded archive. Review README.md's retention and
Telegram chat privacy limitations before using real client accounts.
