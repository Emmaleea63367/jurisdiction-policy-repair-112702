"""从数据目录载入演练资料。

数据目录由参数传入（默认项目内 sources/）。载入时只做结构校验，
不做任何权限推断；来源里的 null 原样保留。
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import Assignment, ChangeRecord, DocketSnapshot, Observation, Rule

DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "sources"

REQUIRED_SOURCES = ("directory", "dockets", "policy", "observations", "changes")


def _rows(directory: Path, name: str, through: int | None) -> list[dict]:
    path = directory / f"{name}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"数据目录缺少来源文件：{path}")
    rows: list[dict] = []
    with path.open(encoding="utf-8") as document:
        for line_number, line in enumerate(document, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or type(row.get("seq")) is not int:
                raise ValueError(f"{name} 第 {line_number} 行缺少序号或对象结构")
            if through is None or row["seq"] <= through:
                rows.append(row)
    rows.sort(key=lambda row: row["seq"])
    return rows


def _opt_tuple(value) -> tuple | None:
    if value is None:
        return None
    return tuple(value)


def load_dataset(directory: Path | str = DEFAULT_DIRECTORY, through: int | None = None) -> dict:
    """载入全部来源并按到达序号排序。

    返回带类型对象的字典；through 可把演练限制在某到达序号之前，
    用于观察中途变更。
    """
    directory = Path(directory)
    missing = [name for name in REQUIRED_SOURCES if not (directory / f"{name}.jsonl").exists()]
    if missing:
        raise FileNotFoundError(f"数据目录 {directory} 缺少来源：{', '.join(missing)}")

    # 同一 assignment 的后到记录是对前一条的更正，只保留最新快照；
    # 任职的完整到达历史由更正分析的漂移视图按 seq 呈现。
    assignment_rows: dict[str, dict] = {}
    for row in _rows(directory, "directory", through):
        assignment_rows[row["assignment"]] = row
    assignments = [
        Assignment(
            seq=row["seq"],
            assignment_id=row["assignment"],
            person=row["person"],
            team=row["team"],
            roles=_opt_tuple(row.get("roles")),
            from_minute=row["from"],
            until_minute=row["until"],
        )
        for row in sorted(assignment_rows.values(), key=lambda row: row["seq"])
    ]
    dockets = [
        DocketSnapshot(
            seq=row["seq"],
            bundle=row["bundle"],
            policy_rev=row["policy_rev"],
            lead=row["lead"],
            joint=_opt_tuple(row.get("joint")),
            stage=row["stage"],
            requester=row["requester"],
        )
        for row in _rows(directory, "dockets", through)
    ]
    rules = [
        Rule(
            seq=row["seq"],
            app=row["app"],
            revision=row["revision"],
            rule_id=row["rule"],
            role=row["role"],
            action=row["action"],
            grant_scope=_opt_tuple(row.get("grant_scope")),
            group=row.get("group"),
        )
        for row in _rows(directory, "policy", through)
    ]
    observations = [
        Observation(
            seq=row["seq"],
            app=row["app"],
            revision=row["revision"],
            person=row["person"],
            bundle=row["bundle"],
            action=row["action"],
            allowed=row["allowed"],
            codes=tuple(row.get("codes", ())),
        )
        for row in _rows(directory, "observations", through)
    ]
    changes = [
        ChangeRecord(
            seq=row["seq"],
            change_id=row["change"],
            before=row["before"],
            after=row["after"],
            state=row["state"],
            active=tuple(row.get("active", ())),
        )
        for row in _rows(directory, "changes", through)
    ]
    return {
        "directory": directory,
        "raw_assignments": [
            Assignment(
                seq=row["seq"],
                assignment_id=row["assignment"],
                person=row["person"],
                team=row["team"],
                roles=_opt_tuple(row.get("roles")),
                from_minute=row["from"],
                until_minute=row["until"],
            )
            for row in _rows(directory, "directory", through)
        ],
        "assignments": assignments,
        "dockets": dockets,
        "rules": rules,
        "observations": observations,
        "changes": changes,
    }
