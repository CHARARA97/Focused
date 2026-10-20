import unittest

from focused.app.enforcer import Enforcer
from focused.domain.tick import Tick
from focused.app.health import Health
from focused.app.history import DenyBook, EpisodeBook
from focused.domain.preflight import Verdict
from focused.domain.intent import Intent
from focused.domain.policy import Policy
from focused.domain.reasons import DenyReason, EventKind, Gate, KillResult, Mode, Outcome
from focused.domain.targets import ProcessFacts, Selection

UID = 1000


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeKiller:
    def __init__(self, results=None):
        self.calls = []
        self.results = results

    def kill_subtree(self, selection):
        self.calls.append(selection)
        if self.results is not None:
            return dict(self.results)
        return {pid: KillResult.SENT for pid in selection.pids}


class ListRecorder:
    def __init__(self):
        self.events = []
        self.health = Health()
        self.stats = {}

    def write(self, event):
        self.events.append(event)

    def record(self, payload):
        self.events.append(payload)

    def bump(self, key):
        self.stats[key] = int(self.stats.get(key) or 0) + 1

    def kill_failure(self, name):
        self.health.record_kill_failure(name)
        self.bump("kill_failed")

    def respawn(self, name, count, threshold):
        self.health.note_respawn(name, count, threshold)

    def kinds(self):
        return [
            event.kind if hasattr(event, "kind") else event.get("kind")
            for event in self.events
        ]


def selection(pids=(1000,), name="firefox", starttimes=None, rule="blacklist.name:firefox"):
    root = pids[0]
    return Selection(
        facts=ProcessFacts(
            pid=root, uid=UID, name=name, is_zombie=False, is_kernel_thread=False
        ),
        pids=tuple(pids),
        starttimes=dict(starttimes or {pid: 5000 + pid for pid in pids}),
        rule=rule,
    )


def tick(mode=Mode.KILL, mono=10.0, scan=7, intent="work", policy=3):
    gate = Gate.WORK if intent == "work" else Gate(intent)
    return Tick(
        scan=scan,
        mono=mono,
        intent=Intent(enforcing=gate is Gate.WORK, reason=gate),
        policy=Policy(mode=mode, max_per_scan=20, version=policy),
    )


def enforcer(killer=None, clock=None, health=None):
    clock = clock or FakeClock()
    recorder = ListRecorder()
    if health is not None:
        recorder.health = health
    instance = Enforcer(
        killer=killer or FakeKiller(),
        reporting=recorder,
        clock=clock,
        history=EpisodeBook(window=60.0),
        denies=DenyBook(window=60.0),
    )
    return instance, recorder, clock, recorder.health


class AuditModeTests(unittest.TestCase):
    def test_audit_sends_no_signal(self):
        killer = FakeKiller()
        instance, _, _, _ = enforcer(killer)
        instance.enforce(selection(), tick=tick(mode=Mode.AUDIT))
        self.assertEqual([], killer.calls)

    def test_audit_reports_would_kill(self):
        instance, recorder, _, _ = enforcer()
        outcome = instance.enforce(selection(), tick=tick(mode=Mode.AUDIT))
        self.assertEqual(Outcome.AUDIT, outcome.kind)
        self.assertEqual([EventKind.WOULD_KILL], recorder.kinds())

    def test_the_would_kill_event_carries_the_whole_decision(self):
        instance, recorder, _, _ = enforcer()
        instance.enforce(selection(pids=(1000, 1001)), tick=tick(mode=Mode.AUDIT))
        fields = recorder.events[0].fields()

        self.assertEqual(7, fields["scan"])
        self.assertEqual("work", fields["intent"])
        self.assertEqual("audit", fields["mode"])
        self.assertEqual(3, fields["policy"])
        self.assertEqual(1000, fields["pid"])
        self.assertEqual(6000, fields["starttime"])
        self.assertEqual([1000, 1001], fields["pids"])
        self.assertEqual(2, fields["tree"])
        self.assertEqual("allow", fields["verdict"])


