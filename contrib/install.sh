#!/bin/bash
# SPDX-FileCopyrightText: 2016-2026 joba1
# SPDX-License-Identifier: AGPL-3.0-or-later
# Install, update or remove ChordsPlay from this checkout, for the current user
# (systemd user units, no root). See docs/install.md.
#
#   contrib/install.sh [options] [web] [mmp]
#
#   web   the web server (default if nothing is named): copies ChordsPlay/ and web/ to
#         ~/.local/share/chordsplay-web/app, makes a venv with the dependencies and
#         starts the user unit chordsplay-web.service. Later edits in the checkout
#         reach the running service with the next run.
#   mmp   the Mustang Micro Plus parts, on the computer the amp is paired with: the
#         amp agent (maneline) and the amp bridge (mmp/install-amp-agent.sh,
#         mmp/install-amp-bridge.sh).
#
#   --music DIR[:DIR...]  the music folders, written into a new settings file
#   --uninstall           stop and remove the named parts; settings and data stay
#   -n, --dry-run         show what would be done
#
# Update: git pull, then run it again with the same parts.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")
BASE=$HOME/.local/share/chordsplay-web
CONFIG=$HOME/.config/chordsplay       # the units read %h/.config/..., so not XDG_CONFIG_HOME
UNIT_DIR=$HOME/.config/systemd/user
WEB_UNIT=chordsplay-web.service
MMP_BASE=$HOME/.local/share/chordsplay
MMP_UNITS=(chordsplay-amp-bridge.service chordsplay-amp-agent.service)

DRY= UNINSTALL= MUSIC= PARTS=()
while [ $# -gt 0 ]; do
    case $1 in
        web|mmp) PARTS+=("$1") ;;
        --music) MUSIC=${2:?--music needs a folder}; shift ;;
        --uninstall) UNINSTALL=1 ;;
        -n|--dry-run) DRY=1 ;;
        -h|--help) sed -n '4,22s/^# \{0,1\}//p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done
[ ${#PARTS[@]} -gt 0 ] || PARTS=(web)

run() {
    if [ -n "$DRY" ]; then
        printf '+'; printf ' %q' "$@"; echo
    else
        "$@"
    fi
}

install_web() {
    local app=$BASE/app venv=$BASE/venv env=$CONFIG/web.env
    command -v rsync >/dev/null || { echo "rsync is needed" >&2; exit 1; }
    python3 -c 'import sys, venv; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
        || { echo "Python 3.11 or newer with the venv module is needed" >&2; exit 1; }
    run mkdir -p "$app" "$CONFIG" "$UNIT_DIR"
    run rsync -a --delete --exclude __pycache__ "$REPO/ChordsPlay" "$REPO/web" "$app/"
    if [ -z "$DRY" ]; then
        git -C "$REPO" describe --always --dirty --tags > "$app/VERSION" 2>/dev/null || true
    fi
    [ -x "$venv/bin/python" ] || run python3 -m venv "$venv"
    run "$venv/bin/python" -m pip install --quiet --upgrade pip
    run "$venv/bin/pip" install --quiet --upgrade -r "$REPO/web/requirements.txt"
    if [ ! -e "$env" ]; then
        run install -m 600 "$HERE/chordsplay.env.example" "$env"
        if [ -n "$MUSIC" ]; then
            run sed -i "s|^#CHORDSPLAY_MUSIC_DIRS=.*|CHORDSPLAY_MUSIC_DIRS=$MUSIC|" "$env"
        fi
        echo "Settings: $env (new, from the template)"
    elif [ -n "$MUSIC" ]; then
        echo "Settings: $env exists, --music not applied; edit CHORDSPLAY_MUSIC_DIRS there"
    fi
    run install -m 644 "$HERE/$WEB_UNIT" "$UNIT_DIR/$WEB_UNIT"
    run systemctl --user daemon-reload
    run systemctl --user enable --quiet "$WEB_UNIT"
    run systemctl --user restart "$WEB_UNIT"
    [ -n "$DRY" ] && return
    sleep 2
    systemctl --user --no-pager --lines=5 status "$WEB_UNIT" || true
    local port
    port=$(sed -n 's/^CHORDSPLAY_PORT=//p' "$env" 2>/dev/null | tail -1)
    echo "Installed $(cat "$app/VERSION" 2>/dev/null): http://127.0.0.1:${port:-8616}/"
    grep -q '^CHORDSPLAY_MUSIC_DIRS=' "$env" \
        || echo "No music folder yet: set CHORDSPLAY_MUSIC_DIRS in $env, then run this again."
    loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q yes \
        || echo "To run it without being logged in: loginctl enable-linger $USER"
}

uninstall_web() {
    run systemctl --user disable --now "$WEB_UNIT" 2>/dev/null || true
    run rm -f "$UNIT_DIR/$WEB_UNIT"
    run systemctl --user daemon-reload
    run rm -rf "$BASE/app" "$BASE/venv"
    echo "Removed the web server. Kept: the database, takes and PDFs in $BASE, the settings in $CONFIG/web.env."
}

install_mmp() {
    run "$REPO/mmp/install-amp-agent.sh"
    if grep -qE '^CHORDSPLAY_BRIDGE_URL=' "$CONFIG/bridge.env" 2>/dev/null; then
        run "$REPO/mmp/install-amp-bridge.sh"
    else
        cat <<MSG
The amp agent is installed. For the amp bridge, open the ChordsPlay page, press g,
"Set up amp control…", put the line it shows into $CONFIG/bridge.env
and run this again.
MSG
    fi
}

uninstall_mmp() {
    local unit
    for unit in "${MMP_UNITS[@]}"; do
        run systemctl --user disable --now "$unit" 2>/dev/null || true
        run rm -f "$UNIT_DIR/$unit"
    done
    run systemctl --user daemon-reload
    run rm -rf "$MMP_BASE/bridge" "$MMP_BASE/maneline" "$MMP_BASE/maneline-venv"
    echo "Removed the amp bridge and the amp agent. Kept: level measurements and preset backups in" \
         "$MMP_BASE/levels, the settings in $CONFIG (bridge.env, amp-agent.env)."
}

for part in "${PARTS[@]}"; do
    if [ -n "$UNINSTALL" ]; then
        "uninstall_$part"
    else
        "install_$part"
    fi
done
