#!/usr/bin/env bash
#
# Run the Focused backend test suite.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Focused: engine, application and plugin-platform tests (python)"
cd "$ROOT"
PYTHONPATH=. python3 "$ROOT/scripts/test-runner.py" "$@"
