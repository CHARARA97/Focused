"""Where a process lives in the cgroup tree, and what may be frozen.

Freeze mode has to answer three questions *before* it may suspend anything, and
all three are pure path/text questions.  They live here, away from /proc and
/sys, so they can be unit-tested without a live system:

1. Which cgroup is this PID in?  :func:`parse_proc_cgroup`
2. Which unit does that cgroup belong to?  :func:`unit_of`
3. May it be frozen -- and as a whole unit or only as a process tree?
   :func:`plan_for`

The default answer is "no".  A refusal comes back as :data:`PLAN_SKIP` with a
reason the caller writes to the audit log, so "why was my browser not frozen?"
stays answerable.

Two hazards shape the rules:

* Freezing a cgroup that contains us -- or an ancestor of it -- would freeze
  *us*: the daemon stops, and nobody is left to thaw anything.  A whole-unit
  freeze is therefore never allowed there.  Suspending the matched process tree
  *is* still allowed, because that only ever touches PIDs the rule engine picked
  out; the engine additionally strips its own PID and the hard-protected ones
  from that tree, so a target that happens to be an ancestor of the daemon
  cannot take the daemon down with it.
* Freezing a cgroup that holds unrelated processes -- a terminal's scope, the
  compositor's own service, the session manager -- hits bystanders.  Only a
  cgroup holding a single application may be frozen whole; everything else
  degrades to freezing the matching process tree.

See ``docs/freeze-mode.md`` (sections 4.1 and 4.3) for the full rationale.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional

#: Freeze the whole cgroup: everything in it, now and later.
PLAN_UNIT = "unit"
#: Freeze just the matching process and its descendants.
PLAN_PID = "pid"
#: Do not freeze at all; ``reason`` says why.
PLAN_SKIP = "skip"

FREEZE_FILENAME = "cgroup.freeze"
EVENTS_FILENAME = "cgroup.events"
CGROUP_ROOT = "/sys/fs/cgroup"

#: Unit types that can hold an application we may freeze.
_UNIT_SUFFIXES = (".scope", ".service")


def read_self_cgroup(path: str = "/proc/self/cgroup") -> Optional[str]:
    """The daemon's own cgroup, read once at startup.

    Every unit freeze is checked against this: a cgroup that contains us -- or an
    ancestor of it -- must never be frozen, because nobody would be left to thaw
    it.  Unreadable means "unknown", which downgrades every plan to the narrower
    process-tree form rather than guessing.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return parse_proc_cgroup(handle.read())
    except OSError:
        return None


def is_unified_cgroup2(root: str = CGROUP_ROOT) -> bool:
    """Whether ``/sys/fs/cgroup`` is a cgroup2 mount.

    The freezer only exists there; on a v1 system freeze mode refuses instead of
    pretending.  Probed by the presence of a cgroup2-only file rather than by the
    filesystem magic, which keeps this dependency-free.
    """
    return os.path.exists(os.path.join(root, "cgroup.controllers"))


def parse_proc_cgroup(text: str) -> Optional[str]:
    """``/proc/<pid>/cgroup`` contents -> the cgroup path, or None.

    The file has one ``hierarchy:controllers:path`` line per hierarchy; on a
    unified system there is exactly one, ``0::/path``.  A process can sit at the
    root, which is reported as ``/``.  On a hybrid system the ``0::`` line is the
    one this module cares about, so it wins over whatever comes first.

    >>> parse_proc_cgroup("0::/user.slice/app.slice/app-firefox-7.scope\\n")
    '/user.slice/app.slice/app-firefox-7.scope'
    """
    if not text:
        return None
    fallback: Optional[str] = None
    for line in text.splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, path = (part.strip() for part in parts)
        if not path:
            continue
        if not path.startswith("/"):
            path = "/" + path
        if hierarchy == "0" and not controllers:
            return path
        if fallback is None:
            fallback = path
    return fallback


def unit_of(cgroup_path: Optional[str]) -> Optional[str]:
    """The unit name at the end of a cgroup path, or None at the root."""
    if not cgroup_path:
        return None
    name = cgroup_path.rstrip("/").rsplit("/", 1)[-1]
    return name or None


def is_dedicated_app_unit(cgroup_path: Optional[str]) -> bool:
    """Whether this cgroup holds one application and nothing else.

    Deliberately conservative: the systemd app-scope convention
    (``app.slice/app-<desktop-id>-<pid>.scope``) is the only shape we accept.  A
    false negative costs granularity -- we freeze the process tree instead -- so
    guessing wider would only add ways to hit bystanders.

    Compositor-spawned children are the reason this matters: a browser started by
    a session that does not create app scopes lands in the *compositor's own*
    cgroup, and freezing that would take the whole desktop with it.
    """
    unit = unit_of(cgroup_path)
    if not unit or not unit.endswith(_UNIT_SUFFIXES):
        return False
    if not unit.startswith(("app-", "app@")):
        return False
    return "/app.slice/" in (cgroup_path or "")


