from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .config_errors import FocusedConfigError
from .core.protect import DEFAULT_PROTECT_NAMES

APP_NAME = "focused"


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, APP_NAME)


def data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, APP_NAME)


def default_config_path() -> str:
    override = os.environ.get("FOCUSED_CONFIG")
    if override:
        return os.path.expanduser(override)
    return os.path.join(config_dir(), "config.json")


def default_audit_path() -> str:
    return os.path.join(data_dir(), "audit.jsonl")


def protect_names_path() -> Path:
    return Path(config_dir()) / "protect_names.txt"


def load_protect_names() -> frozenset:
    names = {name.lower() for name in DEFAULT_PROTECT_NAMES}
    try:
        with open(protect_names_path(), "r", encoding="utf-8") as handle:
            for line in handle:
                entry = line.strip()
                if entry and not entry.startswith("#"):
                    names.add(entry.lower())
    except OSError:
        pass
    return frozenset(names)


def read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise FocusedConfigError("%s is not valid JSON: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise FocusedConfigError("%s must contain a JSON object" % path)
    return data


def write_json(path: str, payload: Dict[str, Any]) -> str:
    target = os.path.expanduser(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return target
