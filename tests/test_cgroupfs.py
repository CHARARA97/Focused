"""Tests for the freeze-target decision logic.

Everything here is pure: no process is ever stopped, and no /proc or /sys file is
read.  The whole point of ``cgroupfs`` is that the dangerous decisions can be
argued about in a unit test.
"""

import unittest

from focused.core import cgroupfs
from focused.core.cgroupfs import (
    PLAN_PID,
    PLAN_SKIP,
    PLAN_UNIT,
    events_file,
    freeze_file,
    is_dedicated_app_unit,
    is_frozen,
    parse_cgroup_events,
    parse_proc_cgroup,
    plan_for,
    process_tree,
    unit_of,
)

SELF = "/user.slice/user-1000.slice/user@1000.service/app.slice/chillfocusd.service"
FIREFOX = "/user.slice/user-1000.slice/user@1000.service/app.slice/app-firefox-4211.scope"
NIRI = "/user.slice/user-1000.slice/user@1000.service/app.slice/niri-autostart.service"


class ParseProcCgroupTests(unittest.TestCase):
    def test_unified_line_becomes_the_path(self):
        self.assertEqual(
            FIREFOX, parse_proc_cgroup("0::%s\n" % FIREFOX)
        )

    def test_the_root_cgroup_is_a_valid_answer(self):
        self.assertEqual("/", parse_proc_cgroup("0::/\n"))

    def test_v1_style_multiple_lines_use_the_first_usable_one(self):
        text = "12:cpuset:/\n0::%s\n" % FIREFOX
        self.assertEqual(FIREFOX, parse_proc_cgroup(text))

    def test_a_missing_or_broken_file_is_none(self):
        for text in ("", "\n", "not a cgroup file", "0::"):
            self.assertIsNone(parse_proc_cgroup(text), text)

    def test_a_path_without_a_leading_slash_is_normalised(self):
        self.assertEqual("/a.service", parse_proc_cgroup("0::a.service"))


class UnitOfTests(unittest.TestCase):
    def test_the_last_component_is_the_unit(self):
        self.assertEqual("app-firefox-4211.scope", unit_of(FIREFOX))

    def test_slices_are_units_too(self):
        self.assertEqual("app.slice", unit_of("/user.slice/app.slice"))

    def test_the_root_has_no_unit(self):
        for path in (None, "", "/"):
            self.assertIsNone(unit_of(path), path)


class DedicatedAppUnitTests(unittest.TestCase):
    def test_an_app_scope_is_dedicated(self):
        self.assertTrue(is_dedicated_app_unit(FIREFOX))

    def test_the_compositors_own_service_is_not_dedicated(self):
        # The browser a compositor spawns lands here; freezing it would take the
        # desktop with it.
        self.assertFalse(is_dedicated_app_unit(NIRI))

    def test_a_plain_scope_is_not_dedicated(self):
        self.assertFalse(
            is_dedicated_app_unit("/user.slice/app.slice/dsh-subprocess-7.scope")
        )

    def test_an_app_unit_outside_app_slice_is_not_dedicated(self):
        # A system service that happens to start with "app-" is not a session app
        # scope, and freezing it needs privileges we do not have.
        self.assertFalse(is_dedicated_app_unit("/system.slice/app-firefox-1.scope"))

    def test_a_slice_that_merely_starts_with_app_is_not_dedicated(self):
        self.assertFalse(is_dedicated_app_unit("/user.slice/app.slice/app-1.slice"))