@dataclass(frozen=True)
class FreezePlan:
    """What the daemon may do with one target."""

    plan: str
    reason: str
    unit: Optional[str] = None

    @property
    def freezable(self) -> bool:
        return self.plan in (PLAN_UNIT, PLAN_PID)


def plan_for(
    cgroup_path: Optional[str],
    self_cgroup: Optional[str],
    dedicated: Optional[bool] = None,
    unified: bool = True,
) -> FreezePlan:
    """Decide how (or whether) a cgroup may be frozen.

    ``self_cgroup`` is the daemon's own cgroup.  When it is unknown the answer is
    never :data:`PLAN_UNIT`: without that reference point we cannot prove a unit
    does not contain us, and freezing ourselves is the one unrecoverable mistake.

    ``unified`` says whether ``/sys/fs/cgroup`` is a cgroup2 mount.  The freezer
    only exists there; on a v1 system this is a refusal, not a guess.
    """
    unit = unit_of(cgroup_path)
    if not cgroup_path or not unit:
        return FreezePlan(PLAN_SKIP, "unknown-cgroup")
    if not unified:
        return FreezePlan(PLAN_SKIP, "not-cgroup-v2", unit)

    contains_us = False
    if self_cgroup:
        own = self_cgroup.rstrip("/")
        target = cgroup_path.rstrip("/")
        contains_us = target == own or own.startswith(target + "/")

    if unit.endswith(".slice"):
        return FreezePlan(PLAN_SKIP, "slice", unit)
    if unit.startswith("user@") or unit == "init.scope":
        return FreezePlan(PLAN_SKIP, "session-manager", unit)
    if not unit.endswith(_UNIT_SUFFIXES):
        return FreezePlan(PLAN_SKIP, "not-a-unit", unit)

    decided = is_dedicated_app_unit(cgroup_path) if dedicated is None else dedicated
    if not decided:
        return FreezePlan(PLAN_PID, "shared-unit", unit)
    if contains_us:
        # A dedicated unit, but it is our own: freezing it whole would freeze the
        # daemon.  The process tree is still safe -- and it is what makes freeze
        # mode work at all when the user launched both from the same terminal.
        return FreezePlan(PLAN_PID, "self-cgroup", unit)
    if not self_cgroup:
        return FreezePlan(PLAN_PID, "unknown-self-cgroup", unit)
    return FreezePlan(PLAN_UNIT, "dedicated-app-unit", unit)


def freeze_file(cgroup_path: str) -> str:
    """The ``cgroup.freeze`` control file for a cgroup path."""
    return CGROUP_ROOT + cgroup_path.rstrip("/") + "/" + FREEZE_FILENAME


def events_file(cgroup_path: str) -> str:
    """The ``cgroup.events`` status file for a cgroup path."""
    return CGROUP_ROOT + cgroup_path.rstrip("/") + "/" + EVENTS_FILENAME


def parse_cgroup_events(text: str) -> Dict[str, str]:
    """``cgroup.events`` -> ``{"populated": "1", "frozen": "1"}``."""
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def is_frozen(text: str) -> bool:
    """Whether a ``cgroup.events`` reading says the cgroup is frozen.

    Freezing is asynchronous (the kernel may take a while to reach every task),
    and a cgroup-frozen task still reports ``S`` in ``/proc/<pid>/status`` -- so
    this file, not ``ps``, is the only way to confirm it worked.
    """
    return parse_cgroup_events(text).get("frozen") == "1"


def process_tree(
    root: int,
    parents: Mapping[int, Optional[int]],
) -> List[int]:
    """``root`` plus every descendant, breadth-first.

    Used when a cgroup cannot be frozen as a whole: freezing only the matched PID
    would leave a browser's renderer children running.  Cycle-safe, and children
    are returned in PID order so callers and tests are deterministic.
    """
    children: Dict[int, List[int]] = {}
    for pid, ppid in parents.items():
        if ppid is None:
            continue
        children.setdefault(int(ppid), []).append(int(pid))
    for group in children.values():
        group.sort()

    seen = {int(root)}
    out: List[int] = []
    queue = [int(root)]
    while queue:
        pid = queue.pop(0)
        out.append(pid)
        for child in children.get(pid, ()):
            if child in seen:
                continue
            seen.add(child)
            queue.append(child)
    return out


def describe(plan: FreezePlan, names: Iterable[str] = ()) -> str:
    """One audit/log line for a plan."""
    label = ", ".join(str(name) for name in names if name) or "-"
    text = "plan=%s target=%s" % (plan.plan, label)
    if plan.unit:
        text += " unit=%s" % plan.unit
    text += " reason=%s" % plan.reason
    return text
