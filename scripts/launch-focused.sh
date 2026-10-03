#!/usr/bin/env bash
#
# ChillFocused launcher.
#
# Point Steam at this instead of the game binary:
#
#   Chill with You -> Properties -> Launch Options:
#       /absolute/path/to/launch-focused.sh %command%
#
# The mod needs Focused running. Focused is an ordinary user service that also
# works with no game at all, so this wrapper only has to make sure it is up -- and
# then get out of the way:
#
#   * it never starts a second copy (it probes the API, not process names);
#   * it never blocks the game: if Focused cannot be started, the game still runs
#     and the mod simply degrades to "Focused 未连接";
#   * it exec()s the game, so the game keeps this script's PID and no wrapper
#     process lingers.
#
# (Before the merge this script started a per-session chillfocusd tied to the
# game's lifetime. That whole middle layer is gone: the plugin talks to Focused
# directly, and Focused outlives the game by design.)

set -euo pipefail

HOST="${FOCUSED_HOST:-127.0.0.1}"
PORT="${FOCUSED_PORT:-8766}"
SERVICE="${FOCUSED_SERVICE:-focused.service}"

log() { printf '[chillfocused] %s\n' "$*" >&2; }

if [ "$#" -eq 0 ]; then
    log "no command given; expected to be used as a Steam launch option with %command%"
    exit 2
fi

focused_is_up() {
    local url="http://${HOST}:${PORT}/api/v1/focus"
    if command -v curl >/dev/null 2>&1; then
        curl -fsS -m 1 "$url" >/dev/null 2>&1
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 1 -O /dev/null "$url" >/dev/null 2>&1
    else
        pgrep -f 'focusedd' >/dev/null 2>&1
    fi
}

if focused_is_up; then
    log "Focused is already answering on ${HOST}:${PORT}"
else
    if command -v systemctl >/dev/null 2>&1; then
        log "starting ${SERVICE}..."
        # --no-block: this is a launch option, and waiting on systemd must never
        # delay the game.
        systemctl --user start --no-block "$SERVICE" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            sleep 0.2
            if focused_is_up; then
                log "Focused is up"
                break
            fi
        done
    fi
    if ! focused_is_up; then
        log "Focused is not reachable on ${HOST}:${PORT}."
        log "Install or start it with: systemctl --user start ${SERVICE}"
        log "The game will still start; the overlay will show 'Focused 未连接'."
    fi
fi

exec "$@"
