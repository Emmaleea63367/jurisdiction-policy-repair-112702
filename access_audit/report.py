"""人类可读与机器可读的输出渲染。"""
from __future__ import annotations

from .corrections import CorrectionAnalysis
from .engine import (
    MatrixRow,
    boundary_decision,
    classify_row,
    latest_docket,
    person_state,
    policy_ledger,
)
from .model import Assignment, DocketSnapshot
from .replay import ReplayReport

ACTION_LABEL = {"read": "普通查询", "export": "案件外发", "approve": "外发复核"}
VERDICT_LABEL = {"allow": "准许", "deny": "拒绝", "pending": "待补"}


def _line(title: str) -> str:
    return f"── {title} " + "─" * max(0, 60 - len(title))


def render_replay(report: ReplayReport) -> str:
    out = [_line("应用回放（按各观察自带修订的规则复刻）")]
    for line in report.lines:
        obs = line.observation
        mark = "一致" if line.matches else "不一致"
        out.append(
            f"seq{obs.seq} 修订{obs.revision} {obs.person} {obs.bundle} "
            f"{ACTION_LABEL[obs.action]} 观察={'允许' if obs.allowed else '拒绝'} "
            f"复刻={VERDICT_LABEL[line.replayed_verdict]} [{mark}]"
        )
        out.append(f"    应用解释码 {list(obs.codes)}；{line.detail}")
        if line.unknown_codes:
            out.append(f"    ⚠ 解释码在该修订规则中不存在：{list(line.unknown_codes)}")
    for note in report.ordering_notes:
        out.append(f"⚠ 时序提示：{note}")
    out.append("结论：回放" + ("全部一致，字面层可作为应用判定的本地复刻"
                            if report.all_match else "存在不一致，需先核对来源"))
    return "\n".join(out)


def render_matrix(rows: list[MatrixRow]) -> str:
    out = [_line("访问矩阵（应用字面判定 / 业务边界判定）")]
    current = None
    for row in rows:
        key = (row.person, row.bundle)
        if key != current:
            current = key
            roles = ",".join(sorted(row.roles)) if row.roles else ("角色未交全" if row.active else "无在任任职")
            teams = ",".join(sorted(row.teams)) if row.teams else "—"
            out.append(f"\n{row.person}（机构 {teams}；岗位 {roles}）× {row.bundle}")
        out.append(
            f"  {ACTION_LABEL[row.action]:<4} 应用={VERDICT_LABEL[row.literal.verdict]:<2} "
            f"边界={VERDICT_LABEL[row.boundary.verdict]:<2} 处置类别：{classify_row(row)}"
        )
        for reason in row.literal.reasons:
            out.append(f"      应用依据：{reason}")
        for reason in row.boundary.reasons:
            out.append(f"      边界条件：{reason}")
    return "\n".join(out)


