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
SYSTEM_UNIT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --schedule) SCHEDULE="$2"; shift 2 ;;
        --schedule=*) SCHEDULE="${1#*=}"; shift ;;
        --cron) USE_CRON=1; shift ;;
        --system) SYSTEM_UNIT=1; shift ;;
        --user) USER_MODE=1; shift ;;
        --yes|-y) shift ;;                    # accepted; nothing to confirm
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (allowed: --schedule KEY --cron --system --user --yes)" >&2
           echo "schedules: hourly 6h 12h daily weekly off" >&2
           exit 1 ;;
    esac
done

# allowlist the schedule, so a typo cannot silently install hourly
case "$SCHEDULE" in
    hourly|6h|12h|daily|weekly|off) ;;
    *) echo "unknown schedule: $SCHEDULE (hourly 6h 12h daily weekly off)" >&2
       exit 2 ;;
esac

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$USER_MODE" = "1" ]; then
    # a user install never uses sudo: the files must belong to the user, or
    # the later chmod/install fails and ~/.local/share ends up root-owned
    INSTALL_DIR="$HOME/.local/share/$APP_NAME"
    BIN_DIR="$HOME/.local/bin"
    SUDO=""
else
    INSTALL_DIR="/opt/$APP_NAME"
    BIN_DIR="/usr/local/bin"
    # sudo only when we are not already root, and it covers both the copy
    # and the symlink
    if [ "$(id -u)" = "0" ]; then SUDO=""; else SUDO="sudo"; fi
fi

echo "==> gitsync installer"
echo "    source : $SRC_DIR"
echo "    target : $INSTALL_DIR"

# --- copy the app ---
$SUDO mkdir -p "$INSTALL_DIR"
$SUDO cp -r "$SRC_DIR/bin" "$SRC_DIR/gitsync" "$SRC_DIR/README.md" \
         "$SRC_DIR/LICENSE" "$SRC_DIR/docs" "$SRC_DIR/quickinstall.sh" \
         "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/bin/gitsync"
[ -f "$INSTALL_DIR/gitsync/askpass.py" ] && chmod +x "$INSTALL_DIR/gitsync/askpass.py" || true

# --- symlink ---
$SUDO mkdir -p "$BIN_DIR"
$SUDO ln -sf "$INSTALL_DIR/bin/gitsync" "$BIN_DIR/gitsync"
echo "    command: $BIN_DIR/gitsync"

# --- config + data dirs ---
mkdir -p "$HOME/.config/$APP_NAME" "$HOME/.local/share/$APP_NAME/mirrors"

# --- scheduler ---
# The unit text is built by `gitsync install` (one source of truth). A user
# unit by default: the timer must run as the user who owns the config, or
# $HOME (gh CLI, tokens) is invisible to it.
if [ "$SCHEDULE" != "off" ]; then
    GITSYNC_BIN="$BIN_DIR/gitsync"
    SCHED_ARGS="--schedule $SCHEDULE"
    [ "$USE_CRON" = "1" ] && SCHED_ARGS="$SCHED_ARGS --cron"
    [ "$SYSTEM_UNIT" = "1" ] && SCHED_ARGS="$SCHED_ARGS --system"
    if "$GITSYNC_BIN" install $SCHED_ARGS --yes; then
        :
    else
        echo "==> scheduler install failed; run 'gitsync install' by hand" >&2
    fi
else
    echo "==> scheduler: skipped (--schedule off)"
fi

echo
echo "==> done. next steps:"
echo "    gitsync setup     # guided configuration (platforms, tokens, routes)"
echo "    gitsync sync      # first sync"
echo "    gitsync tui       # dashboard"
echo "    gitsync web       # dashboard in your browser (http://127.0.0.1:7765)"
echo "    gitsync status    # quick check"
echo "    gitsync scopes    # which token scopes each platform needs"
