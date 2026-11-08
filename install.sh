#!/usr/bin/env bash
# =============================================================================
# gitsync — one-click installer
#
#   bash install.sh                # install to /opt/gitsync + symlink
#   bash install.sh --user         # install to ~/.local/share/gitsync
#   bash install.sh --schedule hourly|6h|12h|daily|off
#   bash install.sh --cron         # use cron instead of a systemd timer
#
# What it does:
#   1. copies the app (default: /opt/gitsync, or ~/.local/share/gitsync)
#   2. symlinks bin/gitsync into /usr/local/bin (or ~/.local/bin)
#   3. writes the config dir (~/.config/gitsync) and data dir
#   4. optionally installs a systemd timer (Persistent=true) or cron job
# =============================================================================
set -euo pipefail

APP_NAME="gitsync"
SCHEDULE="hourly"
USE_CRON=0
USER_MODE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --schedule) SCHEDULE="$2"; shift 2 ;;
        --schedule=*) SCHEDULE="${1#*=}"; shift ;;
        --cron) USE_CRON=1; shift ;;
        --user) USER_MODE=1; shift ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$USER_MODE" = "1" ]; then
    INSTALL_DIR="$HOME/.local/share/$APP_NAME"
    BIN_DIR="$HOME/.local/bin"
else
    INSTALL_DIR="/opt/$APP_NAME"
    BIN_DIR="/usr/local/bin"
fi

echo "==> gitsync installer"
echo "    source : $SRC_DIR"
echo "    target : $INSTALL_DIR"

# --- copy the app ---
sudo mkdir -p "$INSTALL_DIR" 2>/dev/null || mkdir -p "$INSTALL_DIR"
sudo cp -r "$SRC_DIR/bin" "$SRC_DIR/gitsync" "$SRC_DIR/README.md" \
         "$SRC_DIR/LICENSE" "$SRC_DIR/docs" "$SRC_DIR/quickinstall.sh" \
         "$INSTALL_DIR/" 2>/dev/null \
    || cp -r "$SRC_DIR/bin" "$SRC_DIR/gitsync" "$SRC_DIR/README.md" \
           "$SRC_DIR/LICENSE" "$SRC_DIR/docs" "$SRC_DIR/quickinstall.sh" \
           "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/bin/gitsync"

# --- symlink ---
sudo mkdir -p "$BIN_DIR" 2>/dev/null || mkdir -p "$BIN_DIR"
sudo ln -sf "$INSTALL_DIR/bin/gitsync" "$BIN_DIR/gitsync" 2>/dev/null \
    || ln -sf "$INSTALL_DIR/bin/gitsync" "$BIN_DIR/gitsync"
echo "    command: $BIN_DIR/gitsync"

# --- config + data dirs ---
mkdir -p "$HOME/.config/$APP_NAME" "$HOME/.local/share/$APP_NAME/mirrors"

# --- scheduler ---
if [ "$SCHEDULE" = "off" ]; then
    echo "==> scheduler: skipped (--schedule off)"
else
    if [ "$USE_CRON" = "1" ] || [ ! -d /etc/systemd/system ]; then
        LINE=$(python3 - "$INSTALL_DIR" "$SCHEDULE" <<'PY'
import sys
install, sched = sys.argv[1], sys.argv[2]
cron = {"hourly": "0 * * * *", "6h": "0 */6 * * *",
        "12h": "0 */12 * * *", "daily": "0 3 * * *"}[sched]
print("%s %s/bin/gitsync sync --quiet >/dev/null 2>&1" % (cron, install))
PY
)
        ( crontab -l 2>/dev/null | grep -v "gitsync" ; echo "$LINE" ) | crontab -
        echo "==> cron installed: $LINE"
    else
        cat > /etc/systemd/system/$APP_NAME.service <<EOF
[Unit]
Description=gitsync repository sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=HOME=%h
Environment=GH_CONFIG_DIR=%h/.config/gh
Environment=XDG_CONFIG_HOME=%h/.config
ExecStart=$INSTALL_DIR/bin/gitsync sync --quiet
StandardOutput=journal
StandardError=journal
EOF
        CAL=$(python3 -c "import sys; print({'hourly':'hourly','6h':'*:0/6','12h':'*:0/12','daily':'*-*-* 03:00:00'}['$SCHEDULE'])")
        cat > /etc/systemd/system/$APP_NAME.timer <<EOF
[Unit]
Description=Run gitsync on schedule

[Timer]
OnCalendar=$CAL
Persistent=true
AccuracySec=1min
Unit=$APP_NAME.service

[Install]
WantedBy=timers.target
EOF
        systemctl daemon-reload
        systemctl enable --now $APP_NAME.timer
        echo "==> systemd timer installed: $SCHEDULE (Persistent=true)"
        echo "    status: $(systemctl list-timers $APP_NAME.timer --no-pager | tail -1)"
    fi
fi

echo
echo "==> done. next steps:"
echo "    gitsync setup     # guided configuration (platforms, tokens, routes)"
echo "    gitsync sync      # first sync"
echo "    gitsync tui       # dashboard"
echo "    gitsync status    # quick check"
echo "    gitsync scopes    # which token scopes each platform needs"
