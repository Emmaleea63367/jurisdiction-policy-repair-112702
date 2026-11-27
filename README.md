# 并办卷宗的权限收敛和更正

办案部门、目录方和应用管理员分别提供输入。应用观察是原应用实际判定的合成记录，目录未列出的关系不能当成明确无资格。

资料中的对象、身份及记录均为虚构。各来源使用 UTF-8 JSON Lines，一行是一个原始对象。`seq` 是全局到达序号，按演练资料的可知顺序排列。事实发生时刻、确认时刻与适用时段以同行中的独立字段为准，用相对演练起点的整数分钟表达。JSON `null` 保留来源的缺省或未确认值。除非来源明确使用相同标识，编号只在所属来源和实体域内有意义。

## 来源

- `sources/directory.jsonl`　人员任职记录。p1 的两个岗位来自独立的任职。from/until 为任职区间。
- `sources/dockets.jsonl`　卷宗归属与办理阶段。joint 为需要实际参加外发确认的机构集合。requester 为提出外发的人员。
- `sources/policy.jsonl`　应用管理方观察到的策略规则。grant_scope 为空时只表示配置未交全。rules 属于部署组时由 group 指明。
- `sources/observations.jsonl`　应用回放端的实际允许结果，codes 是应用本身提供的解释引用。
- `sources/changes.jsonl`　管理员提交与应用生效记录。active 说明实际启用的规则集合，queued 不是实际状态。

## 查看记录

使用 Python 3.11.2 标准库，无需安装第三方包。以下命令列出可读取的来源、查看单个来源记录并检查资料格式。读取命令按输入顺序输出 JSON，可通过 `--through` 限制已到达的记录。

```bash
python3 source_records.py
python3 source_records.py directory
python3 -m unittest discover -s tests -v
```
