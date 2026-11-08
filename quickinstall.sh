#!/usr/bin/env bash
# =============================================================================
#  gitsync — ONE-CLICK INSTALLER  (bootstrap)
#
#  Run it straight from the internet, nothing else needed:
#
#     curl -fsSL https://raw.githubusercontent.com/SeaXen/gitsync/main/quickinstall.sh | bash
#
#  What that single command does:
#     1. checks Python 3.8+ and downloads the gitsync repo (git or tarball)
#     2. installs it (/opt/gitsync, or ~/.local/share/gitsync with --user)
#     3. puts the `gitsync` command on your PATH
#     4. runs the setup wizard  (platforms, tokens, sync direction, schedule)
#     5. installs the scheduler (systemd timer, Persistent=true — or cron)
#     6. offers to run your first sync
#
#  Options (append after `bash -s --`):
#     --user            install without root (~/.local/share, ~/.local/bin)
#     --cron            use cron instead of a systemd timer
#     --schedule X      hourly | 6h | 12h | daily | off        (default hourly)
#     --no-setup        skip the wizard (configure later with: gitsync setup)
#     --no-sync         don't offer the first sync
#     --branch NAME     install a different branch
#     --yes             accept defaults everywhere (unattended)
#
#  Examples:
#     curl -fsSL https://raw.githubusercontent.com/SeaXen/gitsync/main/quickinstall.sh | bash
#     curl -fsSL .../quickinstall.sh | bash -s -- --user --schedule 6h
# =============================================================================
set -euo pipefail

REPO="SeaXen/gitsync"
BRANCH="${GITSYNC_BRANCH:-main}"

# ---------------------------------------------------------------- ui helpers
if [ -t 1 ]; then
    B=$'\033[1m'; CY=$'\033[1;36m'; GR=$'\033[1;32m'; RD=$'\033[1;31m'; NC=$'\033[0m'
else
    B=""; CY=""; GR=""; RD=""; NC=""
fi
say()   { printf '%s==>%s %s\n' "$CY" "$NC" "$*"; }
ok()    { printf '%s  ✓%s %s\n' "$GR" "$NC" "$*"; }
die()   { printf '%s!! %s%s\n' "$RD" "$*" "$NC" >&2; exit 1; }

# prompt on the controlling terminal (works when piped from curl)
ask_yn() {
    local prompt="$1" def="${2:-y}" ans=""
    if [ -t 0 ]; then
        read -r -p "$prompt " ans || ans=""
    elif [ -e /dev/tty ]; then
        printf '%s' "$prompt" > /dev/tty
        read -r ans < /dev/tty || ans=""
    else
        ans="$def"
    fi
    ans="${ans:-$def}"
    case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# ---------------------------------------------------------------- arguments
USER_MODE=0; USE_CRON=0; SCHEDULE="hourly"; DO_SETUP=1; DO_SYNC=1; AUTO_YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --user)       USER_MODE=1; shift ;;
        --cron)       USE_CRON=1; shift ;;
        --schedule)   SCHEDULE="${2:-hourly}"; shift 2 ;;
        --no-setup)   DO_SETUP=0; shift ;;
        --no-sync)    DO_SYNC=0; shift ;;
        --branch)     BRANCH="${2:-main}"; shift 2 ;;
        --yes|-y)     AUTO_YES=1; shift ;;
        -h|--help)    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            die "unknown option: $1  (try --help)" ;;
    esac
done

# ---------------------------------------------------------------- preflight
say "gitsync one-click installer"
command -v python3 >/dev/null 2>&1 || die "python3 is required (apt install python3 / brew install python3)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "python 3.8+ required (found $(python3 -V 2>&1 | awk '{print $2}'))"
ok "python3 $(python3 -V 2>&1 | awk '{print $2}')"

SUDO=""
if [ "$USER_MODE" = "0" ] && [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
        say "not root and no sudo — falling back to --user install"
        USER_MODE=1
    fi
fi

# ---------------------------------------------------------------- download
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
say "downloading $REPO@$BRANCH ..."
if command -v git >/dev/null 2>&1; then
    git clone --depth 1 -b "$BRANCH" "https://github.com/$REPO.git" "$TMP/gitsync" \
        >/dev/null 2>&1 || die "git clone failed (is the repo reachable?)"
else
    TARBALL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
    command -v curl >/dev/null 2>&1 || die "need git or curl to download"
    curl -fsSL "$TARBALL" | tar xz -C "$TMP" || die "download failed"
    mv "$TMP"/gitsync-* "$TMP/gitsync"
fi
ok "downloaded to $TMP/gitsync"

# ---------------------------------------------------------------- install
ARGS=(--schedule "$SCHEDULE")
[ "$USER_MODE" = "1" ] && ARGS+=(--user)
[ "$USE_CRON"   = "1" ] && ARGS+=(--cron)
[ "$AUTO_YES"   = "1" ] && ARGS+=(--yes)
bash "$TMP/gitsync/install.sh" "${ARGS[@]}"

# ---------------------------------------------------------------- setup
if [ "$DO_SETUP" = "1" ]; then
    say "starting the setup wizard (platforms, tokens, direction, schedule)"
    gitsync setup || die "setup wizard failed — re-run it anytime with: gitsync setup"
    ok "configuration saved"
else
    say "skipping setup (--no-setup) — run 'gitsync setup' when ready"
fi

# ---------------------------------------------------------------- first sync
if [ "$DO_SYNC" = "1" ] && [ "$DO_SETUP" = "1" ]; then
    echo
    if ask_yn "Run your first sync now? [Y/n]" "y"; then
        say "running first sync (this may take a while the first time)..."
        gitsync sync || say "first sync reported failures — check: gitsync records"
    fi
fi

# ---------------------------------------------------------------- done
echo
say "${B}done!${NC} gitsync is installed and configured."
echo
echo "  gitsync              open the dashboard (TUI)"
echo "  gitsync status       quick health check"
echo "  gitsync doctor       full diagnostics"
echo "  gitsync scopes       which token scopes each platform needs"
echo
if [ "$USER_MODE" = "1" ]; then
    echo "  installed to: ~/.local/share/gitsync   (add ~/.local/bin to PATH if needed)"
else
    echo "  installed to: /opt/gitsync"
fi
echo "  config:     ~/.config/gitsync/config.json"
echo "  mirrors:    ~/.local/share/gitsync/mirrors"
echo
