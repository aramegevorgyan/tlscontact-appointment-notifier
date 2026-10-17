#!/usr/bin/env bash
# Install on the EC2 instance: sudo bash install_ubuntu.sh [--agency] [--telegram-only] [--start]
set -Eeuo pipefail
umask 022
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/tlsbot
MODE=monitor
AGENCY=false
START=false

usage() {
    cat <<'HELP'
Usage: sudo bash install_ubuntu.sh [--agency] [--telegram-only] [--start]

Ubuntu 22.04/24.04/26.04, x86_64 or aarch64. Installs Python, Chromium,
system libraries, project files, and a systemd service under /opt/tlsbot.
Existing /opt/tlsbot/.env and /var/lib/tlsbot are preserved.
Default: full monitoring, installed but not enabled/started.
--agency         Enable client entry in chat and one selected client.
--telegram-only  Configure the service to run Telegram without TLScontact.
--start          Validate configuration, enable at boot, and start the service.

Stop an existing EC2 service before reinstalling. Stop your local bot before
starting the EC2 copy. No inbound application port is required.
HELP
}
for arg in "$@"; do
    case "$arg" in
        --agency) AGENCY=true ;;
        --telegram-only) MODE=telegram ;;
        --start) START=true ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$arg" >&2; usage; exit 2 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo 'Run on Ubuntu with sudo bash install_ubuntu.sh'; exit 1; }
# OS metadata supplied by the operating system, never source application .env.
source /etc/os-release
[[ "$ID" == ubuntu ]] || { echo 'This installer is for Ubuntu only; use tlsbot.sh on Arch.'; exit 1; }
case "$VERSION_ID" in 22.04|24.04|26.04) ;; *) echo 'Use Ubuntu 22.04, 24.04, or 26.04.'; exit 1 ;; esac
case "$(uname -m)" in x86_64|aarch64) ;; *) echo 'Unsupported CPU architecture.'; exit 1 ;; esac
command -v systemctl >/dev/null || { echo 'systemd is required.'; exit 1; }
if systemctl is-active --quiet tlsbot.service; then
    echo 'Service is running. Stop it with sudo systemctl stop tlsbot before reinstalling.'
    exit 1
fi
FILES=(config.py monitor.py bot.py main.py clients.py agency_chat.py agency_monitor.py check_telegram.py check_browser.py requirements.txt .env.example tlsbot.service tlsbot.sh install_ubuntu.sh README.md DEPLOYMENT.md)
for file in "${FILES[@]}"; do
    [[ -f "$SOURCE_DIR/$file" ]] || { printf 'Missing project file: %s\n' "$file"; exit 1; }
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv ca-certificates tzdata
if ! id tlsbot >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/tlsbot --shell /usr/sbin/nologin tlsbot
fi
getent group tlsbot >/dev/null || { echo 'Existing tlsbot user has no tlsbot group; resolve this before installing.'; exit 1; }
install -d -m 0755 "$INSTALL_DIR"
install -d -o tlsbot -g tlsbot -m 0700 /var/lib/tlsbot
if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
    for file in "${FILES[@]}"; do
        install -o root -g root -m 0644 "$SOURCE_DIR/$file" "$INSTALL_DIR/$file"
    done
    install -d -m 0755 "$INSTALL_DIR/tests"
    for file in "$SOURCE_DIR"/tests/test_*.py; do
        install -o root -g root -m 0644 "$file" "$INSTALL_DIR/tests/"
    done
fi
if [[ ! -e "$INSTALL_DIR/.env" ]]; then
    if [[ -f "$SOURCE_DIR/.env" ]]; then
        install -o root -g tlsbot -m 0640 "$SOURCE_DIR/.env" "$INSTALL_DIR/.env"
    else
        install -o root -g tlsbot -m 0640 "$SOURCE_DIR/.env.example" "$INSTALL_DIR/.env"
    fi
fi
chown root:tlsbot "$INSTALL_DIR/.env"
chmod 0640 "$INSTALL_DIR/.env"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
export PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/browsers"
"$INSTALL_DIR/.venv/bin/python" -m playwright install --with-deps chromium
# The service runs as tlsbot, not root; browser files must be readable by it.
chmod -R a+rX "$INSTALL_DIR/browsers"
install -o root -g root -m 0644 "$INSTALL_DIR/tlsbot.service" /etc/systemd/system/tlsbot.service
install -d -m 0755 /etc/systemd/system/tlsbot.service.d
# Write only the mode override managed by this installer.
ARGS=()
[[ "$AGENCY" == false ]] || ARGS+=(--agency)
[[ "$MODE" != telegram ]] || ARGS+=(--telegram-only)
if (( ${#ARGS[@]} )); then
    {
        printf '[Service]\nExecStart=\nExecStart=/opt/tlsbot/.venv/bin/python /opt/tlsbot/main.py'
        printf ' %s' "${ARGS[@]}"
        printf '\n'
    } > /etc/systemd/system/tlsbot.service.d/mode.conf
else
    rm -f /etc/systemd/system/tlsbot.service.d/mode.conf
fi
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/tlsbot.service
cd "$INSTALL_DIR"
runuser -u tlsbot -- env PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
    "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/check_browser.py"
if [[ "$START" == true ]]; then
    runuser -u tlsbot -- env TLSBOT_INSTALL_MODE="$MODE" TLSBOT_AGENCY="$AGENCY" HEADLESS=true \
        STATE_FILE=/var/lib/tlsbot/alerts.json "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os
from config import load_config, load_telegram_config
try:
    if os.environ['TLSBOT_INSTALL_MODE'] == 'telegram':
        load_telegram_config()
    else:
        load_config(agency=os.environ['TLSBOT_AGENCY'] == 'true')
except ValueError as exc:
    raise SystemExit(f'Configuration incomplete: {exc}. Edit /opt/tlsbot/.env and rerun the installer.')
print('Configuration valid for selected mode.')
PY
    systemctl enable --now tlsbot.service
    systemctl --no-pager --full status tlsbot.service
else
    printf '\nInstalled in %s mode; service was not started.\n' "$MODE"
    echo 'Edit settings: sudoedit /opt/tlsbot/.env'
    echo 'After configuration, rerun this installer with the same mode and --start.'
fi
printf '\nLogs: sudo journalctl -u tlsbot -f\n'
echo 'A successful installation does not verify the live TLScontact calendar.'
