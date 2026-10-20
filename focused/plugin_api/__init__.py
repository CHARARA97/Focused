"""Public plugin contract.

The implementation is split into capabilities, errors, manifest and API; this
module keeps the historical ``focused.plugin_api`` imports working.
"""

from .api import PluginAPI
from .capabilities import (
    ALL_CAPABILITIES,
    CAP_EVENTS_READ,
    CAP_FOCUS_OVERRIDE,
    CAP_PROCESS_CONTROL,
    CAP_RULES_READ,
    CAP_RULES_WRITE,
    CAP_SCAN_REQUEST,
    CAP_SESSION_CLAIM,
    CAP_STATE_HISTORY,
    CAP_STATE_READ,
    DEFAULT_CAPABILITIES,
    SAFE_CAPABILITIES,
    normalize_capabilities,
)
from .errors import PluginError
from .manifest import PluginManifest

__all__ = [
    "ALL_CAPABILITIES",
    "CAP_EVENTS_READ",
    "CAP_FOCUS_OVERRIDE",
    "CAP_PROCESS_CONTROL",
    "CAP_RULES_READ",
    "CAP_RULES_WRITE",
    "CAP_SCAN_REQUEST",
    "CAP_SESSION_CLAIM",
    "CAP_STATE_HISTORY",
    "CAP_STATE_READ",
    "DEFAULT_CAPABILITIES",
    "PluginAPI",
    "PluginError",
    "PluginManifest",
    "SAFE_CAPABILITIES",
    "normalize_capabilities",
]