class KillModeTests(unittest.TestCase):
    def test_kill_signals_the_subtree(self):
        killer = FakeKiller()
        instance, _, _, _ = enforcer(killer)
        instance.enforce(selection(pids=(1000, 1001)), tick=tick())
        self.assertEqual(1, len(killer.calls))

    def test_a_kill_is_reported_once_per_episode(self):
        instance, recorder, _, _ = enforcer()
        instance.enforce(selection(), tick=tick(mono=10.0))
        instance.enforce(selection(), tick=tick(mono=11.0))
        self.assertEqual([EventKind.KILL], recorder.kinds())

    def test_a_kill_after_the_window_is_reported_again(self):
        instance, recorder, _, _ = enforcer()
        instance.enforce(selection(), tick=tick(mono=10.0))
        instance.enforce(selection(), tick=tick(mono=200.0))
        self.assertEqual([EventKind.KILL, EventKind.KILL], recorder.kinds())

    def test_a_failed_signal_is_reported_and_counted(self):
        killer = FakeKiller({1000: KillResult.DENIED})
        instance, recorder, _, health = enforcer(killer)
        instance.enforce(selection(), tick=tick())

        self.assertIn(EventKind.SIGNAL_FAILED, recorder.kinds())
        self.assertEqual(1, health.kill_failures)
        failed = [e for e in recorder.events if e.kind is EventKind.SIGNAL_FAILED][0]
        self.assertEqual("denied", failed.fields()["result"])

    def test_a_reused_root_is_reported_as_unverified_or_reused(self):
        killer = FakeKiller({1000: KillResult.REUSED})
        instance, recorder, _, _ = enforcer(killer)
        instance.enforce(selection(), tick=tick())
        self.assertNotIn(EventKind.SIGNAL_FAILED, recorder.kinds())


class DenyTests(unittest.TestCase):
    def test_the_first_deny_is_reported_with_its_reason(self):
        instance, recorder, _, _ = enforcer()
        verdict = Verdict(False, DenyReason.TARGET_IS_ANCESTOR)
        instance.deny(selection(name="systemd"), verdict, tick())

        self.assertEqual([EventKind.DENY], recorder.kinds())
        fields = recorder.events[0].fields()
        self.assertEqual("target-is-ancestor", fields["reason"])
        self.assertEqual("deny", fields["verdict"])

    def test_repeated_denies_are_not_reported_again(self):
        instance, recorder, _, _ = enforcer()
        verdict = Verdict(False, DenyReason.PROTECTED)
        instance.deny(selection(name="bash"), verdict, tick(mono=10.0))
        instance.deny(selection(name="bash"), verdict, tick(mono=11.0))
        self.assertEqual([EventKind.DENY], recorder.kinds())

    def test_a_different_reason_is_a_different_deny(self):
        instance, recorder, _, _ = enforcer()
        instance.deny(selection(), Verdict(False, DenyReason.PROTECTED), tick())
        instance.deny(selection(), Verdict(False, DenyReason.SELF_IN_TREE), tick())
        self.assertEqual([EventKind.DENY, EventKind.DENY], recorder.kinds())


class SweepTests(unittest.TestCase):
    def test_a_repeated_kill_becomes_one_summary(self):
        instance, recorder, _, _ = enforcer()
        for second in range(3):
            instance.enforce(selection(), tick=tick(mono=10.0 + second))
        recorder.events.clear()

        instance.sweep(tick(mono=100.0))

        self.assertEqual([EventKind.KILL_SUMMARY], recorder.kinds())
        fields = recorder.events[0].fields()
        self.assertEqual(3, fields["count"])
        self.assertEqual("firefox", fields["name"])

    def test_a_single_kill_is_not_summarised(self):
        instance, recorder, _, _ = enforcer()
        instance.enforce(selection(), tick=tick(mono=10.0))
        recorder.events.clear()

        instance.sweep(tick(mono=100.0))

        self.assertEqual([], recorder.kinds())

    def test_a_repeated_deny_becomes_one_summary(self):
        instance, recorder, _, _ = enforcer()
        verdict = Verdict(False, DenyReason.PROTECTED)
        for second in range(3):
            instance.deny(selection(name="bash"), verdict, tick(mono=10.0 + second))
        recorder.events.clear()

        instance.sweep(tick(mono=100.0))

        self.assertEqual([EventKind.DENY_SUMMARY], recorder.kinds())
        self.assertEqual(3, recorder.events[0].fields()["count"])



if __name__ == "__main__":
    unittest.main()
