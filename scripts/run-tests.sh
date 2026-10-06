#!/usr/bin/env bash
#
# Run the backend's own tests.
#
#   scripts/run-tests.sh [--offline]
#
# The engine tests spawn real processes and really suspend them: that is the point
# of them, and nothing outside those short-lived children is ever touched.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --offline) shift ;;    # kept for symmetry: this suite needs no network
        --plain) PLAIN=1; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

echo "==> Focused: engine, application and plugin-platform tests"
# The runner shows a percentage bar on a terminal; in a log it prints only the
# summary.  Pass --plain to force that.
( cd "$ROOT" && PYTHONPATH="$ROOT" python3 "$ROOT/scripts/test-runner.py" "$@" )

echo
echo "all tests passed"
