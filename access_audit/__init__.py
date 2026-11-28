"""办案协同平台权限收敛演练与更正工具。

只依赖 Python 3.11 标准库。数据目录由调用方通过路径参数传入，
包本身不假定数据位置，也不把数据目录加入模块搜索路径。
"""

__all__ = ["model", "load", "engine", "replay", "corrections", "report"]