def render_explain(
    person: str,
    bundle: str,
    action: str,
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    rules,
    changes,
    minute: int,
) -> str:
    ledger = policy_ledger(rules, changes)
    teams = {a.team for a in assignments}
    for docket in dockets:
        teams.add(docket.lead)
        if docket.joint:
            teams.update(docket.joint)
    state = person_state(person, minute, assignments)
    docket = latest_docket(dockets, bundle)
    from .engine import literal_decision
    literal = literal_decision(state, docket, action, ledger.active_rules, frozenset(teams))
    boundary = boundary_decision(state, docket, action, ledger.active_rules)

    out = [_line(f"单项解释：{person} 对 {bundle} 的{ACTION_LABEL[action]}（第 {minute} 分钟）")]
    out.append("有效身份关系：")
    if not state.active_assignments:
        out.append("  该时刻无在任任职")
    for assignment in state.active_assignments:
        roles = "、".join(assignment.roles) if assignment.roles is not None else "角色未交全（待补）"
        out.append(f"  {assignment.assignment_id}：机构 {assignment.team}，岗位 {roles}，"
                   f"区间 [{assignment.from_minute},{assignment.until_minute})")
    parties = docket.parties()
    out.append(f"卷宗事实：主管方 {docket.lead}，并办方 "
               f"{sorted(parties) if parties is not None else '未交全'}，"
               f"阶段 {docket.stage}，外发提交人 {docket.requester}，策略修订 {docket.policy_rev}")
    out.append(f"当前生效修订：{ledger.effective_revision}，生效规则 "
               f"{[r.rule_id for r in ledger.active_rules]}")
    out.append(f"应用字面判定：{VERDICT_LABEL[literal.verdict]}")
    for basis in literal.bases:
        out.append(f"  · [{basis.source}:{basis.code}] {basis.detail}")
    for reason in literal.reasons:
        out.append(f"    原因：{reason}")
    out.append(f"业务边界判定：{VERDICT_LABEL[boundary.verdict]}")
    for basis in boundary.bases:
        out.append(f"  · [{basis.source}:{basis.code}] {basis.detail}")
    for reason in boundary.reasons:
        out.append(f"    条件：{reason}")
    category = classify_row(MatrixRow(
        person, bundle, action, state.teams, state.roles,
        bool(state.active_assignments), literal, boundary,
    ))
    out.append(f"处置类别：{category}")
    if category == "仍需交接":
        steps = ("业务上具备办理资格，但当前生效策略未授权；"
                 "按经批准的影响范围补登明确授权或完成岗位交接后再办理",)
    elif category == "待处置":
        steps = ("立即按边界停用该窗口，并将更正整组生效后再复核",
                 *boundary.next_steps)
    elif category == "待补":
        steps = boundary.next_steps or literal.next_steps or (
            "补全目录/资源方缺项后重跑本解释，缺项期间按未授权处理",)
    else:
        steps = boundary.next_steps or literal.next_steps or ("无额外动作",)
    out.append("可采取的下一步：")
    for step in steps:
        out.append(f"  → {step}")
    out.append("说明：本解释可由应用管理人持有效依据与原办理单位核对；待补项不得视为风险消除。")
    return "\n".join(out)


def render_corrections(analysis: CorrectionAnalysis) -> str:
    out = [_line(f"更正分析（生效修订 {analysis.ledger_revision}）")]
    out.append("\n[规则级处置]")
    for item in analysis.dispositions:
        rule = item.rule
        status = "生效中" if item.is_active else "未生效"
        scope = "未交全" if rule.grant_scope is None else list(rule.grant_scope)
        out.append(f"  {rule.rule_id}（组 {rule.group or '—'}，{rule.role}/{rule.action}，"
                   f"范围 {scope}，{status}）→ {item.category}")
        for evidence in item.evidence:
            out.append(f"      {evidence}")

    out.append("\n[职责分离并用核对]")
    for group in analysis.groups:
        out.append(f"  组 {group.group}：{group.verdict}")
        for detail in group.detail:
            out.append(f"      {detail}")

    buckets = [
        ("待处置（应用仍准许但边界禁止的实际风险）", analysis.risk_rows),
        ("准予更正（两层均准许的合法事项）", analysis.compliant_allows),
        ("仍需交接（边界允许但应用已不再支持）", analysis.gap_rows),
        ("待补（目录或资源方缺项，不得推断无风险）", analysis.pending_rows),
    ]
    for title, rows in buckets:
        out.append(f"\n[{title}] 共 {len(rows)} 项")
        for row in rows:
            out.append(f"  {row.person} × {row.bundle} × {ACTION_LABEL[row.action]}")
            condition = row.boundary.reasons or row.literal.reasons
            for text in condition[:2]:
                out.append(f"      决定条件：{text}")

    out.append("\n[卷宗接手覆盖]")
    if not analysis.coverage:
        out.append("  各卷宗外发发起、逐机构复核与主管方查询均有合格人员")
    for gap in analysis.coverage:
        party = f"机构 {gap.party}" if gap.party else "全卷宗"
        out.append(f"  {gap.bundle} {ACTION_LABEL.get(gap.action, gap.action)} {party}：{gap.detail}")
        out.append(f"      → {gap.next_step}")

    out.append("\n[中途漂移事件]")
    for event in analysis.drift:
        out.append(f"  seq{event.seq} {event.kind}：{event.title}")
        out.append(f"      {event.detail}")
    return "\n".join(out)


def render_timeline(analysis: CorrectionAnalysis) -> str:
    out = [_line("到达时序（授权面漂移事件）")]
    for event in analysis.drift:
        out.append(f"seq{event.seq:<3} {event.kind:<8} {event.title}")
        out.append(f"        {event.detail}")
    return "\n".join(out)
