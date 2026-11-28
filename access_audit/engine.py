"""访问判定引擎。

分两层判定，刻意不混在一起：

* 字面层（literal）——按应用里实际启用的策略规则复刻应用会给出的结果，
  并由回放模块对照 observations 校准；
* 边界层（boundary）——按题目给出的业务访问边界独立判断，不依赖应用配置，
  作为修订能否通过的准绳。

两层结果不一致时，由更正分析模块归类为风险、待补或交接问题。
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Assignment, ChangeRecord, DocketSnapshot, PersonState, Rule

ACTIONS = ("read", "export", "approve")
# 作用域标记里有业务含义的特殊词，其余标记按机构（team）名解释。
SPECIAL_SCOPES = frozenset({"assigned", "joint", "no-self"})


# --------------------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class Basis:
    """一条判定依据。"""

    source: str
    """rule（应用规则）或 boundary（业务边界）。"""
    code: str
    detail: str
    aligned: bool | None = None
    """该依据是否与业务边界一致；边界自身的依据为 None。"""


@dataclass(frozen=True)
class Decision:
    verdict: str
    """allow / deny / pending。"""
    bases: tuple[Basis, ...]
    reasons: tuple[str, ...]
    next_steps: tuple[str, ...]

    @property
    def is_pending(self) -> bool:
        return self.verdict == "pending"


@dataclass(frozen=True)
class Ledger:
    """某时刻应用实际生效的策略台账。"""

    effective_revision: int
    active_rules: tuple[Rule, ...]
    inactive_rules: tuple[Rule, ...]
    queued_changes: tuple[ChangeRecord, ...]
    transitions: tuple[tuple[ChangeRecord, bool], ...]


# --------------------------------------------------------------------------- 快照与身份


def latest_docket(dockets: list[DocketSnapshot], bundle: str) -> DocketSnapshot | None:
    """取卷宗最新一条归属/状况快照（载入时已按 seq 排序）。"""
    snapshots = [d for d in dockets if d.bundle == bundle]
    return snapshots[-1] if snapshots else None


def current_dockets(dockets: list[DocketSnapshot]) -> list[DocketSnapshot]:
    """每个卷宗只保留最新快照，调辖历史由漂移视图单独呈现。"""
    latest: dict[str, DocketSnapshot] = {}
    for docket in dockets:
        latest[docket.bundle] = docket
    return [latest[bundle] for bundle in sorted(latest)]


def all_persons(assignments: list[Assignment]) -> tuple[str, ...]:
    return tuple(sorted({a.person for a in assignments}))


def person_state(person: str, minute: int, assignments: list[Assignment]) -> PersonState:
    """汇总某人在指定分钟的有效身份关系。

    同一人可有多个独立任职（兼岗），团队与角色取并集；任一在任任职
    未交角色即标记 role_unknown，角色类判断只能给待补。
    """
    active = tuple(a for a in assignments if a.person == person and a.covers(minute))
    teams = frozenset(a.team for a in active)
    roles: set[str] = set()
    role_unknown = False
    for assignment in active:
        if assignment.roles is None:
            role_unknown = True
        else:
            roles.update(assignment.roles)
    return PersonState(
        person=person,
        minute=minute,
        active_assignments=active,
        teams=teams,
        roles=frozenset(roles),
        role_unknown=role_unknown,
    )


# --------------------------------------------------------------------------- 生效策略台账


def policy_ledger(
    rules: list[Rule], changes: list[ChangeRecord], through: int | None = None
) -> Ledger:
    """按变更记录还原实际生效的规则集合。

    只有 state == 'active' 的变更会切换生效修订；queued 不是实际状态。
    应用端只把实际启用的规则（active 列表）作为生效依据。
    """
    considered = [c for c in changes if through is None or c.seq <= through]
    if not rules:
        raise ValueError("策略来源没有任何规则")
    base_rev = min(r.revision for r in rules)
    current_rev = base_rev
    active_ids = {r.rule_id for r in rules if r.revision == base_rev}
    transitions: list[tuple[ChangeRecord, bool]] = []
    queued: list[ChangeRecord] = []
    for change in considered:
        if change.state == "active":
            current_rev = change.after
            active_ids = set(change.active)
            transitions.append((change, True))
        else:
            queued.append(change)
            transitions.append((change, False))

    by_id: dict[str, Rule] = {}
    for rule in rules:
        by_id.setdefault(rule.rule_id, rule)
    active_rules = tuple(by_id[i] for i in sorted(active_ids) if i in by_id)
    active_set = set(active_ids)
    inactive_rules = tuple(r for r in rules if r.rule_id not in active_set)
    return Ledger(
        effective_revision=current_rev,
        active_rules=active_rules,
        inactive_rules=inactive_rules,
        queued_changes=tuple(queued),
        transitions=tuple(transitions),
    )


def rules_for_revision(rules: list[Rule], revision: int) -> tuple[Rule, ...]:
    """取截至某修订已知的累计规则。

    策略来源按修订增量记录（同一 rule_id 不在新修订里重复出现即沿用），
    因此回放某修订时应使用所有 seq 不晚于该修订规则的已知规则，而不是
    只取该修订号下的新增行。
    """
    cutoff_seqs = [r.seq for r in rules if r.revision <= revision]
    if not cutoff_seqs:
        return ()
    cutoff = max(cutoff_seqs)
    return tuple(r for r in rules if r.seq <= cutoff)


def known_teams(assignments: list[Assignment], dockets: list[DocketSnapshot]) -> frozenset[str]:
    teams = {a.team for a in assignments}
    for docket in dockets:
        teams.add(docket.lead)
        if docket.joint:
            teams.update(docket.joint)
    return frozenset(teams)


# --------------------------------------------------------------------------- 字面层：复刻应用判定


def _role_match(rule: Rule, state: PersonState) -> bool | None:
    """规则角色与人员身份的匹配；未知时返回 None。"""
    if rule.role == "staff":
        return state.is_staff()
    return state.has_role(rule.role)


def _literal_scope(
    rule: Rule, state: PersonState, docket: DocketSnapshot, teams: frozenset[str]
) -> str:
    """字面作用域判断，返回 satisfied / denied / incomplete / unrecognized。

    字面层按应用原策略的实际表现解释：

    * assigned 只校验角色归属（回放可见 board 的 review 据此获准了
      east/west 卷宗），joint 同样不做机构过滤；
    * 机构名标记表达的是"该规则被授权到这些机构"的共享范围，应用不校验
      人员是否实际属于该机构——这正是共享权限过宽的来源，越界事实交给
      边界层识别，字面层保持与应用观察一致；
    * no-self 是唯一在字面层生效的人员级排除；
    * 目录里从未出现过的标记才算未覆盖（待补）。
    """
    scope = rule.grant_scope
    if scope is None:
        return "incomplete"
    if len(scope) == 0:
        # 显式空范围 = 停用该操作（如 disable-shared）。
        return "denied"
    for token in scope:
        if token in SPECIAL_SCOPES:
            if token == "no-self":
                if state.person == docket.requester:
                    return "denied"
            # assigned / joint 在字面层不附加机构过滤。
            continue
        if token not in teams:
            return "unrecognized"
    return "satisfied"


def literal_decision(
    state: PersonState,
    docket: DocketSnapshot,
    action: str,
    active_rules: tuple[Rule, ...],
    teams: frozenset[str],
) -> Decision:
    """按实际启用的规则复刻应用的判定。"""
    bases: list[Basis] = []
    reasons: list[str] = []
    blockers: list[str] = []
    pending: list[str] = []
    allows: list[str] = []

    for rule in active_rules:
        if rule.action != action:
            continue
        matched = _role_match(rule, state)
        if matched is None:
            pending.append(f"规则 {rule.rule_id} 需要角色 {rule.role}，但目录未交全该人员角色")
            bases.append(Basis("rule", rule.rule_id, "角色关系待目录方补全", None))
            continue
        if not matched:
            continue
        scope = _literal_scope(rule, state, docket, teams)
        if scope == "incomplete":
            pending.append(f"规则 {rule.rule_id} 的 grant_scope 未交全，不能推断风险已消除")
            bases.append(Basis("rule", rule.rule_id, "作用域配置未交全", None))
        elif scope == "unrecognized":
            pending.append(f"规则 {rule.rule_id} 含目录未覆盖的作用域标记")
            bases.append(Basis("rule", rule.rule_id, "作用域标记未被目录覆盖", None))
        elif scope == "denied":
            blockers.append(rule.rule_id)
            bases.append(Basis("rule", rule.rule_id, "规则显式拒绝（空范围/机构不符/本人回避）", False))
        else:
            allows.append(rule.rule_id)
            bases.append(Basis("rule", rule.rule_id, f"角色 {rule.role} 命中且字面作用域通过", None))

    if pending and not allows and not blockers:
        return Decision("pending", tuple(bases), tuple(pending),
                        ("向目录方或应用管理方补全缺项后再判定，当前不得视为无风险",))
    if allows:
        return Decision("allow", tuple(bases),
                        tuple(f"应用依据 {code} 准许" for code in allows),
                        ())
    if blockers:
        return Decision("deny", tuple(bases),
                        tuple(f"应用规则 {code} 拒绝" for code in blockers),
                        ())
    if pending:
        return Decision("pending", tuple(bases), tuple(pending),
                        ("补全配置前按未授权处理",))
    return Decision(
        "deny", (),
        (f"生效策略中没有可供角色匹配的 {action} 授权规则",),
        ("如需该操作，应由管理员提交明确授权并经审查生效",),
    )


# --------------------------------------------------------------------------- 边界层：业务访问边界


def _boundary_parties(docket: DocketSnapshot):
    parties = docket.parties()
    if parties is None:
        return None, "卷宗的 joint/管辖字段未交全，机构边界无法判断"
    return parties, ""


def boundary_decision(
    state: PersonState,
    docket: DocketSnapshot,
    action: str,
    active_rules: tuple[Rule, ...] = (),
) -> Decision:
    """按业务访问边界独立判断。

    普通查询 read 与案件外发 export/approve 是不同许可；复核不得针对
    本人提交的外发；并办卷宗逐机构确认，资格不可跨机构替代。
    """
    parties, party_problem = _boundary_parties(docket)
    if parties is None:
        return Decision(
            "pending", (), (party_problem,),
            ("请资源方补全卷宗管辖/并办机构后再判断",),
        )
    if not state.active_assignments:
        return Decision(
            "deny", (),
            ("该时刻没有在任任职，历史岗位不构成当前授权",),
            ("完成调入/兼岗手续后再办理，或由在岗同岗人员接手",),
        )
    in_jurisdiction = bool(state.teams & parties)
    bases: list[Basis] = []
    pending: list[str] = []
    allows: list[str] = []
    denies: list[str] = []

    if action == "read":
        # 独立资格一：明确的 staff 只读授权（例如其他管理员加入的 maint-admin）。
        for rule in active_rules:
            if rule.action != "read" or rule.role != "staff":
                continue
            if rule.grant_scope is None:
                pending.append(f"staff 只读规则 {rule.rule_id} 的范围未交全")
                continue
            overlap = state.teams & set(rule.grant_scope)
            if state.is_staff() and overlap:
                detail = f"staff 显式只读授权覆盖机构 {sorted(overlap)}"
                allows.append(rule.rule_id)
                bases.append(Basis("rule", rule.rule_id, detail, True))
            elif state.is_staff():
                denies.append(
                    f"staff 只读规则 {rule.rule_id} 限定 {list(rule.grant_scope)}，"
                    f"其机构 {sorted(state.teams)} 不在范围内"
                )
        # 独立资格二：query 岗位 + 辖区（并办时主管方查询保留，主管方本就在并办方内）。
        query = state.has_role("query")
        if query is None:
            pending.append("query 岗位关系未交全")
        elif query and in_jurisdiction:
            allows.append("query+辖区")
            bases.append(Basis("boundary", "jur-query", "query 岗位且所属机构在卷宗管辖/并办方内"))
        elif query and not in_jurisdiction:
            denies.append("query 岗位所属机构不在管辖范围内")

    elif action == "export":
        initiate = state.has_role("initiate")
        if initiate is None:
            return Decision("pending", (),
                            ("initiate（初办/外发起草）岗位关系未交全",),
                            ("向目录方补全任职角色后再判断",))
        if not initiate:
            return Decision(
                "deny", (),
                ("外发发起需要 initiate 岗位，该人员无此明确资格",),
                ("由辖区内具备 initiate 资格的人员发起",),
            )
        if not in_jurisdiction:
            return Decision(
                "deny", (),
                (f"其机构 {sorted(state.teams)} 不在卷宗管辖/并办方 {sorted(parties)} 内，"
                 "不能跨机构发起外发",),
                ("交由卷宗机构内的 initiate 岗位人员发起",),
            )
        allows.append("initiate+辖区")
        bases.append(Basis("boundary", "export-jurisdiction", "initiate 岗位且所属机构为卷宗方"))

    elif action == "approve":
        review = state.has_role("review")
        if review is None:
            return Decision("pending", (),
                            ("review（复核）岗位关系未交全",),
                            ("向目录方补全任职角色后再判断",))
        if not review:
            return Decision(
                "deny", (),
                ("外发复核需要 review 岗位，该人员无此明确资格",),
                ("由同机构 review 岗位人员复核",),
            )
        if not in_jurisdiction:
            return Decision(
                "deny", (),
                (f"其机构 {sorted(state.teams)} 不是并办方 {sorted(parties)}；"
                 "任何一方的资格都不能自行代替另一机构的外发确认",),
                ("逐机构由本机构 review 岗位人员分别确认",),
            )
        if state.person == docket.requester:
            return Decision(
                "deny", (),
                ("该人员是本卷宗外发提交人，不得对自己提交的外发做复核（职责分离）",),
                ("由同一机构内其他 review 岗位人员复核",),
            )
        allows.append("review+机构+回避")
        bases.append(Basis("boundary", "review-segregation",
                           "review 岗位、所属机构为并办方、且非外发提交人"))
    else:  # pragma: no cover - 动作集合由 CLI 约束
        raise ValueError(f"未知操作 {action}")

    if allows:
        return Decision("allow", tuple(bases),
                        tuple(f"边界准许：{code}" for code in allows),
                        ("可在所属机构对应的职责环节办理",))
    if pending:
        return Decision("pending", tuple(bases), tuple(pending),
                        ("补全前不得视为有权或无权，按待补登记",))
    return Decision("deny", tuple(bases), tuple(denies),
                    ("如需开通，应通过经审查的明确授权，不得沿用共享权限",))


# --------------------------------------------------------------------------- 全量矩阵与机构覆盖


@dataclass(frozen=True)
class MatrixRow:
    person: str
    bundle: str
    action: str
    teams: frozenset[str]
    roles: frozenset[str]
    active: bool
    literal: Decision
    boundary: Decision


def evaluate_matrix(
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    ledger: Ledger,
    minute: int,
) -> list[MatrixRow]:
    """对全部在录人员 × 卷宗 × 操作做两层判定。"""
    teams = known_teams(assignments, dockets)
    rows: list[MatrixRow] = []
    for person in all_persons(assignments):
        state = person_state(person, minute, assignments)
        for latest in current_dockets(dockets):
            for action in ACTIONS:
                literal = literal_decision(state, latest, action, ledger.active_rules, teams)
                boundary = boundary_decision(state, latest, action, ledger.active_rules)
                rows.append(
                    MatrixRow(
                        person=person,
                        bundle=latest.bundle,
                        action=action,
                        teams=state.teams,
                        roles=state.roles,
                        active=bool(state.active_assignments),
                        literal=literal,
                        boundary=boundary,
                    )
                )
    return rows


def classify_row(row: MatrixRow) -> str:
    """把一行双层判定归入管理员视角的处置类别。"""
    if row.literal.is_pending or row.boundary.is_pending:
        return "待补"
    if row.literal.verdict == "allow" and row.boundary.verdict == "deny":
        return "待处置"
    if row.literal.verdict == "deny" and row.boundary.verdict == "allow":
        return "仍需交接"
    if row.literal.verdict == "allow" and row.boundary.verdict == "allow":
        return "准予"
    return "合规拒绝"


@dataclass(frozen=True)
class CoverageGap:
    bundle: str
    action: str
    party: str | None
    detail: str
    next_step: str


def coverage_report(
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    ledger: Ledger,
    minute: int,
) -> list[CoverageGap]:
    """逐卷宗检查外发发起与逐机构复核是否还有合格人员接手。"""
    gaps: list[CoverageGap] = []
    for latest in current_dockets(dockets):
        parties = latest.parties()
        if parties is None:
            gaps.append(CoverageGap(latest.bundle, "read/export/approve", None,
                                    "管辖/并办字段未交全，无法核对接手人",
                                    "请资源方补全 joint 字段"))
            continue
        # 外发发起：卷宗方内至少一名 initiate 且非提交人限制不针对发起。
        eligible_initiators = []
        for person in all_persons(assignments):
            state = person_state(person, minute, assignments)
            decision = boundary_decision(state, latest, "export", ledger.active_rules)
            if decision.verdict == "allow":
                eligible_initiators.append(person)
        if not eligible_initiators:
            gaps.append(CoverageGap(
                latest.bundle, "export", None,
                f"管辖方 {sorted(parties)} 内当前没有合格的 initiate 人员，外发无人发起",
                "办理岗位交接或补任 initiate 资格",
            ))
        # 逐机构复核：每个并办方都要有非提交人的 review 人员。
        for party in sorted(parties):
            eligible = []
            uncertain = []
            for person in all_persons(assignments):
                state = person_state(person, minute, assignments)
                if party not in state.teams:
                    continue
                decision = boundary_decision(state, latest, "approve", ledger.active_rules)
                if decision.verdict == "allow":
                    eligible.append(person)
                elif decision.verdict == "pending":
                    uncertain.append(person)
            if not eligible:
                detail = (f"机构 {party} 没有可复核本卷宗的合格 review 人员"
                          + (f"（待补人员：{sorted(uncertain)}）" if uncertain else ""))
                gaps.append(CoverageGap(latest.bundle, "approve", party, detail,
                                        f"为 {party} 安排独立复核人并完成交接"))
        # 主管方查询保留：必须是主管方在任人员具备 query 岗位（staff 只读
        # 授权是另一份独立资格，不能代替主管方的查询职责保留）。
        lead_query_ok = False
        lead_query_unknown: list[str] = []
        for person in all_persons(assignments):
            state = person_state(person, minute, assignments)
            if latest.lead not in state.teams:
                continue
            query = state.has_role("query")
            if query is True:
                lead_query_ok = True
            elif query is None:
                lead_query_unknown.append(person)
        if not lead_query_ok:
            if lead_query_unknown:
                gaps.append(CoverageGap(
                    latest.bundle, "read", latest.lead,
                    f"主管方 {latest.lead} 无明确 query 岗位，"
                    f"在岗人员 {sorted(lead_query_unknown)} 角色未交全",
                    "向目录方补全角色后确认主管方查询是否保留",
                ))
            else:
                gaps.append(CoverageGap(
                    latest.bundle, "read", latest.lead,
                    f"主管方 {latest.lead} 当前无 query 岗位人员，主管方查询未保留",
                    "保留或补任主管方 query 岗位；staff 只读授权不替代主管方查询",
                ))
    return gaps
