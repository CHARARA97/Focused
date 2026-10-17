#!/usr/bin/env python3
"""A tiny HTTP plugin, using only the standard library.

Register once, keep the returned plugin token, then push rules and a lease:

    python3 examples/http_plugin.py register --name demo
    python3 examples/http_plugin.py rules --plugin-token TOKEN --name firefox
    python3 examples/http_plugin.py session --plugin-token TOKEN --ttl 30
    python3 examples/http_plugin.py events --plugin-token TOKEN

The example does not claim a session unless you ask it to; a plugin that claims
and then dies is the case the lease exists to protect against.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8766"


def request(base, method, path, body=None, token=""):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["X-Focused-Token"] = token
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        print("HTTP %s: %s" % (exc.code, payload), file=sys.stderr)
        raise SystemExit(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("FOCUSED_URL", DEFAULT_BASE))
    parser.add_argument("--app-token", default=os.environ.get("FOCUSED_TOKEN", ""))
    parser.add_argument("--plugin-token", default=os.environ.get("FOCUSED_PLUGIN_TOKEN", ""))
    parser.add_argument("--plugin-id", default=os.environ.get("FOCUSED_PLUGIN_ID", "demo"))
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register")
    register.add_argument("--name", default="demo")
    register.add_argument("--version", default="0.1.0")

    rules = sub.add_parser("rules")
    rules.add_argument("--name", action="append", default=[])
    rules.add_argument("--cmdline", action="append", default=[])

    session = sub.add_parser("session")
    session.add_argument("--ttl", type=float, default=30)
    session.add_argument("--release", action="store_true")

    events = sub.add_parser("events")
    events.add_argument("--since", type=int, default=0)

    sub.add_parser("status")
    sub.add_parser("state")
    sub.add_parser("scan")
    args = parser.parse_args(argv)
    base = args.base.rstrip("/")

    if args.command == "register":
        payload = request(base, "POST", "/api/v1/plugins/register",
                          {"name": args.name, "version": args.version},
                          token=args.app_token)
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    token = args.plugin_token or args.app_token
    if not token:
        print("a plugin token is required (--plugin-token)", file=sys.stderr)
        return 2

    if args.command == "rules":
        payload = request(base, "PUT",
                          "/api/v1/plugins/%s/rules" % args.plugin_id,
                          {"names": args.name, "cmdline_substrings": args.cmdline},
                          token=token)
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "session":
        path = "/api/v1/plugins/%s/session" % args.plugin_id
        if args.release:
            payload = request(base, "POST", path, {"active": False}, token=token)
        else:
            payload = request(base, "POST", path,
                              {"active": True, "ttl_seconds": max(1.0, args.ttl)},
                              token=token)
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "events":
        payload = request(base, "GET", "/api/v1/events?since=%d" % args.since, token=token)
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "state":
        payload = request(base, "GET",
                          "/api/v1/plugins/%s/state" % args.plugin_id, token=token)
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "scan":
        payload = request(base, "POST",
                          "/api/v1/plugins/%s/scan" % args.plugin_id, {},
                          token=token)
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    payload = request(base, "GET", "/api/v1/focus", token=token)
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
