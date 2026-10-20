from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from ..core.proctree import live_subtree
from ..domain.preflight import Context as PreflightContext
from ..domain.identity import Identity
from ..domain.targets import ProcessFacts, Selection, check_starttimes, subtree
from ..ports import ProcessReader


@dataclass(frozen=True)
class Candidate:
    selection: Selection
    context: PreflightContext


class Targeting:
    def __init__(
        self,
        *,
        procfs: ProcessReader,
        matcher: object,
        identity: Identity,
    ) -> None:
        self._procfs = procfs
        self._matcher = matcher
        self._identity = identity

    @property
    def hard_protect_pids(self):
        return self._hard_protect_pids

    def resolve(
        self,
        proc: object,
        rule: str,
        *,
        parents: Mapping[int, Optional[int]],
        rows: Dict[int, object],
    ) -> Optional[Candidate]:
        tree = live_subtree(
            proc.pid, self._procfs, fallback=subtree(proc.pid, parents)
        )
        members: List[int] = []
        for pid in tree:
            row = rows.get(pid)
            if row is None:
                row = self._procfs.read(pid)
                if row is None:
                    continue
                rows[pid] = row
            members.append(int(pid))
        if not members or members[0] != proc.pid:
            return None

        resolved = check_starttimes(
            members, {pid: rows[pid].starttime for pid in members}
        )
        selection = Selection(
            facts=ProcessFacts(
                pid=proc.pid,
                uid=proc.uid,
                name=proc.display_name(),
                is_zombie=proc.is_zombie,
                is_kernel_thread=proc.is_kernel_thread,
            ),
            pids=tuple(members),
            starttimes=resolved.starttimes,
            rule=rule,
        )
        context = PreflightContext(
            self_pid=self._identity.pid,
            self_ppid=self._identity.ppid,
            self_uid=self._identity.uid,
            hard_protect_pids=self._identity.hard_protect_pids,
            parents=parents,
            uids={pid: rows[pid].uid for pid in members},
            candidates={pid: rows[pid].name_candidates() for pid in members},
            is_protected=self._matcher.protects_names,
        )
        return Candidate(selection=selection, context=context)
