"""来源记录的领域模型。

每个数据类对应一个来源里的一行原始对象，保留来源缺省的 null，
不把"未交全"改写成空集合或无风险。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Assignment:
    """目录方人员任职记录（directory.jsonl 一行）。"""

    seq: int
    assignment_id: str
    person: str
    team: str
    roles: Optional[tuple[str, ...]]
    """显式角色；None 表示来源未交全，不能推断为无角色或任意角色。"""
    from_minute: int
    until_minute: int

    def covers(self, minute: int) -> bool:
        """任职区间采用左闭右开 [from, until)。"""
        return self.from_minute <= minute < self.until_minute


@dataclass(frozen=True)
class DocketSnapshot:
    """卷宗在某个到达序号上的归属与办理状况。"""

    seq: int
    bundle: str
    policy_rev: int
    lead: str
    joint: Optional[tuple[str, ...]]
    """需要实际参加外发确认的机构集合；None 表示该字段未交全。"""
    stage: str
    requester: str

    def parties(self) -> Optional[frozenset[str]]:
        """并办方集合；非并办卷宗为主管方单元素集合，未交全时为 None。"""
        if self.joint is None:
            return None
        return frozenset(self.joint) if self.joint else frozenset({self.lead})


@dataclass(frozen=True)
class Rule:
    """应用管理方观察到的一条策略规则。"""

    seq: int
    app: str
    revision: int
    rule_id: str
    role: str
    action: str
    grant_scope: Optional[tuple[str, ...]]
    """None 表示配置未交全（README 明确空值不等于空范围）。"""
    group: Optional[str]


@dataclass(frozen=True)
class Observation:
    """应用回放端的一次实际允许/拒绝记录。"""

    seq: int
    app: str
    revision: int
    person: str
    bundle: str
    action: str
    allowed: bool
    codes: tuple[str, ...]


@dataclass(frozen=True)
class ChangeRecord:
    """管理员提交与应用实际生效记录；queued 不是实际状态。"""

    seq: int
    change_id: str
    before: int
    after: int
    state: str
    active: tuple[str, ...]


@dataclass(frozen=True)
class PersonState:
    """某人在指定分钟的有效身份关系汇总。"""

    person: str
    minute: int
    active_assignments: tuple[Assignment, ...]
    teams: frozenset[str]
    roles: frozenset[str]
    role_unknown: bool
    """任一有效任职的 roles 为 null 时为 True，角色类判断只能给待补。"""

    def has_role(self, role: str) -> Optional[bool]:
        """显式角色归属；未知时返回 None 而不是 False。"""
        if self.role_unknown:
            return None
        return role in self.roles

    def is_staff(self) -> Optional[bool]:
        """staff 是共享权限沿用的基线身份：有在任任职即具备。

        若存在在任任职但其角色未交全，任职事实本身仍可确认，
        因此 staff 判断不受 role_unknown 影响；无在任任职才是 False。
        """
        return bool(self.active_assignments)
