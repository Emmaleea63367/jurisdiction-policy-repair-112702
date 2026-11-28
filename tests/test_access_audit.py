"""引擎与业务边界规则测试。"""
import unittest
from pathlib import Path

from access_audit.corrections import analyze, drift_events
from access_audit.engine import (
    ACTIONS,
    boundary_decision,
    classify_row,
    current_dockets,
    coverage_report,
    evaluate_matrix,
    latest_docket,
    literal_decision,
    known_teams,
    person_state,
    policy_ledger,
)
from access_audit.load import load_dataset
from access_audit.replay import replay_observations

DATA = Path(__file__).resolve().parent.parent / "sources"


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.data = load_dataset(DATA)
        self.teams = known_teams(self.data["assignments"], self.data["dockets"])

    def test_assignment_correction_replaces_old_interval(self):
        # seq18 把 du-3 更正为 [0,17)：第 10 分钟在任，第 30 分钟已离任。
        early = person_state("p2", 10, self.data["assignments"])
        late = person_state("p2", 30, self.data["assignments"])
        self.assertTrue(early.active_assignments)
        self.assertEqual(late.active_assignments, ())
        self.assertEqual(late.teams, frozenset())

    def test_concurrent_assignments_stack_teams_and_roles(self):
        state = person_state("p1", 30, self.data["assignments"])
        self.assertEqual(state.teams, frozenset({"east", "board"}))
        self.assertEqual(state.roles, frozenset({"initiate", "review"}))

    def test_unknown_roles_never_become_deny_or_allow(self):
        state = person_state("p4", 30, self.data["assignments"])
        self.assertTrue(state.role_unknown)
        self.assertIsNone(state.has_role("review"))
        docket = latest_docket(self.data["dockets"], "bun-7")
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        decision = boundary_decision(state, docket, "approve", ledger.active_rules)
        self.assertEqual(decision.verdict, "pending")

    def test_reviewer_cannot_approve_own_submission(self):
        # p1 兼有 initiate 与 review，但是 bun-7 的外发提交人。
        state = person_state("p1", 30, self.data["assignments"])
        docket = latest_docket(self.data["dockets"], "bun-7")
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        boundary = boundary_decision(state, docket, "approve", ledger.active_rules)
        self.assertEqual(boundary.verdict, "deny")
        self.assertIn("自己提交的外发", boundary.reasons[0])
        # 应用字面层仍准许——这正是待处置风险。
        literal = literal_decision(state, docket, "approve", ledger.active_rules, self.teams)
        self.assertEqual(literal.verdict, "allow")

    def test_joint_docket_requires_each_party_own_confirmation(self):
        # board 的 review 不能代替 east/west 任一方确认 bun-7。
        state = person_state("p1", 30, self.data["assignments"])
        docket = latest_docket(self.data["dockets"], "bun-7")
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        decision = boundary_decision(state, docket, "approve", ledger.active_rules)
        # 拒绝原因优先命中本人回避；改用 p5（east review，非提交人）验证机构规则。
        p5 = person_state("p5", 30, self.data["assignments"])
        ok = boundary_decision(p5, docket, "approve", ledger.active_rules)
        self.assertEqual(ok.verdict, "allow")
        # bun-8 调辖到 west 后，east 的 review 不能再确认。
        moved = latest_docket(self.data["dockets"], "bun-8")
        barred = boundary_decision(p5, moved, "approve", ledger.active_rules)
        self.assertEqual(barred.verdict, "deny")
        self.assertIn("不能自行代替另一机构", barred.reasons[0])
        self.assertEqual(decision.verdict, "deny")

    def test_read_and_export_are_distinct_permissions(self):
        state = person_state("p3", 30, self.data["assignments"])
        docket = latest_docket(self.data["dockets"], "bun-8")
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        read = boundary_decision(state, docket, "read", ledger.active_rules)
        export = boundary_decision(state, docket, "export", ledger.active_rules)
        self.assertEqual(read.verdict, "allow")
        self.assertEqual(export.verdict, "deny")  # 无 initiate 资格

    def test_lead_party_query_retained_on_joint_docket(self):
        # bun-7 主管方 east：p5 只有 review，p2 已离任，query 保留缺口要被识别。
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        gaps = coverage_report(self.data["assignments"], self.data["dockets"], ledger, 30)
        lead_gap = next(g for g in gaps if g.bundle == "bun-7" and g.action == "read")
        self.assertEqual(lead_gap.party, "east")

    def test_moved_docket_leaves_no_initiator_in_west(self):
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        gaps = coverage_report(self.data["assignments"], self.data["dockets"], ledger, 30)
        export_gap = next(g for g in gaps if g.bundle == "bun-8" and g.action == "export")
        self.assertIn("没有合格的 initiate", export_gap.detail)

    def test_missing_joint_scope_is_pending_not_safe(self):
        docket = latest_docket(self.data["dockets"], "bun-9")
        self.assertIsNone(docket.parties())
        state = person_state("p1", 30, self.data["assignments"])
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        for action in ACTIONS:
            self.assertEqual(
                boundary_decision(state, docket, action, ledger.active_rules).verdict,
                "pending",
            )

    def test_matrix_buckets(self):
        ledger = policy_ledger(self.data["rules"], self.data["changes"])
        rows = evaluate_matrix(self.data["assignments"], self.data["dockets"], ledger, 30)
        categories = {classify_row(row) for row in rows}
        self.assertIn("待处置", categories)
        self.assertIn("待补", categories)
        self.assertIn("仍需交接", categories)
        self.assertIn("准予", categories)
        # 调辖后卷宗不重复。
        self.assertEqual(len({row.bundle for row in rows}), len(current_dockets(self.data["dockets"])))


