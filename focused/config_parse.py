from __future__ import annotations

from typing import Any, Dict, List

from .config_errors import FocusedConfigError
from .domain.policy import ENFORCEMENT_MODES
from .domain.session_timer import SESSION_MODES


def object_field(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FocusedConfigError("%s must be an object" % key)
    return value


def enforcement_mode(data: Dict[str, Any], key: str, default: str) -> str:
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, str) and value.strip().lower() in ENFORCEMENT_MODES:
        return value.strip().lower()
    raise FocusedConfigError(
        "enforcement.mode must be one of: %s" % ", ".join(ENFORCEMENT_MODES)
    )


def session_mode(data: Dict[str, Any], key: str, default: str) -> str:
    value = data.get(key)
    if isinstance(value, str) and value.strip().lower() in SESSION_MODES:
        return value.strip().lower()
    return default


def bool_field(data: Dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    raise FocusedConfigError("config.%s must be true or false" % key)


def int_field(data: Dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FocusedConfigError("%s must be an integer" % key)
    return value


def float_field(data: Dict[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FocusedConfigError("%s must be a number" % key)
    return float(value)


def str_field(data: Dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise FocusedConfigError("%s must be a string" % key)
    return value


def str_list_field(data: Dict[str, Any], key: str) -> List[str]:
    value = data.get(key) or []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise FocusedConfigError("%s must be an array of strings" % key)
    return [item.strip() for item in value if item.strip()]


def int_list_field(data: Dict[str, Any], key: str) -> List[int]:
    value = data.get(key) or []
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise FocusedConfigError("%s must be an array of integers" % key)
    return list(value)
