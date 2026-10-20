"""The registry file: one JSON envelope, written atomically, never world-readable."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Sequence


class RegistryStore:
    def __init__(
        self, *, clock: Callable[[], float], log: Callable[[str, str], None]
    ) -> None:
        self._clock = clock
        self._log = log

    def read(self, path: str) -> List[Dict[str, Any]]:
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            self._log("error", "plugin registry unreadable (%s): %s" % (path, exc))
            return []
        items = data.get("plugins") if isinstance(data, dict) else None
        return [item for item in (items or ()) if isinstance(item, dict)]

    def write(self, path: str, records: Sequence[Any]) -> None:
        payload = {
            "version": 1,
            "updated": self._clock(),
            "plugins": [record.to_dict() for record in records],
        }
        parent = os.path.dirname(path)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            self._log("error", "cannot write plugin registry %s: %s" % (path, exc))
