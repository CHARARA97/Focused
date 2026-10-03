#!/usr/bin/env bash
#
# Install the standalone Focused application for the current user (no root).
#
#   ~/.local/share/focused/app        the application itself (engine included)
#   ~/.local/bin/focusedd             launcher on PATH
#   ~/.local/bin/focused-doctor       self-check: backend, BepInEx, winhttp, the mod DLL
#   ~/.config/focused/config.json     configuration (created only if absent)
#   ~/.config/systemd/user/focused.service
#
# Focused is the backend: the game plugin and the browser extension are frontends
# that talk to it over loopback HTTP. It owns the engine itself (focused/core/).
#
# Usage:
#   scripts/install-focused.sh [--no-service] [--start]
#
# It runs two ways:
#   * from a checkout (this file next to the source tree): installs the local tree;
#   * standalone, e.g. curl -fsSL .../releases/latest/download/install.sh | sh:
#     downloads the wheel and the two helper scripts from the latest release.
#
# In both cases the result is the same: an application directory that runs with
# nothing but Python 3 -- no pip, no virtualenv, no dependencies to resolve.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

APP_DIR="${FOCUSED_APP_DIR:-$HOME/.local/share/focused/app}"
BIN_DIR="${FOCUSED_BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/focused"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

WITH_SERVICE=1
START=0

#: Filled in by the repository split (see scripts/split-repos.sh).
RELEASE_REPO="CHARARA97/Focused"
API_URL="https://api.github.com/repos/$RELEASE_REPO/releases/latest"
ASSET_BASE="https://github.com/$RELEASE_REPO/releases/latest/download"

WHEEL_SOURCE=""
SCRIPTS_SOURCE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-service) WITH_SERVICE=0; shift ;;
        --start) START=1; shift ;;
        --wheel) WHEEL_SOURCE="$2"; shift 2 ;;
        --scripts-dir) SCRIPTS_SOURCE="$2"; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say() { printf '[install] %s\n' "$*"; }
die() { printf '[install] %s\n' "$*" >&2; exit 1; }

# fetch <url> <dest> -- curl understands file:// too, which is how the offline
# tests exercise this path.
fetch() {
    command -v curl >/dev/null 2>&1 || die "curl is required to download $1"
    curl -fsSL "$1" -o "$2" || die "could not download $1"
}

# latest_wheel -- the wheel asset of the latest release, by asking the API rather
# than guessing the file name (it contains the version).
latest_wheel() {
    local json
    json="$(curl -fsSL "$API_URL")" || die "could not reach $API_URL"
    printf '%s' "$json" | python3 -c '
import json, sys
release = json.load(sys.stdin)
for asset in release.get("assets", []):
    if asset["name"].endswith(".whl"):
        print(asset["browser_download_url"])
        break
else:
    sys.exit("no wheel asset in the latest release")
'
}

# install_helper <asset-name> <destination-name> -- from the checkout when there is
# one, otherwise from the release (stable asset names, so no version is needed).
install_helper() {
    local asset="$1" dest="$2" source=""
    if [ -n "$SCRIPTS_SOURCE" ] && [ -f "$SCRIPTS_SOURCE/$asset" ]; then
        source="$SCRIPTS_SOURCE/$asset"
    elif [ -f "$ROOT/scripts/$asset" ]; then
        source="$ROOT/scripts/$asset"
    fi

    if [ -n "$source" ]; then
        cp -f "$source" "$dest"
    else
        fetch "$ASSET_BASE/$asset" "$dest"
    fi
    chmod +x "$dest"
}

say "installing Focused to $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$CONFIG_DIR"

if [ -d "$ROOT/focused/focused" ]; then
    # From a checkout: the source tree is right there.
    rm -rf "$APP_DIR/focused"
    cp -r "$ROOT/focused" "$APP_DIR/focused"
    cp -f "$ROOT/focusedd.py" "$APP_DIR/focusedd.py"
