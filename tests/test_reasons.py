import unittest

from focused.domain.reasons import (
    ActionEvent,
    DenyReason,
    EventKind,
    Gate,
    KillResult,
    Mode,
    Outcome,
)


class StableValueTests(unittest.TestCase):
    def test_mode_values_are_the_config_strings(self):
        self.assertEqual({"audit", "kill"}, {mode.value for mode in Mode})

    def test_gate_values_name_every_reason_a_scan_did_not_run(self):
        self.assertEqual(
            {"work", "busy", "no-session", "break", "paused", "disabled"},
            {gate.value for gate in Gate},
        )

    def test_kill_result_values_are_the_log_words(self):
        self.assertEqual(
            {"sent", "gone", "reused", "denied", "unverified"},
            {result.value for result in KillResult},
        )

    def test_deny_reasons_are_unique(self):
        values = [reason.value for reason in DenyReason]
        self.assertEqual(len(values), len(set(values)))

    def test_event_kinds_are_namespaced(self):
        for kind in EventKind:
            self.assertTrue(kind.value.startswith(("enforce.", "scan.")), kind)


class ActionEventTests(unittest.TestCase):
    def test_the_wire_shape_carries_kind_and_timestamp(self):
        fields = ActionEvent(kind=EventKind.KILL, at=12.5).fields()
        self.assertEqual("enforce.kill", fields["kind"])
        self.assertEqual(12.5, fields["ts"])

    def test_the_correlation_fields_are_always_present(self):
        fields = ActionEvent(kind=EventKind.KILL, at=1.0).fields()
        self.assertEqual({"kind", "ts", "mono", "scan"}, set(fields))

    def test_target_fields_are_omitted_when_empty(self):
        fields = ActionEvent(kind=EventKind.KILL, at=1.0).fields()
        self.assertNotIn("pid", fields)
        self.assertNotIn("starttime", fields)

    def test_a_pid_tree_becomes_a_list(self):
        fields = ActionEvent(kind=EventKind.KILL, at=1.0, pids=(1, 2)).fields()
        self.assertEqual([1, 2], fields["pids"])
        self.assertIsInstance(fields["pids"], list)

    def test_text_fields_are_kept(self):
        fields = ActionEvent(
            kind=EventKind.KILL, at=1.0, name="firefox", rule="blacklist.name:firefox", mode="kill"
        ).fields()
        self.assertEqual("firefox", fields["name"])
        self.assertEqual("blacklist.name:firefox", fields["rule"])
        self.assertEqual("kill", fields["mode"])

    def test_counts_are_kept_when_non_zero(self):
        fields = ActionEvent(
            kind=EventKind.KILL_SUMMARY, at=1.0, count=3, duration_s=2.5
        ).fields()
        self.assertEqual(3, fields["count"])
        self.assertEqual(2.5, fields["duration_s"])

    def test_a_zero_count_is_omitted(self):
        fields = ActionEvent(kind=EventKind.KILL, at=1.0, count=0).fields()
        self.assertNotIn("count", fields)


if __name__ == "__main__":
    unittest.main()