class LedgerAndReplayTest(unittest.TestCase):
    def setUp(self):
        self.data = load_dataset(DATA)

    def test_queued_change_has_no_effect(self):
        ledger = policy_ledger(self.data["rules"], self.data["changes"], through=22)
        self.assertEqual(ledger.effective_revision, 14)
        self.assertIn("shared-export", {r.rule_id for r in ledger.active_rules})

    def test_only_active_rules_take_effect(self):
        # seq24 的 active 只带 cross-unit，repair-1 其余规则不生效。
        ledger = policy_ledger(self.data["rules"], self.data["changes"], through=24)
        active = {r.rule_id for r in ledger.active_rules}
        self.assertEqual(active, {"cross-unit"})
        full = policy_ledger(self.data["rules"], self.data["changes"])
        self.assertEqual(full.effective_revision, 16)
        self.assertEqual(
            {r.rule_id for r in full.active_rules},
            {"jur-query", "review-access", "maint-admin"},
        )

    def test_replay_matches_every_observation(self):
        report = replay_observations(
            self.data["observations"], self.data["rules"], self.data["assignments"],
            self.data["dockets"], self.data["changes"],
        )
        self.assertTrue(report.all_match)
        self.assertEqual(len(report.lines), 5)
        # 应用观察先于管理员 active 记录到达，应给出时差提示而非误判失败。
        self.assertTrue(any("到达时差" in note for note in report.ordering_notes))


class CorrectionAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.data = load_dataset(DATA)

    def test_partial_sod_group_keeps_forbidden_review_window_open(self):
        analysis = analyze(
            self.data["raw_assignments"], self.data["assignments"], self.data["dockets"],
            self.data["rules"], self.data["changes"], minute=20, through=24,
        )
        repair = next(group for group in analysis.groups if group.group == "repair-1")
        self.assertFalse(repair.complete)
        self.assertIn("部分启用", repair.verdict)
        self.assertEqual({r.rule_id for r in repair.active_rules}, {"cross-unit"})
        # p1 自我复核窗口仍然开着。
        risk = {(r.person, r.bundle, r.action) for r in analysis.risk_rows}
        self.assertIn(("p1", "bun-7", "approve"), risk)

    def test_live_risks_and_pending_kept_separate(self):
        analysis = analyze(
            self.data["raw_assignments"], self.data["assignments"], self.data["dockets"],
            self.data["rules"], self.data["changes"], 30,
        )
        risk = {(r.person, r.bundle, r.action) for r in analysis.risk_rows}
        # maint-admin 把 east 人员带进了只读授权（外部管理员修订扩大授权）。
        self.assertIn(("p1", "bun-7", "read"), risk)
        # bun-9 管辖缺项只能进待补，不能算风险消除。
        pending_bundles = {r.bundle for r in analysis.pending_rows}
        self.assertIn("bun-9", pending_bundles)
        # p4 角色 null 的角色类操作全部待补。
        p4_pending = {r.action for r in analysis.pending_rows if r.person == "p4"}
        self.assertIn("approve", p4_pending)
        self.assertIn("export", p4_pending)

    def test_drift_lists_transfer_reassignment_and_external_revision(self):
        analysis = analyze(
            self.data["raw_assignments"], self.data["assignments"], self.data["dockets"],
            self.data["rules"], self.data["changes"], 30,
        )
        kinds = [event.kind for event in analysis.drift]
        self.assertIn("任职更正", kinds)
        self.assertIn("卷宗调辖", kinds)
        self.assertIn("应用修订生效", kinds)
        titles = " ".join(event.title for event in analysis.drift)
        self.assertIn("bun-8", titles)

    def test_unaffected_correction_remains_compliant_after_drift(self):
        # p3 在 west 的 query/review 不受 east 人员变动影响，始终在准予桶。
        analysis = analyze(
            self.data["raw_assignments"], self.data["assignments"], self.data["dockets"],
            self.data["rules"], self.data["changes"], 30,
        )
        allowed = {(r.person, r.bundle, r.action) for r in analysis.compliant_allows}
        self.assertIn(("p3", "bun-8", "read"), allowed)
        self.assertIn(("p3", "bun-8", "approve"), allowed)


if __name__ == "__main__":
    unittest.main()