else
    # Standalone: unpack the released wheel.  A wheel is a zip, so this needs no
    # pip and no network beyond the download itself.
    wheel="$WHEEL_SOURCE"
    if [ -z "$wheel" ]; then
        say "no source tree next to this script: fetching the latest release"
        wheel="$(latest_wheel)"
    fi

    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    say "downloading $(basename "$wheel")"
    fetch "$wheel" "$tmp/app.whl"

    rm -rf "$APP_DIR/focused"
    find "$APP_DIR" -maxdepth 1 -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true
    python3 -m zipfile -e "$tmp/app.whl" "$APP_DIR" \
        || die "could not unpack $(basename "$wheel")"

    # The launcher and the systemd unit both call this file, so it exists in both
    # modes; in a checkout it is the repository's own shim.
    cat > "$APP_DIR/focusedd.py" <<'SHIM'
#!/usr/bin/env python3
"""Run Focused: the entry point is focused.cli."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from focused.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
SHIM
fi
chmod +x "$APP_DIR/focusedd.py"

cat > "$BIN_DIR/focusedd" <<EOF
#!/bin/sh
# Generated by scripts/install-focused.sh
exec python3 "$APP_DIR/focusedd.py" "\$@"
EOF
chmod +x "$BIN_DIR/focusedd"
say "installed launcher: $BIN_DIR/focusedd"

# The Steam launch wrapper: it makes sure Focused is up and then exec()s the game.
# Installed under both names because the old wrapper (which started the retired
# daemon) was called chillfocus-launch, and Steam launch options still point there.
install_helper launch-focused.sh "$BIN_DIR/focused-launch"
ln -sf "$BIN_DIR/focused-launch" "$BIN_DIR/chillfocus-launch"
say "installed Steam wrapper: $BIN_DIR/focused-launch (and chillfocus-launch)"

# The self-check goes on PATH too: it is the first thing to run when the mod does
# not appear, and it has to work even when the Python app is broken.
install_helper doctor-focused.sh "$BIN_DIR/focused-doctor"
say "installed self-check: $BIN_DIR/focused-doctor"

# The only check that matters: the installed copy imports and reports its version.
if ! python3 "$APP_DIR/focusedd.py" --version >/dev/null 2>&1; then
    die "the installed copy does not run: $APP_DIR/focusedd.py --version failed"
fi
say "verified: $(python3 "$APP_DIR/focusedd.py" --version 2>/dev/null || echo '?')"

if [ ! -f "$CONFIG_DIR/config.json" ]; then
    # Defaults only: an empty blacklist, so a fresh install suspends nothing until
    # the user (or ChillFocus) says what to suspend.
    if python3 "$APP_DIR/focusedd.py" --write-default-config "$CONFIG_DIR/config.json" >/dev/null; then
        say "created config: $CONFIG_DIR/config.json (empty blacklist)"
    else
        say "WARNING: could not write $CONFIG_DIR/config.json"
    fi
else
    say "kept existing config: $CONFIG_DIR/config.json"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        say "NOTE: $BIN_DIR is not on your PATH."
        say "      Call $BIN_DIR/focusedd directly, or add that directory to PATH."
        ;;
esac

if [ "$WITH_SERVICE" -eq 1 ]; then
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/focused.service" <<EOF
[Unit]
Description=Focused - suspend distracting applications while you focus
Documentation=https://github.com/CHARARA97/Focused/blob/main/docs/focused.md
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$BIN_DIR/focusedd --config $CONFIG_DIR/config.json
# Crash safety net: systemd runs this even when Focused is killed outright, so a
# suspended application cannot outlive the process that suspended it.
ExecStopPost=$BIN_DIR/focusedd --config $CONFIG_DIR/config.json --thaw-all
Restart=on-failure
RestartSec=5
# Freezing a heavy application must not make the desktop sluggish.
Nice=5

# Focused only ever signals processes of the same user, so it needs no
# privileges. Do NOT add ProtectProc=invisible or ProcSubset=pid here: hiding
# /proc would leave it with nothing to look at.

[Install]
WantedBy=default.target
EOF
    say "installed unit: $UNIT_DIR/focused.service"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload || true
        if [ "$START" -eq 1 ]; then
            systemctl --user enable --now focused.service
            say "service enabled and started"
            systemctl --user --no-pager status focused.service || true
        else
            say "enable it with: systemctl --user enable --now focused.service"
        fi
    fi
fi

say "done. Dashboard: http://127.0.0.1:8766/  (after starting the service)"
