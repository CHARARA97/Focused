from __future__ import annotations

from . import apps, log, overview, plugins, settings, urls

ORDER = ("overview", "apps", "urls", "plugins", "settings", "log")

TAB_MODULES = {
    "overview": overview,
    "apps": apps,
    "urls": urls,
    "plugins": plugins,
    "settings": settings,
    "log": log,
}
