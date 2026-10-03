#!/usr/bin/env python3
"""Run Focused from a checkout: the real entry point is focused.cli."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from focused.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
