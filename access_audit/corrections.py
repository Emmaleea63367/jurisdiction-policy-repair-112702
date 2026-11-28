"""更正候选评估与中途漂移处理。

更正候选的三种形态都在规则层体现：撤回历史角色（如 disable-shared）、
缩小组范围（如把跨区复核限定到并办方）、增加明确排除（如 no-self）。
评估只回答两类问题：

1. 每条候选/在用规则在真实访问边界下是安全、需收紧、待补还是应停用；
2. 一组需要并用才守得住职责分离的规则，是否被整体启用——只启用一部分
   时，原本应禁止的复核窗口仍然开着，更正不能算完成。

应用端只把实际启用的规则作为生效依据，queued 不产生效果。
"""
from __future__ import annotations

from dataclasses import dataclass

from .engine import (
    MatrixRow,
    boundary_decision,
    classify_row,
    coverage_report,
    current_dockets,
    evaluate_matrix,
    literal_decision,
    known_teams,
    person_state,
    policy_ledger,
)
from .model import Assignment, ChangeRecord, DocketSnapshot, Rule


@dataclass(frozen=True)
class RuleDisposition:
    rule: Rule
    is_active: bool
    category: str
    """安全 / 需收紧 / 待补 / 应停用。"""
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class GroupAssessment:
    group: str
    changes: tuple[ChangeRecord, ...]
    intended_rules: tuple[Rule, ...]
    active_rules: tuple[Rule, ...]
    missing_rules: tuple[Rule, ...]
    complete: bool
    verdict: str
    detail: tuple[str, ...]


@dataclass(frozen=True)
class DriftEvent:
    seq: int
    kind: str
    title: str
    detail: str


@dataclass(frozen=True)
class CorrectionAnalysis:
    ledger_revision: int
    dispositions: tuple[RuleDisposition, ...]
    groups: tuple[GroupAssessment, ...]
    matrix: tuple[MatrixRow, ...]
    risk_rows: tuple[MatrixRow, ...]
    pending_rows: tuple[MatrixRow, ...]
    gap_rows: tuple[MatrixRow, ...]
    compliant_allows: tuple[MatrixRow, ...]
    coverage: tuple
    drift: tuple[DriftEvent, ...]


# --------------------------------------------------------------------------- 规则级安全判断


def _rule_evidence(
    rule: Rule,
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    active_rules: tuple[Rule, ...],
    minute: int,
) -> tuple[str, str]:
    """在真实人员/卷宗上回放一条规则，返回（类别, 证据）。"""
    if rule.grant_scope is None:
        return "待补", f"{rule.rule_id} 的 grant_scope 未交全，不能推断风险已消除"
    if len(rule.grant_scope) == 0:
        return "待补", (f"{rule.rule_id} 显式空范围表示停用意图，但缺撤回对象的完整配置，"
                        "需补全后才能确认历史共享权限确已收回")

    teams = known_teams(assignments, dockets)
    violations: list[str] = []
    pending_notes: list[str] = []
    compliant = 0
    for latest in current_dockets(dockets):
        for person in sorted({a.person for a in assignments}):
            state = person_state(person, minute, assignments)
            literal = literal_decision(state, latest, rule.action, (rule,), teams)
            if literal.verdict != "allow":
                if literal.verdict == "pending":
                    pending_notes.append(f"{person}/{latest.bundle}/{rule.action} 判定待补")
                continue
            boundary = boundary_decision(state, latest, rule.action, active_rules)
            if boundary.verdict == "deny":
                violations.append(f"{person} 对 {latest.bundle} 的 {rule.action} 被规则准许，"
                                  f"但边界拒绝：{'；'.join(boundary.reasons)}")
            elif boundary.verdict == "pending":
                pending_notes.append(f"{person}/{latest.bundle}/{rule.action} 边界待补")
            else:
                compliant += 1
    if violations and compliant == 0:
        return "应停用", "规则准许的全部事项均越过边界：" + "；".join(violations[:4])
    if violations:
        return "需收紧", (f"规则有 {compliant} 项合法用途，但同时越界："
                          + "；".join(violations[:4]))
    if pending_notes:
        return "待补", "；".join(sorted(set(pending_notes))[:4])
    return "安全", f"规则准许的 {compliant} 项事项在边界层全部成立"


def assess_rule(
    rule: Rule,
    active_ids: set[str],
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    active_rules: tuple[Rule, ...],
    minute: int,
) -> RuleDisposition:
    category, evidence = _rule_evidence(rule, assignments, dockets, active_rules, minute)
    # 生效中的越界规则在证据上标明其为现行风险，便于管理员直接定位。
    if category in {"应停用", "需收紧"} and rule.rule_id in active_ids:
        evidence = f"【现行生效风险】{evidence}"
    return RuleDisposition(
        rule=rule,
        is_active=rule.rule_id in active_ids,
        category=category,
        evidence=(evidence,),
    )


# --------------------------------------------------------------------------- 变更组（含职责分离并用）评估


