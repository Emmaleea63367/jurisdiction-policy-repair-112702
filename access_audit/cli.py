"""权限管理员本地演练与更正执行的命令行入口。

数据目录、到达截止序号与演练分钟均由参数给出，便于管理员把新项目里
新的目录与卷宗关系重复带入，而不改动工具代码。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .corrections import analyze
from .load import DEFAULT_DIRECTORY, load_dataset
from .report import (
    ACTION_LABEL,
    render_corrections,
    render_explain,
    render_matrix,
    render_replay,
    render_timeline,
)
from .replay import replay_observations


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, frozenset):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"不可序列化的类型 {type(value)!r}")


def _emit(text: str, payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, default=_json_default, indent=2))
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="access_audit",
        description="办案协同平台权限收敛演练与更正工具（纯标准库）",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DIRECTORY,
                        help="来源 JSON Lines 所在目录，默认项目内 sources/")
    parser.add_argument("--through", type=int, default=None,
                        help="只纳入到达序号 <= 该值的记录，用于中途演练")
    parser.add_argument("--minute", type=int, default=30,
                        help="演练分钟（相对起点），任职按 [from, until) 取值，默认 30")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    replay_cmd = sub.add_parser("replay", help="按应用观察复刻原策略的实际获准结果")
    replay_cmd.add_argument("--at-minute", type=int, default=0,
                            help="复刻观察时身份取值的分钟，观察来源无分钟字段，默认起点 0")
    sub.add_parser("matrix", help="人员×卷宗×操作的双层访问矩阵")

    explain = sub.add_parser("explain", help="解释单人单卷宗单操作的有效依据与下一步")
    explain.add_argument("person")
    explain.add_argument("bundle")
    explain.add_argument("action", choices=tuple(ACTION_LABEL))

    sub.add_parser("corrections", help="更正候选评估、风险定位与交接覆盖核对")
    sub.add_parser("timeline", help="按到达顺序列出授权面漂移事件")
    sub.add_parser("report", help="综合报告（默认入口，等价于依次运行各视图）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = load_dataset(args.data_dir, args.through)

    if args.command == "replay":
        # 观察来源不带分钟字段，复刻身份按 --at-minute（默认演练起点 0）。
        report = replay_observations(
            data["observations"], data["rules"], data["assignments"],
            data["dockets"], data["changes"], minute=args.at_minute,
        )
        _emit(render_replay(report), report, args.json)
        return 0 if report.all_match else 2

    if args.command == "matrix":
        from .engine import policy_ledger, evaluate_matrix
        ledger = policy_ledger(data["rules"], data["changes"], args.through)
        rows = evaluate_matrix(data["assignments"], data["dockets"], ledger, args.minute)
        _emit(render_matrix(rows), rows, args.json)
        return 0

    if args.command == "explain":
        text = render_explain(
            args.person, args.bundle, args.action,
            data["assignments"], data["dockets"], data["rules"], data["changes"],
            args.minute,
        )
        _emit(text, {"person": args.person, "bundle": args.bundle,
                     "action": args.action, "minute": args.minute}, args.json)
        return 0

    if args.command in {"corrections", "timeline", "report"}:
        analysis = analyze(
            data["raw_assignments"], data["assignments"], data["dockets"],
            data["rules"], data["changes"], args.minute, args.through,
        )
        if args.command == "timeline":
            _emit(render_timeline(analysis), analysis.drift, args.json)
        elif args.command == "corrections":
            _emit(render_corrections(analysis), analysis, args.json)
        else:
            replay_report = replay_observations(
                data["observations"], data["rules"], data["assignments"],
                data["dockets"], data["changes"], minute=0,
            )
            sections = [
                render_replay(replay_report),
                render_timeline(analysis),
                render_corrections(analysis),
            ]
            _emit("\n\n".join(sections),
                  {"replay": replay_report, "analysis": analysis}, args.json)
        return 0

    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
