#!/usr/bin/env python3
"""focused -- the standalone Focused application.

It finds the applications on your blacklist and kills them while a focus
session runs.  Nothing is resumed: a killed application stays dead until you
start it again.

Three ways to run a session:

    focused --session 25        # start here, run 25 minutes, exit
    focused                     # run until SIGINT/SIGTERM (driven by its API)
    curl -XPOST .../focus/state -d '{"active": true}'   # driven by ChillFocus

Config lives in ``~/.config/focused/config.json``.  ``enforcement.mode`` decides
whether a match is killed or only recorded: it defaults to ``audit``.

    focused --write-default-config ~/.config/focused/config.json
    focused --status            # ask a running instance what it is doing
    focused --once              # one scan, print the report, exit
    focused --audit             # record matches without killing anything
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
# The engine lives inside this package (``focused.core``), so the only thing
# that has to be on the path is the directory holding the ``focused`` package --
# which differs between a checkout and an installed copy.
for _candidate in (_HERE, _REPO):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from focused import __version__  # noqa: E402
from focused.config import Config, FocusedConfigError  # noqa: E402
from focused.engine import FocusedEngine  # noqa: E402
from focused.server import API_PREFIX, FocusedServer  # noqa: E402
from focused.core.audit import AuditLog  # noqa: E402

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_RUNTIME_ERROR = 3
EXIT_UNREACHABLE = 4

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
LOG_DATEFMT = "%H:%M:%S"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="focused",
        description=(
            "Standalone focus application: kill the applications on your "
            "blacklist while a session runs. Loopback HTTP API for ChillFocus "
            "and the browser extension."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="focused " + __version__)
    parser.add_argument("--config", metavar="PATH", help="path to config.json")
    parser.add_argument("--write-default-config", metavar="PATH",
                        help="write a default config and exit")
    parser.add_argument("--print-config", action="store_true",
                        help="print the effective configuration as JSON and exit")
    parser.add_argument("--status", action="store_true",
                        help="query a running instance and print its status, then exit")
    parser.add_argument("--session", type=float, metavar="MINUTES",
                        help="start a session of this many minutes and run until it ends")
    parser.add_argument("--once", action="store_true",
                        help="perform a single scan, print the report and exit")
    parser.add_argument("--benchmark-scan", type=int, nargs="?", const=20, metavar="N",
                        help="measure N scans of the real /proc and exit (no killing)")
    parser.add_argument("--audit", action="store_true",
                        help="record what would be killed without signalling anything")
    parser.add_argument("--host", help="override http.host (loopback only)")
    parser.add_argument("--port", type=int, help="override http.port")
    parser.add_argument("--no-http", action="store_true", help="do not start the HTTP API")
    parser.add_argument("--log-level", choices=("debug", "info", "warning", "error"),
                        help="override log_level")
    parser.add_argument("--quiet", action="store_true", help="only log warnings and errors")
    parser.add_argument("--add-name", metavar="NAME", action="append", default=[],
                        help="add a name to the blacklist and exit (repeatable)")
    parser.add_argument("--add-url", metavar="PATTERN", action="append", default=[],
                        help="add a pattern to the browser blacklist and exit (repeatable)")
    return parser


def setup_logging(level_name: str, quiet: bool = False) -> logging.Logger:
    level = getattr(logging, (level_name or "info").upper(), logging.INFO)
    if quiet:
        level = max(level, logging.WARNING)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    root = logging.getLogger("focused")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    return root


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.audit:
        config.enforcement.mode = "audit"
    if args.host:
        config.http.host = args.host
    if args.port is not None:
        config.http.port = args.port
    if args.no_http:
        config.http.enabled = False
    if args.log_level:
        config.log_level = args.log_level
    config._validate()
    return config


def build_engine(config: Config, logger: logging.Logger) -> FocusedEngine:
    engine = FocusedEngine(config, logger=logger)
    engine.audit = AuditLog(config.audit_log, logger=logger) if config.audit_log else None
    return engine


def fetch_status(config: Config) -> dict:
    url = "http://%s:%d%s/status" % (config.http.host, config.http.port, API_PREFIX)
    request = urllib.request.Request(url, method="GET")
    if config.http.token:
        request.add_header("X-Focused-Token", config.http.token)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def benchmark_scan(config: Config, rounds: int) -> int:
    """Measure the real ``/proc`` scan without starting HTTP or freezing.

    This is the number the refactor's performance budget is written against:
    ``focused --benchmark-scan 20`` on the machine that will actually run
    Focused.  The synthetic benchmark in ``.dshtmp/`` is for development; this
    one uses the live process table and the configured rules.
    """
    import statistics

    from focused.core.procfs import ProcFS
    from focused.core.decision import EvalContext, evaluate

    procfs = ProcFS()
    rules = config.rules
    context = EvalContext(
        uid=os.getuid(),
        self_pid=os.getpid(),
        self_ppid=os.getppid(),
        self_pgid=-1,
        hard_protect_pids=frozenset({os.getpid(), 1, 0}),
        protect_own_process_group=False,
    )

    # Warm the compiled-pattern cache; the first call is not what we measure.
    for proc in procfs.snapshot():
        evaluate(proc, rules, context)

    snapshots = []
    evaluations = []
    processes = 0
    for _ in range(max(1, int(rounds))):
        started = time.perf_counter()
        procs = procfs.snapshot()
        after_snapshot = time.perf_counter()
        for proc in procs:
            evaluate(proc, rules, context)
        after_eval = time.perf_counter()
        processes = len(procs)
        snapshots.append((after_snapshot - started) * 1000.0)
        evaluations.append((after_eval - after_snapshot) * 1000.0)

    def summary(values):
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int(round(len(ordered) * 0.95)) - 1))
        return {
            "p50": round(statistics.median(ordered), 2),
            "p95": round(ordered[index], 2),
            "min": round(ordered[0], 2),
            "max": round(ordered[-1], 2),
        }

    totals = [snapshot + evaluation for snapshot, evaluation in zip(snapshots, evaluations)]
    total_p50 = statistics.median(totals)
    print(json.dumps(
        {
            "ok": True,
            "processes": processes,
            "protect_patterns": len(rules.protect_names),
            "blacklist_names": len(rules.names),
            "rounds": len(totals),
            "snapshot_ms": summary(snapshots),
            "evaluate_ms": summary(evaluations),
            "total_ms": summary(totals),
            "cpu_percent_at_1s": round(total_p50 / 10.0, 2),
            "cpu_percent_at_0_2s": round(total_p50 / 2.0, 2),
        },
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ))
    return EXIT_OK


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = Config.load(args.config)
        apply_overrides(config, args)
    except FocusedConfigError as exc:
        print("configuration error: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR

    logger = setup_logging(config.log_level, quiet=args.quiet)

    if args.write_default_config:
        path = os.path.expanduser(args.write_default_config)
        Config.defaults().save(path)
        print("wrote %s" % path)
        return EXIT_OK

    if args.print_config:
        print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return EXIT_OK

    if args.benchmark_scan is not None:
        return benchmark_scan(config, args.benchmark_scan)

    if args.status:
        try:
            print(json.dumps(fetch_status(config), indent=2, ensure_ascii=False, sort_keys=True))
            return EXIT_OK
        except urllib.error.HTTPError as exc:
            print("focused returned HTTP %s: %s" % (exc.code, exc.reason), file=sys.stderr)
            return EXIT_UNREACHABLE
        except Exception as exc:
            print(
                "cannot reach focused at %s:%d (%s)"
                % (config.http.host, config.http.port, exc),
                file=sys.stderr,
            )
            return EXIT_UNREACHABLE

    if args.add_name or args.add_url:
        engine = build_engine(config, logger)
        try:
            if args.add_name:
                names = sorted(
                    set(config.rules.names)
                    | {name.strip() for name in args.add_name if name.strip()}
                )
                engine.update_config({"blacklist": {"names": names}})
            if args.add_url:
                patterns = list(config.urls.patterns) + [
                    item.strip() for item in args.add_url if item.strip()
                ]
                engine.update_config({"urls": {"patterns": patterns}})
        except ValueError as exc:
            print("configuration error: %s" % exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR
        current = engine.config_dict()
        print(json.dumps(
            {"ok": True, "blacklist": current["blacklist"]["names"],
             "url_patterns": current["urls"]["patterns"]},
            indent=2, ensure_ascii=False, sort_keys=True,
        ))
        return EXIT_OK

    engine = build_engine(config, logger)
    failed_plugins = engine.load_plugins(config.plugins_path)
    for failure in failed_plugins:
        logger.error("plugin %s failed to load: %s", failure["id"], failure["error"])
    if engine.addons():
        logger.info("in-process plugins: %s", ", ".join(engine.addons()))
    for problem in config.warnings:
        logger.warning(problem)

    if args.session:
        engine.start_session(args.session)

    if args.once:
        result = engine.scan_once()
        if not engine.active():
            logger.info("no session is running, so nothing was scanned; pass --session")
        print(json.dumps(
            {"ok": True, "scan": result.to_wire(), "status": engine.status()},
            indent=2, ensure_ascii=False, sort_keys=True,
        ))
        return EXIT_OK

    stop_event = threading.Event()

    def handle_stop(signum, _frame):
        logger.info("received %s, shutting down", signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    for name in ("SIGHUP",):
        try:
            signal.signal(getattr(signal, name), lambda *_: None)
        except (AttributeError, ValueError):
            pass
    server = None
    if config.http.enabled:
        server = FocusedServer(
            engine,
            host=config.http.host,
            port=config.http.port,
            token=config.http.token,
            logger=logger,
        )
        bound = server.start()
        logger.info("http api on http://%s:%d (dashboard at /)", config.http.host, bound)

    if args.session:
        # A session given on the command line ends by itself: no reason to keep
        # running once it is over and everything is resumed.
        def end_when_finished():
            while not stop_event.is_set():
                if not engine.active():
                    logger.info("session finished, shutting down")
                    stop_event.set()
                    return
                stop_event.wait(1.0)

        threading.Thread(target=end_when_finished, name="focused-session", daemon=True).start()

    scan_thread = threading.Thread(
        target=engine.run_forever, args=(stop_event,), name="focused-scan", daemon=True
    )
    scan_thread.start()

    try:
        while not stop_event.is_set():
            stop_event.wait(0.25)
    finally:
        stop_event.set()
        scan_thread.join(timeout=5)
        if server is not None:
            server.stop()
        if getattr(engine, "audit", None) is not None:
            engine.audit.close()
        logger.info("stopped")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
