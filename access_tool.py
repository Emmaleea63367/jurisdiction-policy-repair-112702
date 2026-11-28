#!/usr/bin/env python3
"""命令行入口脚本，支持 `python3 -I access_tool.py ...` 隔离运行。

隔离模式（-I）下部分 Python 构建不把脚本目录注入 sys.path，因此这里
显式加入脚本自身所在的项目根；数据目录通过 --data-dir 参数传入，
永远不会成为模块搜索路径，数据里即使有同名模块也无法被 import。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from access_audit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
