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

## 权限收敛演练与更正工具 `access_audit`

工具把"应用实际判定"与"业务访问边界"分成两层独立计算，再对照归类。
字面层只复刻应用按实际启用规则会给出的结果（由回放对照 observations
校准）；边界层按职责分离与管辖规则独立判断，不依赖应用配置。两层不一致
即为风险、缺口或待补，不能通过角色名前后对比得出结论。

### 判定语义（与业务约定一致）

- 普通查询 `read` 与案件外发 `export`、外发复核 `approve` 是不同许可，分别判定。
- 兼有 initiate 与 review 的人员可在不同卷宗履职，但不得复核自己提交的外发。
- 并办卷宗按机构逐家确认：任何一方的资格都不能代替另一机构的外发确认；主管方查询保留。
- `queued` 变更不产生效果；只有变更记录 `active` 列表中的规则是生效依据，提交成功不代表更正完成。
- 一组规则并用才守得住职责分离时，只启用一部分会让原本应禁止的复核窗口继续开放，组评估直接标为未完成。
- 目录角色为 null、规则 `grant_scope` 为空、卷宗 `joint` 为 null 等缺项一律判"待补"，不得推断风险已消除。
- 同一任职/卷宗的后到记录是更正：判定只用最新快照，完整到达历史在漂移视图中保留。

### 命令

全局参数（放在子命令前）：`--data-dir 目录`（默认 `sources/`，可重复带入新项目）、
`--through 序号`（只看该到达序号之前的世界）、`--minute 分钟`（按 `[from, until)`
取在任身份，默认 30）、`--json`（机器可读输出）。

```bash
# 复刻应用回放端的实际获准结果，全部一致时退出码 0，否则 2
python3 -I access_tool.py replay
# 人员 × 卷宗 × 操作的双层访问矩阵与处置类别
python3 -I access_tool.py --minute 30 matrix
# 单人单卷宗单操作的有效依据、边界条件与可采取的下一步
python3 -I access_tool.py --minute 30 explain p1 bun-7 approve
# 规则级处置、职责分离并用核对、四个处置桶、接手覆盖与漂移事件
python3 -I access_tool.py --minute 30 corrections
# 授权面漂移时序（调入、任职更正、卷宗调辖、修订生效/排队）
python3 -I access_tool.py timeline
# 综合报告；中途演练示例：只看到 seq24（修订15仅 cross-unit 生效）
python3 -I access_tool.py --through 24 --minute 20 report
```

处置桶含义：**待处置**＝应用仍准许但边界禁止的实际风险；**准予更正**＝两层
均准许的合法事项（含因另一份独立资格仍可办理的情形，依据会分别列出）；
**仍需交接**＝边界允许但应用已不支持；**待补**＝目录或资源方缺项。
`--json` 输出与文本视图同源，可供脚本比对批准时边界与实际允许操作。

建议以 `python3 -I` 运行：隔离模式不读取环境变量与用户 site 包，数据目录
只通过 `--data-dir` 传入、不会进入模块搜索路径，数据内的同名 `.py` 无法被导入。

