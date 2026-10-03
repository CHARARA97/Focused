"""Focused -- the standalone focus application.

It owns the "find the distracting application and suspend it" engine, keeps its
own configuration and state, and speaks a small loopback HTTP protocol so that
ChillFocus (the game mod's daemon) can hand a session over to it and the browser
extension can read the current session state.

Interface contract: ``docs/focused-protocol.md``.
"""

__version__ = "0.1.0"
