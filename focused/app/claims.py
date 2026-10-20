"""Session claims: one lease per frontend, with idempotent renewal.

A frontend says "a focus session is running, and you may keep believing that for
N seconds".  The backend stores one claim per source; renewing an unchanged
claim is not an event, while creating, withdrawing or expiring one is.  That
distinction is what keeps a two-second heartbeat from writing an audit record,
a log line and a registry file every two seconds.

The class is deliberately not thread-safe: the engine already owns a lock
around every call.  Keeping the lock out of this module makes the state machine
trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Claim:
    """One frontend's promise that a session is running."""

    source: str
    #: Epoch seconds when the claim lapses; ``0`` means "until withdrawn".
    until: float
    #: The lease length the frontend asked for, for diagnostics.
    ttl: float

    def live(self, now: float) -> bool:
        return self.until == 0.0 or self.until > now


class ClaimBook:
    """The set of claims, with the transitions spelled out."""

    def __init__(self) -> None:
        self._claims: Dict[str, Claim] = {}

    # -- mutations ---------------------------------------------------------

    def claim(self, source: str, now: float, ttl_seconds: float = 0.0) -> bool:
        """Add or renew a claim.  Returns True only when it was not live."""
        ttl = max(1.0, float(ttl_seconds)) if ttl_seconds else 0.0
        until = now + ttl if ttl else 0.0
        previous = self._claims.get(source)
        changed = previous is None or not previous.live(now)
        self._claims[source] = Claim(source=source, until=until, ttl=ttl)
        return changed

    def withdraw(self, source: str) -> bool:
        """Remove a claim.  Returns True when there was one to remove."""
        return self._claims.pop(source, None) is not None

    def expire(self, now: float) -> List[str]:
        """Remove every lapsed claim and return its source names."""
        lapsed = [
            source
            for source, claim in self._claims.items()
            if claim.until and claim.until <= now
        ]
        for source in lapsed:
            del self._claims[source]
        return lapsed

    # -- reads -------------------------------------------------------------

    def live(self, now: float) -> Dict[str, Claim]:
        """Claims that still hold at ``now``."""
        return {
            source: claim
            for source, claim in self._claims.items()
            if claim.live(now)
        }

    def all(self) -> Dict[str, Claim]:
        """Every recorded claim, including lapsed ones not yet expired."""
        return dict(self._claims)

    def active(self, now: float) -> bool:
        return any(claim.live(now) for claim in self._claims.values())

    def nearest_deadline(self, now: float) -> float:
        """The earliest expiry among live claims; ``0`` when none has a deadline."""
        deadlines = [claim.until for claim in self.live(now).values() if claim.until]
        return min(deadlines) if deadlines else 0.0

    def nearest_ttl(self) -> float:
        """The shortest requested lease, for the status payload."""
        ttls = [claim.ttl for claim in self._claims.values() if claim.ttl]
        return min(ttls) if ttls else 0.0

    def describe(self, now: float) -> str:
        """Who is currently claiming, or who claimed last when nobody is."""
        live = self.live(now)
        if live:
            return ", ".join(sorted(live))
        return ", ".join(sorted(self._claims))

    # -- testing / lifecycle ----------------------------------------------

    def clear(self) -> None:
        self._claims.clear()
