from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


@dataclass
class Episode:
    name: str
    rule: str
    first_at: float
    last_at: float
    count: int = 0
    root_pids: Set[int] = field(default_factory=set)

    @property
    def roots(self) -> int:
        return len(self.root_pids)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_at - self.first_at)


@dataclass
class DenyWindow:
    reason: str
    name: str
    first_at: float
    last_at: float
    count: int = 0

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_at - self.first_at)


class DenyBook:
    def __init__(self, window: float = 60.0) -> None:
        self._window = float(window)
        self._windows: Dict[Tuple[str, str], DenyWindow] = {}

    def note(self, reason: str, name: str, now: float) -> bool:
        key = (reason, name)
        window = self._windows.get(key)
        if window is None or now - window.last_at >= self._window:
            self._windows[key] = DenyWindow(
                reason=reason, name=name, first_at=now, last_at=now, count=1
            )
            return True
        window.last_at = now
        window.count += 1
        return False

    def sweep(self, now: float) -> Tuple[DenyWindow, ...]:
        closed: List[DenyWindow] = []
        for key, window in list(self._windows.items()):
            if now - window.last_at < self._window:
                continue
            del self._windows[key]
            closed.append(window)
        return tuple(closed)

    def current(self) -> Tuple[DenyWindow, ...]:
        return tuple(self._windows[key] for key in sorted(self._windows))


class EpisodeBook:
    def __init__(self, window: float = 60.0) -> None:
        self._window = float(window)
        self._episodes: Dict[str, Episode] = {}

    @property
    def window(self) -> float:
        return self._window

    def note(self, name: str, rule: str, root_pid: int, now: float) -> bool:
        episode = self._episodes.get(name)
        if episode is None or now - episode.last_at >= self._window:
            self._episodes[name] = Episode(
                name=name,
                rule=rule,
                first_at=now,
                last_at=now,
                count=1,
                root_pids={int(root_pid)},
            )
            return True
        episode.last_at = now
        episode.count += 1
        episode.root_pids.add(int(root_pid))
        return False

    def count_of(self, name: str) -> int:
        episode = self._episodes.get(name)
        return episode.count if episode is not None else 0

    def sweep(self, now: float) -> Tuple[Episode, ...]:
        closed: List[Episode] = []
        for name, episode in list(self._episodes.items()):
            if now - episode.last_at < self._window:
                continue
            del self._episodes[name]
            closed.append(episode)
        return tuple(closed)

    def current(self) -> Tuple[Episode, ...]:
        return tuple(self._episodes[name] for name in sorted(self._episodes))
