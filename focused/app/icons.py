"""Best-effort process-icon lookup for the in-game picker.

The plugin runs inside Wine and cannot see host processes, so icons are resolved
here, on the same side as procfs. Everything is optional: an empty string simply
means the plugin should use its letter-tile fallback.
"""

from __future__ import annotations

import base64
import glob
import io
import os
import shlex
from typing import Dict, Optional

try:  # Pillow is already used by the dashboard/build tooling.
    from PIL import Image
except Exception:  # pragma: no cover - optional at runtime
    Image = None


class ProcessIcons:
    def __init__(self, icon_roots=None) -> None:
        self._cache: Dict[str, str] = {}
        self._desktop_icons: Optional[Dict[str, str]] = None
        self._icon_roots = list(icon_roots or (
            "/usr/share/icons",
            "/usr/local/share/icons",
            os.path.expanduser("~/.local/share/icons"),
            "/usr/share/pixmaps",
        ))

    def icon_for(self, name: str, executable: str = "") -> str:
        key = ((name or "") + "|" + (executable or "")).strip().lower()
        if not key.strip("|"):
            return ""
        if key in self._cache:
            return self._cache[key]
        value = self._resolve(name or "", executable or "")
        self._cache[key] = value
        return value

    def _resolve(self, name: str, executable: str = "") -> str:
        candidates = []
        exe_base = os.path.basename(executable) if executable else ""
        for lookup in (name.lower(), exe_base.lower()):
            icon = self._desktop_icon(lookup)
            if icon:
                candidates.append(icon)
        candidates.extend((name, exe_base))
        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            path = self._find_icon(candidate)
            if path:
                encoded = self._encode(path)
                if encoded:
                    return encoded
        return ""

    def _desktop_icon(self, name: str) -> str:
        if self._desktop_icons is None:
            self._desktop_icons = {}
            for pattern in (
                "/usr/share/applications/*.desktop",
                "/usr/local/share/applications/*.desktop",
                os.path.expanduser("~/.local/share/applications/*.desktop"),
            ):
                for path in glob.glob(pattern):
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as handle:
                            icon = ""
                            executables = []
                            names = []
                            wmclasses = []
                            for line in handle:
                                if line.startswith("Icon="):
                                    icon = line[5:].strip()
                                elif line.startswith("Exec="):
                                    executables.append(line[5:].strip())
                                elif line.startswith("Name="):
                                    names.append(line[5:].strip())
                                elif line.startswith("StartupWMClass="):
                                    wmclasses.append(line[10:].strip())
                            if not icon:
                                continue
                            stem = os.path.splitext(os.path.basename(path))[0].lower()
                            self._desktop_icons.setdefault(stem, icon)
                            for value in names + wmclasses:
                                if value:
                                    self._desktop_icons.setdefault(value.lower(), icon)
                            for command in executables:
                                try:
                                    words = shlex.split(command)
                                except ValueError:
                                    words = command.split()
                                for word in words:
                                    if not word or word.startswith("-"):
                                        continue
                                    base = os.path.basename(word).lower()
                                    if base:
                                        self._desktop_icons.setdefault(base, icon)
                                    break
                    except OSError:
                        continue
        direct = self._desktop_icons.get(name, "")
        if direct:
            return direct

        # Wine/comm often truncates to 15 chars ("appimagelaunche") or appends
        # ".exe"; match those against the desktop stem/Name as a fallback.
        key = os.path.splitext(name)[0].lower()
        for candidate, icon in self._desktop_icons.items():
            if len(candidate) < 4:
                continue
            if key.startswith(candidate) or candidate.startswith(key):
                return icon
        return ""

    def _find_icon(self, name: str) -> str:
        if not name:
            return ""
        if os.path.isabs(name) and os.path.isfile(name):
            return name
        if "." in os.path.basename(name):
            return ""
        base = os.path.basename(name)
        if base.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            return ""
        patterns = []
        for root in self._icon_roots:
            patterns.extend((
                os.path.join(root, "*", "*", "apps", base + ".png"),
                os.path.join(root, "*", "apps", base + ".png"),
                os.path.join(root, "apps", base + ".png"),
                os.path.join(root, "*", base + ".png"),
                os.path.join(root, base + ".png"),
            ))
        for pattern in patterns:
            matches = glob.glob(pattern)
            if matches:
                # Prefer the largest raster icon, not the shortest path.
                def score(path):
                    try:
                        return (os.path.getsize(path), path)
                    except OSError:
                        return (0, path)
                return sorted(matches, key=score, reverse=True)[0][1] if False else sorted(matches, key=score, reverse=True)[0]
        return ""

    @staticmethod
    def _encode(path: str) -> str:
        if Image is None:
            return ""
        try:
            with Image.open(path) as image:
                image = image.convert("RGBA")
                image.thumbnail((32, 32))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=True)
                return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return ""
