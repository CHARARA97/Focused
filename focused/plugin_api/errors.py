"""The one exception type a plugin should catch."""

from __future__ import annotations


class PluginError(Exception):
    """A plugin request the host refused, with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__("%s: %s" % (code, message))
