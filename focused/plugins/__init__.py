"""Public plugin API for the rest of the application.

Split into a registry (persistence/tokens/capabilities) and a loader (in-process
modules); importing ``focused.plugins`` keeps the old names working.
"""

from .loader import AddonResult, load_addons
from .registry import (
    KIND_HTTP,
    KIND_PYTHON,
    STATUS_DISABLED,
    STATUS_ENABLED,
    STATUS_FAILED,
    PluginRecord,
    PluginRegistry,
    slugify,
)

__all__ = [
    "AddonResult",
    "KIND_HTTP",
    "KIND_PYTHON",
    "PluginRecord",
    "PluginRegistry",
    "STATUS_DISABLED",
    "STATUS_ENABLED",
    "STATUS_FAILED",
    "load_addons",
    "slugify",
]