def assess_groups(rules: list[Rule], changes: list[ChangeRecord],
                  ledger) -> tuple[GroupAssessment, ...]:
    grouped: dict[str, list[Rule]] = {}
    for rule in rules:
        if rule.group:
            grouped.setdefault(rule.group, []).append(rule)
    assessments: list[GroupAssessment] = []
    active_ids = {r.rule_id for r in ledger.active_rules}
    for group in sorted(grouped):
        intended = tuple(grouped[group])
        intended_ids = {r.rule_id for r in intended}
        group_changes = tuple(c for c in changes
                              if c.state == "active" and set(c.active) & intended_ids)
        active = tuple(r for r in intended if r.rule_id in active_ids)
        missing = tuple(r for r in intended if r.rule_id not in active_ids)
        complete = not missing
        if complete:
            verdict = "整体启用"
            detail = (f"组 {group} 的 {len(intended)} 条规则全部实际生效，"
                      "并用关系保持完整",)
        elif not active:
            verdict = "整组未启用"
            detail = (
                f"组 {group} 全部规则 {sorted(intended_ids)} 均未实际生效",
                "若该组承担撤回或职责分离职责，则其负责封死的窗口仍由旧规则开放；"
                "效果核对以实际允许的操作与批准边界比较，提交成功不代表更正完成",
            )
        else:
            open_windows = [
                r.rule_id for r in active
                if r.action == "approve" and r.grant_scope
                and "no-self" not in r.grant_scope
            ]
            verdict = "部分启用-禁止窗口仍开"
            detail = (
                f"组 {group} 仅 {[r.rule_id for r in active]} 生效，"
                f"未启用 {[r.rule_id for r in missing]}",
                "职责分离需要整组并用：" + ("复核窗口仍开放：" + "、".join(open_windows)
                                         if open_windows else "撤回/排除规则缺失，越界授权未封死"),
            )
        assessments.append(GroupAssessment(
            group=group,
            changes=group_changes,
            intended_rules=intended,
            active_rules=active,
            missing_rules=missing,
            complete=complete,
            verdict=verdict,
            detail=detail,
        ))
    return tuple(assessments)


# --------------------------------------------------------------------------- 中途漂移


def drift_events(
    raw_assignments: list,
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    changes: list[ChangeRecord],
) -> tuple[DriftEvent, ...]:
    """列出当班途中会改变授权面的到达事件。

    raw_assignments 是含被更正旧记录的完整到达序列；assignments 是去重
    后的当前任职快照。两者对照才能还原"任职更正"事件。
    """
    events: list[DriftEvent] = []
    current = {a.assignment_id: a for a in assignments}
    seen: dict[str, object] = {}
    for assignment in raw_assignments:
        if assignment.assignment_id in seen:
            previous = seen[assignment.assignment_id]
            events.append(DriftEvent(
                assignment.seq, "任职更正",
                f"{assignment.person} 的任职 {assignment.assignment_id} 被更正",
                f"区间由 [{previous.from_minute},{previous.until_minute}) "
                f"改为 [{assignment.from_minute},{assignment.until_minute})，"
                f"角色由 {previous.roles} 改为 {assignment.roles}",
            ))
        elif assignment.assignment_id not in current:
            # 截至演练点已被后续记录完全取代的旧任职，仍作为调入历史展示。
            events.append(DriftEvent(
                assignment.seq, "人员调入（已被更正）",
                f"{assignment.person} 经 {assignment.assignment_id} 加入 {assignment.team}",
                f"角色 {assignment.roles}，区间 "
                f"[{assignment.from_minute},{assignment.until_minute})",
            ))
        else:
            events.append(DriftEvent(
                assignment.seq, "人员调入",
                f"{assignment.person} 经 {assignment.assignment_id} 加入 {assignment.team}",
                f"角色 {assignment.roles}，区间 "
                f"[{assignment.from_minute},{assignment.until_minute})",
            ))
        seen[assignment.assignment_id] = assignment
    seen_dockets: dict[str, DocketSnapshot] = {}
    for docket in dockets:
        if docket.bundle in seen_dockets:
            previous = seen_dockets[docket.bundle]
            events.append(DriftEvent(
                docket.seq, "卷宗调辖",
                f"{docket.bundle} 调整管辖",
                f"主管方 {previous.lead}→{docket.lead}，"
                f"并办方 {previous.joint}→{docket.joint}，修订 {previous.policy_rev}→{docket.policy_rev}",
            ))
        seen_dockets[docket.bundle] = docket
    for change in changes:
        events.append(DriftEvent(
            change.seq,
            "应用修订生效" if change.state == "active" else "应用修订排队",
            f"变更 {change.change_id}（{change.before}→{change.after}）{change.state}",
            f"实际启用规则 {list(change.active) or '无'}",
        ))
    return tuple(sorted(events, key=lambda event: event.seq))


# --------------------------------------------------------------------------- 汇总分析


def analyze(
    raw_assignments: list[Assignment],
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    rules: list[Rule],
    changes: list[ChangeRecord],
    minute: int,
    through: int | None = None,
) -> CorrectionAnalysis:
    ledger = policy_ledger(rules, changes, through)
    active_ids = {r.rule_id for r in ledger.active_rules}
    dispositions = tuple(
        assess_rule(rule, active_ids, assignments, dockets, ledger.active_rules, minute)
        for rule in rules
    )
    groups = assess_groups(rules, changes, ledger)
    matrix = tuple(evaluate_matrix(assignments, dockets, ledger, minute))

    risk = tuple(row for row in matrix if classify_row(row) == "待处置")
    pending = tuple(row for row in matrix if classify_row(row) == "待补")
    gaps = tuple(row for row in matrix if classify_row(row) == "仍需交接")
    allows = tuple(row for row in matrix if classify_row(row) == "准予")
    coverage = tuple(coverage_report(assignments, dockets, ledger, minute))
    drift = drift_events(raw_assignments, assignments, dockets, changes)
    return CorrectionAnalysis(
        ledger_revision=ledger.effective_revision,
        dispositions=dispositions,
        groups=groups,
        matrix=matrix,
        risk_rows=risk,
        pending_rows=pending,
        gap_rows=gaps,
        compliant_allows=allows,
        coverage=coverage,
        drift=drift,
    )
