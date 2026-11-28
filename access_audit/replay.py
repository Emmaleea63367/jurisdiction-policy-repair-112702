"""应用回放：用字面层复刻回放端观察到的实际判定。

观察记录里带应用自身的修订号与解释 codes；回放必须按该修订下的规则
复刻，而不是按当前生效修订，否则无法验证"当时实际启用的策略"。
"""
from __future__ import annotations

from dataclasses import dataclass

from .engine import (
    literal_decision,
    known_teams,
    person_state,
    rules_for_revision,
)
from .model import Assignment, ChangeRecord, DocketSnapshot, Observation, Rule


@dataclass(frozen=True)
class ReplayLine:
    observation: Observation
    replayed_verdict: str
    matches: bool
    cited_codes: tuple[str, ...]
    unknown_codes: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class ReplayReport:
    lines: tuple[ReplayLine, ...]
    ordering_notes: tuple[str, ...]

    @property
    def all_match(self) -> bool:
        return all(line.matches for line in self.lines)


def replay_observations(
    observations: list[Observation],
    rules: list[Rule],
    assignments: list[Assignment],
    dockets: list[DocketSnapshot],
    changes: list[ChangeRecord],
    minute: int = 0,
) -> ReplayReport:
    """逐条复刻观察结果并核对解释引用是否真实存在于该修订规则中。

    观察来源没有分钟字段（其结构由来源固定），身份按任职区间在给定
    minute 上取值，默认取演练起点；本数据中相关任职均自 0 起在任。
    """
    teams = known_teams(assignments, dockets)
    lines: list[ReplayLine] = []
    for observation in observations:
        rev_rules = rules_for_revision(rules, observation.revision)
        known_codes = {r.rule_id for r in rev_rules}
        unknown = tuple(code for code in observation.codes if code not in known_codes)
        docket = next((d for d in dockets if d.bundle == observation.bundle), None)
        if docket is None:
            lines.append(ReplayLine(
                observation, "pending", False, observation.codes, unknown,
                "资源方未提供该卷宗，无法复刻",
            ))
            continue
        state = person_state(observation.person, minute, assignments)
        decision = literal_decision(state, docket, observation.action, rev_rules, teams)
        matches = decision.verdict == ("allow" if observation.allowed else "deny")
        cited = tuple(code for code in observation.codes if code in known_codes)
        lines.append(ReplayLine(
            observation,
            decision.verdict,
            matches,
            cited,
            unknown,
            "；".join(decision.reasons) if decision.reasons else "无匹配规则",
        ))

    notes: list[str] = []
    # 到达顺序核对：观察引用的规则是否已先于观察到达；变更记录只表明
    # 应用何时把规则置为 active，规则文本本身以策略来源 seq 为准。
    seq_by_rule = {rule.rule_id: rule.seq for rule in rules}
    for line in lines:
        for code in line.observation.codes:
            rule_seq = seq_by_rule.get(code)
            if rule_seq is not None and rule_seq > line.observation.seq:
                notes.append(
                    f"seq{line.observation.seq} 观察引用了尚未到达的规则 {code}"
                    f"（规则 seq{rule_seq}），来源时序冲突"
                )
        # 旁证：观察发生时该修订的 active 变更记录是否也已到达。
        target_rev = line.observation.revision
        activation = next((c for c in changes
                           if c.after == target_rev and c.state == "active"), None)
        if activation is not None and activation.seq > line.observation.seq:
            notes.append(
                f"seq{line.observation.seq} 观察已按修订 {target_rev} 放行，但对应 "
                f"active 变更 {activation.change_id} 到 seq{activation.seq} 才到达；"
                "应用观察与管理员记录存在到达时差，以应用观察为准"
            )
    return ReplayReport(tuple(lines), tuple(notes))
