#!/usr/bin/env bash
#
# Self-check for an installed ChillFocused: says which link in the chain is
# broken and what to type to fix it.
#
# The mod fails quietly by nature.  If BepInEx never loads it, the game simply
# starts and nothing appears; if the backend is down, the overlay says "Focused
# 未连接" and nothing is suspended; if the two sides disagree about the port or the
# token, they look connected but nothing works.  This script walks the whole chain
# and reports the first thing that is actually wrong.
#
# Usage:
#   scripts/doctor-focused.sh [--game-dir DIR]
#
# Exit code: 0 when the chain looks healthy, 1 when something is broken.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME_DIR="${CHILLFOCUS_GAME_DIR:-}"
PREFIX="${CHILLFOCUS_PFX:-$HOME/.local/share/Steam/steamapps/compatdata/3548580/pfx}"
FOCUSED_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/focused/config.json"
APP_ID=3548580

while [ "$#" -gt 0 ]; do
    case "$1" in
        --game-dir) GAME_DIR="$2"; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

OK=0
WARN=0
BAD=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; OK=$((OK + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; WARN=$((WARN + 1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; BAD=$((BAD + 1)); }
fix()  { printf '      %s\n' "$1"; }
head_() { printf '\n%s\n' "$1"; }

# --- 1. the backend ---------------------------------------------------------
head_ "1. 后端 / backend"

FOCUSED_URL="http://127.0.0.1:8766"
BACKEND_JSON="$(curl -fsS -m 2 "$FOCUSED_URL/api/v1/focus" 2>/dev/null || true)"
if [ -n "$BACKEND_JSON" ]; then
    version="$(printf '%s' "$BACKEND_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version","?"))' 2>/dev/null || echo '?')"
    ok "Focused is answering on $FOCUSED_URL (version $version)"
else
    bad "Focused is not answering on $FOCUSED_URL"
    fix "systemctl --user status focused"
    fix "systemctl --user enable --now focused"
fi

# --- 2. the game ------------------------------------------------------------
head_ "2. 游戏目录 / game directory"

if [ -z "$GAME_DIR" ]; then
    for vdf in \
        "$HOME/.local/share/Steam/steamapps/libraryfolders.vdf" \
        "$HOME/.steam/steam/steamapps/libraryfolders.vdf" \
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/libraryfolders.vdf"
    do
        [ -f "$vdf" ] || continue
        while IFS= read -r library; do
            [ -n "$library" ] || continue
            candidate="$library/steamapps/common/Chill with You Lo-Fi Story"
            if [ -f "$candidate/Chill With You_Data/Managed/Assembly-CSharp.dll" ]; then
                GAME_DIR="$candidate"
                break
            fi
        done < <(sed -n 's/.*"path"[[:space:]]*"\([^"]*\)".*/\1/p' "$vdf")
        [ -n "$GAME_DIR" ] && break
    done
fi
if [ -z "$GAME_DIR" ]; then
    GAME_DIR="$HOME/.local/share/Steam/steamapps/common/Chill with You Lo-Fi Story"
fi

if [ -f "$GAME_DIR/Chill With You_Data/Managed/Assembly-CSharp.dll" ]; then
    ok "game found: $GAME_DIR"
else
    bad "game not found (expected the folder with 'Chill With You.exe')"
    fix "scripts/doctor-focused.sh --game-dir '/path/to/Chill with You Lo-Fi Story'"
    GAME_DIR=""
fi

PLUGINS="$GAME_DIR/BepInEx/plugins"

# --- 3. BepInEx -------------------------------------------------------------
head_ "3. BepInEx"

if [ -n "$GAME_DIR" ] && [ -f "$GAME_DIR/winhttp.dll" ] && [ -d "$GAME_DIR/BepInEx/core" ]; then
    ok "BepInEx is installed in the game"
else
    bad "BepInEx is missing from the game"
    fix "scripts/install-bepinex.sh   (downloads BepInEx 5.4.23.5 and verifies its sha256)"
fi

# The override is what makes UnityDoorstop load at all: without it the game runs
# normally and BepInEx is simply never asked to do anything.
if [ -f "$PREFIX/user.reg" ]; then
    if python3 - "$PREFIX/user.reg" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.search(r'\[Software\\\\Wine\\\\DllOverrides\](.*?)(?=\n\[|\Z)', text, re.S)
sys.exit(0 if m and re.search(r'"winhttp"\s*=', m.group(1), re.I) else 1)
PY
    then
        ok "the winhttp DLL override is set in $PREFIX"
    else
        bad "the winhttp DLL override is NOT set (BepInEx will never load)"
        fix "protontricks -c 'wine reg add \"HKCU\\Software\\Wine\\DllOverrides\" /v winhttp /d \"native,builtin\" /f' $APP_ID"
        fix "or set the Steam launch option: WINEDLLOVERRIDES=\"winhttp.dll=n,b\" %command%"
    fi
else
    warn "no Proton prefix at $PREFIX (game never started, or a different prefix)"
    fix "start the game once, then run this again"
fi

# --- 4. the mod -------------------------------------------------------------
head_ "4. 插件 / the mod"

# Two copies mean the mod runs twice: BepInEx loads every DLL under plugins/,
# subdirectories included.
mapfile -t COPIES < <(find "$PLUGINS" -maxdepth 2 -iname 'ChillFocus*.dll' 2>/dev/null | sort)
if [ -n "$GAME_DIR" ] && [ -f "$PLUGINS/ChillFocused.dll" ]; then
    sha="$(sha256sum "$PLUGINS/ChillFocused.dll" | cut -d' ' -f1)"
    ok "installed: $PLUGINS/ChillFocused.dll"
    fix "sha256 $sha"
elif [ "${#COPIES[@]}" -gt 0 ]; then
    # Older layout.  It loads, so this is a tidiness problem with a real failure
    # mode: as soon as a second copy appears the mod runs twice.
    warn "installed in the older layout: ${COPIES[0]}"
    fix "scripts/install-bepinex.sh   (moves it to $PLUGINS/ChillFocused.dll and cleans up)"
else
    bad "ChillFocused.dll is not installed"
    fix "scripts/install-bepinex.sh"
fi

if [ "${#COPIES[@]}" -gt 1 ]; then
    bad "more than one copy of the mod is installed (it would run twice)"
    for copy in "${COPIES[@]}"; do
        fix "remove: $copy"
    done
elif [ "${#COPIES[@]}" -eq 1 ] && [ -f "$PLUGINS/ChillFocused.dll" ]; then
    ok "exactly one copy of the mod is installed"
elif [ "${#COPIES[@]}" -eq 1 ]; then
    warn "one copy, in the older layout (see above)"
fi

# --- 5. the log -------------------------------------------------------------
head_ "5. 日志 / the log file"

LOG="$GAME_DIR/BepInEx/LogOutput.log"
if [ -n "$GAME_DIR" ] && [ -f "$LOG" ]; then
    if grep -q 'ChillFocused' "$LOG"; then
        line="$(grep -m1 'ChillFocused .* loaded' "$LOG" | cut -c1-150)"
        if [ -n "$line" ]; then
            ok "the mod loaded: $line"
        else
            ok "the log mentions ChillFocused"
        fi
    else
        bad "the log has no ChillFocused entry: BepInEx is loading, the mod is not"
        fix "check the two 'another copy' / winhttp items above"
        fix "tail -50 '$LOG'"
    fi
    if grep -q 'another copy of this plugin is installed' "$LOG"; then
        warn "the log warns about a duplicate install (see the remove: lines above)"
        fix "grep -A3 'another copy' '$LOG'"
    fi
else
    warn "no log yet at $LOG"
    fix "start the game once, then run this again"
fi

# --- 6. plugin <-> backend agreement ---------------------------------------
head_ "6. 两端是否对得上 / plugin and backend agreement"

CONFIG="$GAME_DIR/BepInEx/config/com.chillfocused.plugin.cfg"
PLUGIN_URL=""
PLUGIN_TOKEN=""
if [ -f "$CONFIG" ]; then
    PLUGIN_URL="$(sed -n 's/^FocusedUrl[[:space:]]*=[[:space:]]*//p' "$CONFIG" | head -1 | tr -d '\r')"
    PLUGIN_TOKEN="$(sed -n 's/^FocusedToken[[:space:]]*=[[:space:]]*//p' "$CONFIG" | head -1 | tr -d '\r')"
fi

if [ -n "$PLUGIN_URL" ]; then
    ok "the plugin points at $PLUGIN_URL"
    case "$PLUGIN_URL" in
        *:8765*) bad "that is the retired daemon port; Focused listens on 8766"
                 fix "edit $CONFIG: FocusedUrl = http://127.0.0.1:8766" ;;
    esac
else
    warn "the plugin has no configuration file yet"
    fix "start the game once so BepInEx writes $CONFIG"
fi

BACKEND_TOKEN=""
if [ -f "$FOCUSED_CONFIG" ]; then
    BACKEND_TOKEN="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get("http") or {}).get("token",""))' "$FOCUSED_CONFIG" 2>/dev/null || echo '')"
    ok "Focused config: $FOCUSED_CONFIG"
else
    warn "Focused has no config yet at $FOCUSED_CONFIG (it is written on first run)"
fi

if [ -n "$PLUGIN_TOKEN" ] || [ -n "$BACKEND_TOKEN" ]; then
    if [ "$PLUGIN_TOKEN" = "$BACKEND_TOKEN" ]; then
        ok "the tokens match"
    else
        bad "the tokens differ: the plugin will be refused with 401"
        fix "put the same value in FocusedToken (plugin cfg) and http.token (Focused config)"
    fi
else
    ok "no token configured on either side (loopback only)"
fi

# --- summary ----------------------------------------------------------------
printf '\n%s\n' "小结 / summary: $OK ok, $WARN warning(s), $BAD problem(s)"
if [ "$BAD" -eq 0 ]; then
    printf '%s\n' "链路看起来是通的。打开 http://127.0.0.1:8766/ ，开始一轮专注，看「概览」里出现已冻结的应用即可。"
    exit 0
fi

printf '%s\n' "先修上面标 ✗ 的项，再跑一次本脚本。"
exit 1
