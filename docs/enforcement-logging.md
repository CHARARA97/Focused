# 日志与溯源规范

> 状态：**已实施 1–7**（分支 `feature/new-work`；`--explain` 待做）。配套：[enforcement-mode.md](enforcement-mode.md)、[enforcement-interfaces.md](enforcement-interfaces.md)。
> 目标：**任何一次决策都能被单独重建，任何一串历史都能被一次查询取回。**

---

## 1. 结论

三条输出流，一个事件源：

| 流 | 读者 | 格式 | 保留 | 用途 |
|---|---|---|---|---|
| **journal**（stderr → journald） | 人 | `event=<kind> k=v` | 由 journald 轮转 | 正在发生什么 |
| **audit.jsonl** | 机器 / 事后审计 | JSONL，0600，只追加 | 永不删除 | 发生过什么 |
| **event bus** | 插件 / webhook | dict，环形 200 | 内存 | 实时通知 |

**一个领域事件只构造一次（`ActionEvent`），三种输出都是它的投影。**
运维日志（启动、配置、插件加载）不是领域事件，只进 journal。

唯一的完整记录是 **audit.jsonl**：journal 会被轮转，bus 是环形的。

---

## 2. 现状的四个缺陷

### D-1 事件总线丢字段（真 bug）

[app/events.py:52](../focused/app/events.py) 的 `record()` 只保留七个字段：

```python
event = {"seq", "ts", "kind", "source", "name", "pid", "detail"}
```

于是 `ActionEvent.fields()` 里的 `reason / pids / count / duration_s / mode` **全部被丢弃**。
插件和 webhook 永远看不到"为什么被拒"、"杀了几个"、"持续多久"。

### D-2 没有关联标识

每条事件是孤立的。无法回答"第 N 轮扫描里发生了什么"，也无法把同一应用的多次终止串成一条线。

### D-3 pid 不是身份

审计只记 `pid`。PID 被复用之后，两条记录无法区分是两个不同的进程。
必须记 `(pid, starttime)`——这也是 `Killer` 发信号前的唯一判据。

### D-4 重复事件没有聚合

`enforce.kill` 已按 episode 聚合，但 `enforce.deny` 与 `enforce.would-kill` 是**每轮一条**：
audit 模式下一个常驻黑名单进程会每秒写一行，一天 86400 行。

---

## 3. 事件 schema

### 3.1 公共字段（每条事件都有）

| 字段 | 类型 | 含义 |
|---|---|---|
| `kind` | string | 事件名，见 §4 |
| `ts` | float | wall clock，给人读 |
| `mono` | float | 单调时钟，算间隔（避免 NTP 跳变） |
| `scan` | int | 扫描序号，单调递增；同一轮的所有事件共享它 |

### 3.2 目标字段（有目标的事件）

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | string | 显示名 |
| `pid` | int | 根进程 |
| `starttime` | int | **根进程的 start time；`(pid, starttime)` 才是身份** |
| `pids` | int[] | 本轮涉及的整棵树 |
| `tree` | int | `len(pids)`，便于快速筛选"杀了一大片的" |
| `rule` | string | 命中的规则 |

### 3.3 决策字段（有目标的事件）

| 字段 | 类型 | 含义 |
|---|---|---|
| `intent` | string | 本轮的意图：`work` / `break` / `paused` / `no-session` / `disabled` |
| `policy` | int | 规则版本号；规则一变就变，用来确认"这轮用的是哪份规则" |
| `verdict` | string | `allow` / `deny` |

### 3.4 结果字段（按事件类型追加）

| 字段 | 类型 | 用于 |
|---|---|---|
| `reason` | string | 拒绝原因（`DenyReason` 的值） |
| `result` | string | 单个 pid 的信号结果（`sent/gone/reused/denied/unverified`） |
| `count` | int | 窗口内累计次数 |
| `duration_s` | float | 窗口持续秒数 |
| `roots` | int | 窗口内不同根进程数 |
| `mode` | string | `audit` / `kill` |
| `duration_ms` | float | 单轮扫描耗时 |

### 3.5 示例

```json
{"kind":"enforce.kill","ts":1730000000.12,"mono":12345.6,"scan":8842,
 "name":"firefox","pid":4242,"starttime":887766,"pids":[4242,4243],"tree":2,
 "rule":"blacklist.name:firefox","mode":"kill"}

{"kind":"enforce.deny","ts":1730000001.05,"mono":12346.5,"scan":8843,
 "name":"systemd","pid":3300,"starttime":9911,
 "reason":"target-is-ancestor","rule":"blacklist.name:systemd"}

{"kind":"enforce.kill.summary","ts":1730000062.0,"mono":12407.5,"scan":8903,
 "name":"firefox","rule":"blacklist.name:firefox","count":47,"duration_s":62.0,"roots":3}
```

---

## 4. 事件词汇表与级别

