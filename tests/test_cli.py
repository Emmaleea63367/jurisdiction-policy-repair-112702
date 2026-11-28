"""CLI 入口、隔离运行与可重复载入数据目录的测试。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    # -I 隔离模式：忽略环境变量与用户 site 包，数据目录即使含同名模块
    # 也不会污染导入；脚本所在的项目根由解释器自行加入 sys.path。
    return subprocess.run(
        [sys.executable, "-I", str(ROOT / "access_tool.py"), *args],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


class CliTest(unittest.TestCase):
    def test_replay_exits_zero_and_json_is_machine_readable(self):
        proc = run_cli("--json", "replay")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload["lines"]), 5)
        self.assertTrue(all(line["matches"] for line in payload["lines"]))

    def test_explain_renders_identity_and_next_step(self):
        proc = run_cli("--minute", "30", "explain", "p1", "bun-7", "approve")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("du-1", proc.stdout)
        self.assertIn("du-2", proc.stdout)
        self.assertIn("自己提交的外发", proc.stdout)
        self.assertIn("待处置", proc.stdout)

    def test_corrections_report_contains_all_buckets(self):
        proc = run_cli("--minute", "30", "corrections")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for title in ("待处置", "准予更正", "仍需交接", "待补", "卷宗接手覆盖", "中途漂移"):
            self.assertIn(title, proc.stdout)

    def test_through_replays_mid_shift_state(self):
        proc = run_cli("--through", "24", "--minute", "20", "--json", "corrections")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["ledger_revision"], 15)
        groups = {g["group"]: g for g in payload["groups"]}
        self.assertEqual(groups["repair-1"]["active_rules"][0]["rule_id"], "cross-unit")
        self.assertFalse(groups["repair-1"]["complete"])

    def test_dataset_can_be_reloaded_from_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = Path(tmp)
            for source in SOURCES.glob("*.jsonl"):
                (extra / source.name).write_text(source.read_text(encoding="utf-8"),
                                                  encoding="utf-8")
            proc = run_cli("--data-dir", str(extra), "replay")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("回放全部一致", proc.stdout)
    def test_missing_source_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("--data-dir", tmp, "replay")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("缺少来源", proc.stderr)


if __name__ == "__main__":
    unittest.main()
