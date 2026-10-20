"""The Focused dashboard: one page, no build step, no external resources.

The page is assembled from ``web/``: one module per tab (HTML + script), a shared
script, the shell and the stylesheet.  Editing a tab means editing its module;
editing the copy means editing ``web/text.py``.
"""

from .web import render

DASHBOARD_HTML = render()
