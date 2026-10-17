#!/usr/bin/env python3
"""Act like the BepInEx plugin, without launching the game.

This is the protocol conformance harness.  It performs the same request sequence
``FocusedClient`` does -- health, push rules, toggle focus, poll status, drain the
event feed -- and prints what the in-game overlay would be showing.  Two uses:

* verifying Focused and the plugin agree on the wire format;
* debugging Focused when the game is not running.

Examples:

    scripts/simulate-plugin.py --probe
    scripts/simulate-plugin.py --names 'firefox; discord' --cmdline '--profile distractor'
    scripts/simulate-plugin.py --enable --watch 15
    scripts/simulate-plugin.py --disable
    scripts/simulate-plugin.py --scan
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

API = "/api/v1"


def call(base: str, method: str, path: str, body=None, token: str = "", timeout: float = 4.0):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["X-Focused-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        exc.close()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"ok": False, "error": raw}
    except Exception as exc:  # connection refused, timeout, ...
        return None, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def split_list(raw: str):
    return [piece.strip() for piece in (raw or "").replace("\n", ";").split(";") if piece.strip()]


def print_status(payload, label="Focused status"):
    stats = payload.get("stats", {})
    rules = payload.get("blacklist", {})
    print("--- %s ---" % label)
    print("  session            : %s%s" % (
        "active" if payload.get("active") else "idle",
        "   (dry-run)" if payload.get("dry_run") else ""))
    print("  claims             : %s" % (payload.get("claims") or {}))
    print("  frozen             : %d  %s" % (
        payload.get("frozen_count", 0), payload.get("frozen_names") or []))
    print("  protect list       : %d entries" % payload.get("protect_count", 0))
    print("  blacklist names    : %s" % (rules.get("names") or []))
    print("  blacklist cmdline  : %s" % (rules.get("cmdline_substrings") or []))
    print("  rule sources       : %s" % (payload.get("sources") or {}))
    print("  plugins            : %s" % [
        (plugin.get("id"), plugin.get("status")) for plugin in payload.get("plugins") or []
    ])
    print("  stats              : scans=%(scans)s matched=%(matched)s frozen=%(frozen)s "
          "thawed=%(thawed)s protected=%(protected)s skipped=%(freeze_skipped)s" % stats)
    warnings = payload.get("warnings") or []
    for warning in warnings:
        print("  WARNING            : %s" % warning)


def format_event(event) -> str:
    stamp = time.strftime("%H:%M:%S", time.localtime(event.get("ts", 0)))
    return "  [%s] %-14s pid=%-7s %-8s %s" % (
        stamp,
        event.get("kind"),
        event.get("pid"),
        event.get("name") or "-",
        event.get("detail") or "",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8766", help="Focused base URL")
    parser.add_argument("--token", default="", help="X-Focused-Token value, if Focused requires one")
    parser.add_argument("--names", default=None, help="blacklist process names, ';' separated")
    parser.add_argument("--cmdline", default=None, help="blacklist cmdline substrings, ';' separated")
    parser.add_argument("--protect", default=None, help="extra protect cmdline substrings, ';' separated")
    parser.add_argument("--enable", action="store_true", help="claim a session (like the game does)")
    parser.add_argument("--disable", action="store_true", help="release the session claim")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None, help="record but never suspend")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="really suspend")
    parser.add_argument("--scan", action="store_true", help="force one scan and print the outcomes")
    parser.add_argument("--probe", action="store_true", help="only check that Focused answers")
    parser.add_argument("--watch", type=float, default=0.0, metavar="SECONDS",
                        help="poll the event feed for this long, like the in-game overlay")
    parser.add_argument("--interval", type=float, default=1.0, metavar="SECONDS", help="poll interval while watching")
    parser.add_argument("--history", type=int, default=5, metavar="N",
                        help="how many already-logged events to show before watching (default 5)")
    args = parser.parse_args(argv)

    base = args.url.rstrip("/")

    code, payload = call(base, "GET", API + "/focus", token=args.token)
    if code != 200:
        print("cannot reach Focused at %s (%s)" % (base, payload.get("error")), file=sys.stderr)
        print("start it with: systemctl --user start focused.service", file=sys.stderr)
        return 4
    print("connected: Focused %s (pid %s)" % (payload.get("version"), payload.get("pid")))

    if args.probe:
        return 0

    # 1. push the rule set, exactly as the plugin does on every (re)connect
    if args.names is not None or args.cmdline is not None or args.protect is not None:
        body = {
            "names": split_list(args.names or ""),
            "cmdline_substrings": split_list(args.cmdline or ""),
            "pids": [],
            "protect_names": [],
            "protect_cmdline_substrings": split_list(args.protect or ""),
        }
        code, response = call(base, "PUT", API + "/focus/rules", body, token=args.token)
        if code != 200:
            print("rule push rejected: %s" % json.dumps(response, ensure_ascii=False), file=sys.stderr)
            return 5
        applied = response.get("applied", {})
        print("rules accepted: names=%s cmdline=%s (protect list now %d entries)"
              % (applied.get("names"), applied.get("cmdline_substrings"),
                 len(applied.get("protect_names") or [])))

    # 2. session claim on / off, with the same lease the plugin sends
    if args.enable:
        code, response = call(base, "POST", API + "/focus/state",
                              {"active": True, "dry_run": bool(args.dry_run), "source": "simulate-plugin",
                               "ttl_seconds": 30}, token=args.token)
        print("session -> %s" % response.get("active"))
    if args.disable:
        code, response = call(base, "POST", API + "/focus/state",
                              {"active": False, "source": "simulate-plugin"}, token=args.token)
        print("session -> %s" % response.get("active"))
    if args.dry_run is not None and not (args.enable or args.disable):
        code, response = call(base, "POST", API + "/focus/state",
                              {"active": True, "dry_run": bool(args.dry_run),
                               "source": "simulate-plugin", "ttl_seconds": 30}, token=args.token)
        print("dry-run -> %s (claimed a session to carry it)" % response.get("active"))

    # 3. status, then the event feed
    code, status = call(base, "GET", API + "/status", token=args.token)
    if code == 200:
        print_status(status)

    if args.scan:
        code, response = call(base, "POST", API + "/scan", {}, token=args.token)
        outcomes = response.get("frozen") or []
        print("--- forced scan ---")
        if not outcomes:
            print("  nothing matched")
        for outcome in outcomes:
            print("  pid %-7s %-14s %-16s %s" % (
                outcome.get("pid"), outcome.get("result"), outcome.get("signal"),
                outcome.get("name") or outcome.get("detail")))

    if args.watch > 0:
        print("--- watching the event feed for %.1fs (Ctrl-C to stop) ---" % args.watch)
        _, first = call(base, "GET", API + "/events?since=0", token=args.token)
        history = first.get("events") or []

        # The plugin deliberately establishes a baseline, so history from before
        # the game started is not counted as "intercepted this session". For a
        # debugging tool that silence is confusing, so show the tail once.
        if args.history > 0 and history:
            shown = history[-args.history:]
            print("  --- already logged (last %d of %d) ---" % (len(shown), len(history)))
            for event in shown:
                print(format_event(event))

        cursor = first.get("next_seq", 1) - 1
        deadline = time.time() + args.watch
        intercepted = 0
        try:
            while time.time() < deadline:
                time.sleep(max(0.2, args.interval))
                code, events = call(base, "GET", API + "/events?since=%d" % cursor, token=args.token)
                if code != 200:
                    continue
                cursor = max(cursor, events.get("next_seq", cursor + 1) - 1)
                for event in events.get("events") or []:
                    print(format_event(event))
                    if event.get("kind") == "freeze":
                        intercepted += 1
        except KeyboardInterrupt:
            pass

        _, status = call(base, "GET", API + "/status", token=args.token)
        stats = status.get("stats", {}) if isinstance(status, dict) else {}
        print("--- suspended during this watch: %d | totals: matched=%s frozen=%s "
              "thawed=%s skipped=%s ---" % (
                  intercepted, stats.get("matched"), stats.get("frozen"),
                  stats.get("thawed"), stats.get("freeze_skipped")))

    return 0


if __name__ == "__main__":
    sys.exit(main())
