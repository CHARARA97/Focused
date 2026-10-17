"""One log line shape for the whole application.

A human reads the journal; a test may look for a reason code; a future UI may
parse the fields.  All three are easier when a line starts with a stable event
name and then carries ``key=value`` fields:

    event=freeze.refuse pid=4242 name=firefox reason=shared-unit rule=blacklist.name:firefox

Values with spaces are JSON-quoted so the line remains unambiguous.  This module
is deliberately tiny and dependency-free; it is called from the hot path, so it
must not build strings when no logger is configured.
"""

from __future__ import annotations

import json
from typing import Any


def log_event(logger, level: str, event: str, **fields: Any) -> None:
    """Log one structured event, or do nothing when there is no logger."""
    if logger is None:
        return
    parts = ["event=" + event]
    for key in sorted(fields):
        value = fields[key]
        if value is None or value == "":
            continue
        if isinstance(value, str):
            text = json.dumps(value, ensure_ascii=False) if any(
                ch.isspace() for ch in value
            ) or "=" in value else value
        else:
            text = str(value)
        parts.append("%s=%s" % (key, text))
    getattr(logger, level, logger.info)(" ".join(parts))