| kind | 何时写 | 级别 | 关键字段 |
|---|---|---|---|
| `enforce.gate` | 门控**变化**时（不是每轮） | info | `reason`, `mode` |
| `enforce.deny` | 某目标**首次**被拒（窗口内） | warning | `reason`, `rule` |
| `enforce.deny.summary` | 拒绝窗口结束且 count>1 | warning | `count`, `duration_s` |
| `enforce.would-kill` | audit 模式下**首次**命中 | info | `rule`, `mode` |
| `enforce.kill` | 窗口内**首次**真正终止 | info | `rule`, `mode` |
| `enforce.kill.summary` | 窗口结束且 count>1 | info | `count`, `duration_s` |
| `enforce.signal-failed` | 单个 pid 返回 denied/unverified | **error** | `result` |
| `enforce.mode` | mode 改变 | warning | `mode` |
| `scan.slow` | 单轮 > 100 ms | warning | `duration_ms` |
| `scan.fail` | 扫描抛异常 | error | `error` |
| `scan.degraded` | 连续失败达阈值 | error | `failures` |

**规则：重复不写、状态不变不写、热路径不写 info。**
每轮扫描最多一条 journal 行（gate 变化或 slow scan）。

---

## 5. 关联与去重

### 5.1 三个标识

```
scan      —— 一轮扫描的 id（每次 scan_once 递增）
(pid, starttime) —— 一个进程的身份（PID 复用后仍然唯一）
episode   —— 一个应用的一段连续处置（name + first_at）
```

有了这三个，任意一条记录都能定位到"哪一轮、哪个进程、哪个时段"。

### 5.2 去重规则

| 事件 | 去重键 | 窗口 | 汇总 |
|---|---|---|---|
| `enforce.kill` | `name` | 60s | `enforce.kill.summary` |
| `enforce.would-kill` | `name` | 60s | 同名字段 |
| `enforce.deny` | `(reason, name)` | 60s | `enforce.deny.summary` |

去重状态是**观察性状态，绝不参与决策**（与 `EpisodeBook` 同一条规矩）。

### 5.3 为什么按 name 而不是 pid

应用被杀后会重启，pid 必然变化。按 pid 去重等于没有去重。

---

## 6. 查询手册

**为什么这个进程现在会被杀？**（离线，不依赖守护进程）

```bash
focused --explain 4242
```

输出与 audit 同 schema：`evaluate → tree → starttimes → preflight → action`。

**为什么它没被杀？**（历史）

```bash
jq -c 'select(.kind=="enforce.deny" and .pid==4242)' ~/.local/share/focused/audit.jsonl
```

**它被杀了几次？**

```bash
jq -c 'select(.kind=="enforce.kill.summary" and .name=="firefox")' audit.jsonl
```

**某一轮扫描发生了什么？**

```bash
jq -c 'select(.scan==8842)' audit.jsonl
```

**有进程在反复重生吗？**

```bash
jq -c 'select(.kind=="enforce.kill.summary" and .count>10)' audit.jsonl
```

**本轮为什么一个都没杀？**（在线）

```bash
curl -s localhost:8766/api/v1/status | jq '.scan_report, .enforcement, .health'
```

---

## 7. 改造清单

| # | 改动 | 文件 |
|---|---|---|
| 1 | `ActionEvent` 增加 `scan` / `mono` / `starttime` / `tree` / `result` | `domain/reasons.py` |
| 2 | `EventBus.record` 保留全部字段（新增字段透传，`detail` 保留） | `app/events.py` |
| 3 | `Selection` 暴露 `starttime`（根进程） | `domain/targets.py` |
| 4 | `Enforcer` 接收 `scan_id`，写入所有事件 | `app/enforcer.py` |
| 5 | `DenyBook`：拒绝按 `(reason, name)` 聚合，产出 `enforce.deny.summary` | `domain/history.py` |
| 6 | 单一 sink：`engine._record(event)` → audit + bus；journal 由同一事件渲染 | `engine.py` |
| 7 | `scan.slow` 事件（>100 ms） | `app/scan.py` |
| 8 | `--explain` 输出同一 schema | `focusedd.py`（**未做**） |

已完成 1–7。`scan_once` 的 `by_reason` 同时记录 `protected` / `seen` / `gone` / `throttled`：
**被跳过的目标以计数说明，不逐条写事件**——这是"每个 continue 都有理由"与"重复不写"之间的落点。

`scan.slow` 是运维诊断，只进 journal，不进审计（见 §1 的边界）。

---

## 8. 不做的事

| 不要 | 为什么 |
|---|---|
| 在 journal 里记录每个目标 | 每秒几十行，人读不了；那是 audit 的职责 |
| 让 audit 丢事件 | 审计完整性优先于体积；体积靠聚合控制，不靠丢弃 |
| 记录 cmdline / URL / token | 可能含敏感信息；`name + rule` 足够定位 |
| 用 pid 做身份 | PID 复用后无法区分两个进程 |
| 用 wall clock 算间隔 | NTP 调整会让间隔变成负数 |
| 在热路径写 info | 每秒一行会把 journal 淹掉，真正的问题事件被埋 |
| 让插件依赖 `detail` 字符串 | 它是旧兼容字段，结构化字段才是契约 |