class PlanTests(unittest.TestCase):
    def test_a_dedicated_app_scope_may_be_frozen_whole(self):
        plan = plan_for(FIREFOX, SELF)
        self.assertEqual(PLAN_UNIT, plan.plan)
        self.assertEqual("dedicated-app-unit", plan.reason)
        self.assertTrue(plan.freezable)

    def test_a_shared_unit_degrades_to_the_process_tree(self):
        plan = plan_for(NIRI, SELF)
        self.assertEqual(PLAN_PID, plan.plan)
        self.assertEqual("shared-unit", plan.reason)

    def test_our_own_dedicated_unit_degrades_to_the_process_tree(self):
        # Freezing this unit whole would freeze the daemon with it, but
        # suspending the matched process tree is still safe -- and it is what
        # makes freeze mode work when both were launched from the same place.
        own_unit = "/user.slice/user-1000.slice/user@1000.service/app.slice/app-chillfocus-5.scope"
        plan = plan_for(own_unit, own_unit)
        self.assertEqual(PLAN_PID, plan.plan)
        self.assertEqual("self-cgroup", plan.reason)

    def test_our_own_shared_service_is_just_a_shared_unit(self):
        plan = plan_for(SELF, SELF)
        self.assertEqual(PLAN_PID, plan.plan)
        self.assertEqual("shared-unit", plan.reason)

    def test_an_ancestor_that_is_a_slice_is_refused(self):
        ancestor = "/user.slice/user-1000.slice/user@1000.service/app.slice"
        plan = plan_for(ancestor, SELF)
        self.assertEqual(PLAN_SKIP, plan.plan)
        self.assertEqual("slice", plan.reason)

    def test_a_sibling_of_our_cgroup_is_allowed(self):
        self.assertTrue(plan_for(FIREFOX, SELF).freezable)

    def test_slices_are_refused(self):
        plan = plan_for("/user.slice/user-1000.slice/user@1000.service/app.slice", None)
        self.assertEqual(PLAN_SKIP, plan.plan)
        self.assertIn(plan.reason, ("slice", "self-ancestor"))

    def test_the_user_manager_is_refused(self):
        plan = plan_for("/user.slice/user-1000.slice/user@1000.service", None)
        self.assertEqual(PLAN_SKIP, plan.plan)
        self.assertEqual("session-manager", plan.reason)

    def test_init_scope_is_refused(self):
        plan = plan_for("/init.scope", None)
        self.assertEqual(PLAN_SKIP, plan.plan)
        self.assertEqual("session-manager", plan.reason)

    def test_the_root_cgroup_is_refused(self):
        self.assertEqual(PLAN_SKIP, plan_for("/", SELF).plan)

    def test_an_unreadable_cgroup_is_refused(self):
        for path in (None, ""):
            self.assertEqual(PLAN_SKIP, plan_for(path, SELF).plan, path)

    def test_an_unknown_self_cgroup_never_yields_a_unit_freeze(self):
        # Without our own cgroup we cannot prove a unit does not contain us, so
        # the safe answer is the narrower one.
        plan = plan_for(FIREFOX, None)
        self.assertEqual(PLAN_PID, plan.plan)
        self.assertEqual("unknown-self-cgroup", plan.reason)

    def test_a_cgroup_v1_system_is_refused_rather_than_guessed(self):
        plan = plan_for(FIREFOX, SELF, unified=False)
        self.assertEqual(PLAN_SKIP, plan.plan)
        self.assertEqual("not-cgroup-v2", plan.reason)

    def test_an_explicit_dedicated_flag_is_honoured(self):
        self.assertEqual(PLAN_UNIT, plan_for(NIRI, SELF, dedicated=True).plan)
        self.assertEqual(PLAN_PID, plan_for(FIREFOX, SELF, dedicated=False).plan)

    def test_every_plan_carries_a_reason(self):
        for path in (FIREFOX, NIRI, None, "/", "/init.scope"):
            self.assertTrue(plan_for(path, SELF).reason, path)


class ControlFileTests(unittest.TestCase):
    def test_the_freeze_file_sits_inside_the_cgroup(self):
        self.assertEqual(
            "/sys/fs/cgroup" + FIREFOX + "/cgroup.freeze", freeze_file(FIREFOX)
        )

    def test_the_events_file_sits_inside_the_cgroup(self):
        self.assertEqual(
            "/sys/fs/cgroup" + FIREFOX + "/cgroup.events", events_file(FIREFOX)
        )

    def test_a_trailing_slash_does_not_double_up(self):
        self.assertEqual(freeze_file(FIREFOX), freeze_file(FIREFOX + "/"))

    def test_frozen_is_read_from_cgroup_events(self):
        text = "populated 1\nfrozen 1\n"
        self.assertEqual({"populated": "1", "frozen": "1"}, parse_cgroup_events(text))
        self.assertTrue(is_frozen(text))

    def test_a_thawed_cgroup_is_not_frozen(self):
        self.assertFalse(is_frozen("populated 1\nfrozen 0\n"))

    def test_broken_or_missing_events_read_as_not_frozen(self):
        for text in ("", "frozen", "frozen\n", "junk"):
            self.assertFalse(is_frozen(text), text)


class ProcessTreeTests(unittest.TestCase):
    def test_the_root_alone_is_a_tree_of_one(self):
        self.assertEqual([7], process_tree(7, {7: 1}))

    def test_children_and_grandchildren_are_included(self):
        parents = {10: 1, 11: 10, 12: 10, 13: 11, 14: 1}
        self.assertEqual([10, 11, 12, 13], process_tree(10, parents))

    def test_a_cycle_cannot_hang_the_walk(self):
        parents = {1: 2, 2: 1}
        self.assertEqual([1, 2], process_tree(1, parents))

    def test_unknown_parents_are_ignored(self):
        self.assertEqual([5], process_tree(5, {5: None, 6: 999}))

    def test_siblings_are_in_pid_order(self):
        parents = {1: 0, 5: 1, 3: 1, 4: 1}
        self.assertEqual([1, 3, 4, 5], process_tree(1, parents))


if __name__ == "__main__":
    unittest.main()
