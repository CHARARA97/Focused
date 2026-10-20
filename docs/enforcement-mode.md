# 执行模式（enforcement）— 从冻结转向终止

> 状态：**提案 v4，尚未实施**（分支 `feature/new-work`）。
> 演进：v1 假设 TERM→KILL 升级；v2 改为"匹配就杀"；v3 改为 SIGTERM + 严格向下；
> **v4 定为 SIGKILL，风险由使用者承担**，并补上"历史与审计"一节（重复终止会刷爆现有三份历史）。
> 本文取代 [freeze-mode.md](freeze-mode.md) 的机制部分。
> 基线：`python3 -m pytest -q` → **407 passed, 5 skipped, 594 subtests**（56s）。

---

## 1. 结论先行

1. **动作是 SIGKILL**：不可捕获、不可阻塞，进程没有保存机会。**这是产品层面的数据丢失风险，由使用者承担**，
   所以默认 `mode=audit`、切换 `kill` 需要显式确认（§6-D9）。
2. **目标集合只由 ppid 子树决定，且只向下**：从匹配到的根出发取它和它的后代；
   **永不向上取父进程，永不使用 cgroup 成员**。这一条消灭了冻结时代最大的误杀源，因此
   `cgroupfs.py`（317 行）整个删除。
3. **"宁可不杀"落到代码上**：任何不确定 → 拒绝 + 记原因，下一轮重新判断。拒绝是默认方向，不是失败。
4. 决策"匹配就杀、重复也杀、直接 SIGKILL"之后，**执行路径不需要任何跨轮状态**；
   但"重复"会把现有三份历史（`_recent` / 事件环 / 审计文件）全部刷爆，所以 §9 把历史改成**按事件聚合**。

```
snapshot → evaluate → 选树 → 安全预检 → kill → record
  数据       纯        纯        纯        副作用   聚合追加
```

---

## 2. 决策记录

| # | 决策 | 架构后果 |
|---|---|---|
| 1 | 匹配到就杀，重复匹配重复杀 | 删 `_handled`、会话记忆、去重逻辑 |
| 2 | **直接 SIGKILL，不等待、不升级** | 删升级状态机、`advance()`、宽限期、in-flight 表 |
| 3 | 默认只记录，直到打开"杀死真进程"开关 | `enforcement.mode: audit \| kill`，默认 `audit` |
| 4 | 休息/暂停期间不杀 | 删全部 thaw 分支，保留门控（§8） |
| 5 | 不考虑 freeze | 删冻结实现、配置段、恢复路径、`ExecStopPost` |
| 6 | 只需重复杀，不需要抑制 | 删冷却表、退避、按分钟限流（保留单轮上限） |
| 7 | **根 + 子树，绝不牵连父进程** | 删 cgroup 选目标；新增祖先自伤检查 |
| 8 | **安全第一，宁可不杀也不能误杀** | 预检 fail-closed；拒绝原因全部可查 |
| 9 | **SIGKILL，风险由使用者承担** | 默认 audit + 切换需确认 + 强提示（§6-D9） |

---

## 3. 误杀威胁模型

| # | 威胁 | 现实场景 | 防御 | 层 |
|---|---|---|---|---|
| T1 | **PID 复用** | 快照后目标退出，pid 被新进程复用 | 信号发出**前**逐 pid 复检 starttime；不符不发 | core/killer |
| T2 | **树向上蔓延** | 向上找父进程 / 用 cgroup 成员 | 只走 `children` 向下；cgroup 不参与选目标 | domain/targets |
| T3 | **目标是守护进程的祖先** | 匹配到启动 Focused 的终端、合成器 | `root ∈ ancestors(self)` → 整体拒绝（`target-is-ancestor`） | domain/safety |
| T4 | **树里混进自己/父进程** | 同上，经子树路径进来 | `tree ∩ {self, ppid, hard_protect}` → 整体拒绝（`self-in-tree`） | domain/safety |
| T5 | **树里有受保护成员** | 应用启动了保护名单里的程序 | 任一成员命中保护名单 → 整体拒绝（`tree-protected`） | domain/safety |
| T6 | **树里有异 uid 成员** | setuid 程序 | 任一成员 uid ≠ 我们 → 整体拒绝（`tree-foreign-uid`） | domain/safety |
| T7 | **规则过宽** | `*`、单字符、超短子串 | 三层拒绝 catch-all；`rule.cmdline-short` | domain/rules |
| T8 | **把父进程当目标** | 用户把 `bash` 加进黑名单 | 保护名单 + 硬护栏 + T3 | domain |
| T9 | **信号发到进程组** | 历史上用过 `killpg` | 禁止 `killpg`（AST 断言）；只按 pid 发 | 全局 |
| T10 | **SIGKILL 无法拦截** | 误杀即永久，无保存机会 | 唯一发信号点 + 全量预检 + 默认 audit + 切换需确认 | 全局 |
| T11 | **扫描中途改规则** | 边扫边改 | 每轮重取 `rules.merged()` | app/scan |
| T12 | **重复终止风暴** | 目标被反复拉起 | 不抑制（决策 1）；`Health.respawn_storm` **只告警** | app/enforcer |
| T13 | **重复记账** | 两棵重叠的树覆盖同一 pid | 单轮局部 `seen`；跨轮聚合到 episode（§9） | app/scan |
| T14 | **目标是系统服务** | 规则命中 `systemd --user`、`dbus-daemon` | T3 结构化拦截 + 保护名单 | domain |

**T14 单独说**：`systemd --user` 是 Focused 的祖先（守护进程由它拉起），所以 T3 会直接拒绝它，
不需要额外名单。系统级误杀由**结构**拦住，而不是靠人维护的名单。

### 3.1 失败方向（fail-closed）

| 情况 | 行为 |
|---|---|
| 预检任一条不过 | 拒绝 + 记 reason，**不发任何信号** |
| 根进程 guard 不符 | 拒绝整个目标（快照已过期） |
| 后代 guard 不符 | 跳过该 pid（已属别人），其余照发 |
| 后代 guard 缺失 | 跳过该 pid（已消失） |
| 信号返回 EPERM | 记 `DENIED`，进 `Health.kill_failures` |
| 进程已消失 | 记 `GONE`，正常结算，不算失败 |

---

## 4. 目标集合：严格向下

```python
# domain/targets.py —— 纯函数
def descendants(root, parents) -> Tuple[int, ...]:
    """root + 全部后代（叶在前）。**永不包含 ppid。**"""

def ancestors(pid, parents) -> frozenset[int]:
    """pid 的祖先链（不含自己），到 1 为止。"""
```

预检里两次等价判断（belt and braces）：

```python
if root in ancestors(self_pid, parents):   # 目标在我们之上（T3）
    deny("target-is-ancestor")
if {self_pid, self_ppid} & set(tree):      # 树里出现了自己或父进程（T4）
    deny("self-in-tree")
```

**为什么不用 cgroup 选目标**：cgroup 成员是共享的。从终端启动浏览器时，终端、shell、浏览器
同在一个 scope；冻结时代为此写了 317 行判定，还踩过真实事故（`app-niri-noctalia-3778.scope`
里住着 shell、Steam、游戏、VPN）。在"杀"的语义下，那 317 行判定本身就是误杀源，因此整块删除。

**代价**：double-fork 到别处的进程（`wineserver`、部分 helper）不在子树里，本轮不杀。这是可接受的方向。

---

## 5. 目标架构

### 5.1 依赖方向（不变）

```
api/      协议、DTO、鉴权
  ↓
app/      用例、唯一状态所有者（stats/episode/health）
  ↓
domain/   纯决策，无 IO
  ↑
ports.py  4 个 Protocol
  ↑
core/     OS 适配器；唯一发信号的地方
```

### 5.2 模块表

| 层 | 模块 | 唯一职责 | 目标行数 |
|---|---|---|---|
| domain | `rules.py` | 是不是目标（已有；`should_kill` 命名已正确） | 388（不变） |
| domain | `targets.py` **新** | `Selection`、`descendants()`、`ancestors()`、`verify_guards()` | ~110 |
| domain | `safety.py` **改** | 预检：T3–T6，fail-closed | ~110 |
| domain | `reasons.py` **新** | 全部 reason code（唯一来源） | ~55 |
| domain | `health.py` **改** | `kill_failures` / `respawn_storm` / `enforcement_enabled` | ~70 |
| app | `scan.py` **改** | 门控 + 遍历 + 调 Enforcer + 采样 + `ScanReport` | ~150 |
| app | `enforcer.py` **新** | 单目标执行 + 上限 + 模式 + episode 聚合 + 审计 | ~170 |
| app | `status.py` / `state.py` / `config.py` **改** | 快照、`recent` 投影、配置事务 | — |
| ports | `ports.py` **新** | `ProcessReader` / `SignalSender` / `Clock` / `Recorder` | ~40 |
| core | `killer.py` **新** | **唯一**发 `SIGKILL`；信号前复检 guard | ~90 |
| core | `proctree.py` **新** | 执行前的活体重读（`/proc/<pid>/task/*/children`） | ~55 |
| core | `procfs.py` / `matching.py` / `protect.py` / `audit.py` | 保留 | — |
| core | `cgroupfs.py` / `freezer.py` | **删除** | −1184 |
| app | `freeze.py` | **删除** | −323 |

**调用深度**：`scan → enforcer → safety → killer → os.kill`，4 跳。domain 纯函数直接调用，不包 service。

### 5.3 命名

动作是 SIGKILL，所以用 `kill`：`core/killer.py` / `Killer.kill()` / `Killed` / `Selection` / `Verdict`。
不用 `terminate`（那是 SIGTERM 的名字），避免下次有人以为这里在发 TERM 而"顺手优化"。

---

## 6. 关键决策

### D1 Killer 无状态、不确认、不复检之后重试

```python
class Result(str, Enum):
    SENT, GONE, REUSED, DENIED

def kill(pid: int, expected_starttime: int) -> Result
def kill_tree(pids, guards) -> Tuple[Result, ...]
```

不等待、不确认、不重试。每个 pid **发信号前**再读一次 starttime（唯一有效的 TOCTOU 收敛点）。
根 `REUSED` → 整棵放弃；后代 `REUSED`/`GONE` → 跳过该 pid。

### D2 IO 边界 4 个窄 Protocol

`Enforcer` 只依赖 `ProcessReader` / `SignalSender` / `Clock` / `Recorder` + 配置值对象，
不再是 `FreezeService` 的 **14 个关键字参数**。4 个 fake 覆盖全部路径。

### D3 一个 `Selection` 贯穿决策、执行与记录

```python
@dataclass(frozen=True)
class Selection:
    proc: ProcessInfo
    pids: Tuple[int, ...]     # 叶→根，只含后代
    guards: Mapping[int, int] # 每个 pid 的 starttime
    rule: str                 # 命中的规则
```

### D4 `enabled` + `mode` 保留两个概念

`enabled` = 功能开不开；`mode` = 记录还是动手。合并会让 UI 一个复选框承担两个语义。

### D5 限流只保留单轮上限

`max_per_scan`（默认 20）按**本轮根进程数**计，超出记 `throttled`。不引入按分钟限流。

### D6 观察性状态，绝不参与决策

`Enforcer` 维护 60 秒窗口的 episode 计数（§9）。**硬规则**：该计数**绝不允许影响是否发信号**。
测试固定它："计数超阈值时行为与计数为 0 时完全一致，只有 health/历史字段变化"。

### D7 迁移红线

1. 旧 `dry_run:false` **绝不**迁移成 `mode:kill`；
2. 删除的字段在旧客户端上降级为空值而不是错误值；
3. 旧 unit 的 `ExecStopPost=--thaw-all` 由 doctor 检测并提示。

### D8 历史按"事件"记录，不按"信号"记录（详见 §9）

同一 name 在 60 秒窗口内只写首条事件 + 一条窗口汇总；重复信号只累加计数。

### D9 切换到 kill 需要显式确认

```http
PATCH /api/v1/config  {"enforcement": {"mode": "kill"}}
```

必须同时带 `{"confirm": "kill"}`，否则 `400`。理由：SIGKILL 不可逆，一次误点的代价是用户丢工作。
配置文件里手写 `"mode": "kill"` 不需要确认（手写就是明确意图），但启动时记一条 warning 日志。

---

## 7. 流水线

```
scan_once（扫描锁，非阻塞）
  active = session.active(); paused = session.paused()
  housekeeping(active, paused)            # 只续租/过期，不再 thaw
  if not active or paused: return ScanReport(reason="break"|"paused"|"no-session")
  if not enforcement.enabled: return ScanReport(reason="disabled")

  mode = enforcement.mode
  rules = rules.merged()                  # 每轮重取
  parents = snapshot.ppid_map()
  killed = denied = throttled = 0
  seen = set()

  for proc in snapshot:
      decision = evaluate(proc, rules, ctx)
      if decision.action != MATCH: continue
      if proc.pid in seen: continue
      if killed >= enforcement.max_per_scan:
          throttled += 1; continue

      tree = live_proctree(proc.pid, parents)
      guards = verify_guards(tree, procfs)
      selection = Selection(proc, tree, guards, decision.rule)
      verdict = check_safety(selection, parents, ctx)
      if not verdict.allow:
          denied += 1; record_denied(selection, verdict.reason); continue

      if mode == "audit":
          record_would_kill(selection); continue

      results = killer.kill_tree(selection)
      seen.update(tree)
      killed += 1
      record_kill(selection, results)      # 按 episode 聚合，见 §9

  sweep_episodes(now)                      # 过期窗口 → 写汇总
  return ScanReport(killed, denied, throttled, mode, by_reason)
```

---

## 8. 相位与执行门控（决策 4）

### 8.1 现有相位模型

- 三种模式：`countdown` / `pomodoro`（work-break 轮转）/ `stopwatch`
- `active()` = 手动会话在 **work** 且未暂停，**或**有外部 claim（插件/扩展心跳）
- **`break` 不算 active**，依据 `engine.active()` docstring 原文：
  *"A pomodoro break is not active — a break means the applications come back."*
- `paused` 独立维度：不结束会话，只放行 N 分钟
- claim 带租约：前端崩溃/被杀 → 租约过期 → 不再 active

### 8.2 期望行为

| 状态 | active | paused | 动作 |
|---|---|---|---|
| 无会话 | false | false | 不杀 |
| **work 进行中** | true | false | **终止** |
| work 中暂停 | true | true | 不杀 |
| **break** | false | false | 不杀 |
| complete | false | false | 不杀 |
| 会话结束 | false | false | 不做任何事 |
| `mode=audit` | true | false | 只记录 |
| `enabled=false` | 任意 | 任意 | 不杀 |

### 8.3 产品文案必须写清楚

| 现象 | 冻结时代 | SIGKILL 时代 |
|---|---|---|
| 进入 work | 应用被挂起，窗口还在 | 应用**立即退出，无保存机会** |
| 进入 break | 应用自动恢复原样 | **不会恢复**，用户自己重开 |
| 暂停 10 分钟 | 应用回来 10 分钟 | 应用**不会自己回来** |
| 会话结束 | 全部恢复 | 无动作；状态已丢失 |

> 「专注结束后，被终止的应用不会自动恢复，未保存的内容会丢失。」

### 8.4 要删的代码（`app/scan.py:_housekeeping`）

```python
if not active:
    if lease_lapsed: thaw_all("lease-expired")
    elif transition == "break": thaw_all("break")
    else: thaw_all("session-ended")
elif paused: thaw_all("paused")
elif rising: thaw_all("session-start")
```

全删。删完 `_housekeeping` 只剩 `note_active` / `expire` / `take_transition`，
`ScanService` 对执行器的依赖归零，构造参数 6 → 4。

### 8.5 三条要保住的行为

1. **break / pause 不杀，也不"补杀"**；恢复 work 后靠下一轮扫描（≤1s）自然开始。
2. **会话开始（rising）不主动批量杀**，让第一轮扫描做。
3. **会话结束不做任何事**，尤其禁止"结束时顺手再杀一遍"。

### 8.6 新增可解释字段

```json
"enforcement": { "mode": "kill", "active": false, "reason": "break", "since": 1730000000.0 }
```

`reason ∈ {work, audit, break, paused, no-session, disabled}`。

---

## 9. 历史、审计与 `recent`

### 9.1 问题：三份历史，且重复终止会把它们全部刷爆

现状有三份"最近发生了什么"：

| 存储 | 容量 | 写入者 | 读者 |
|---|---|---|---|
| `engine._recent` | 8 条 | `FreezeService` | `/status.recent`、`/focus.recent_names`（chillfocus HUD） |
| `EventBus._events` | 200 条 | `_audit` → `_emit` | 插件 `events.recent()`、看板 |
| `audit.jsonl` | 无限 | `_audit` → `AuditLog.write` | 事后排查 |

三份是同一份数据的三种拷贝：`_audit` 已经把一条记录同时写进审计与事件环，`_recent` 是第三份。

**冻结时代这没问题**：一次会话只冻几次。**重复终止让它变成结构性问题**：

| 容量 | 单应用每秒被杀一次时 |
|---|---|
| `_recent` 8 条 | **8 秒**填满，`recent_names` 变成 `["firefox"]×8` |
| 事件环 200 条 | **3.3 分钟**滚完，插件 `events.recent(since=0)` 全是同一应用 |
| `audit.jsonl` | 每秒 1 行 → **~21 MB/天**，且无轮转 |

调大 `maxlen` 治不了：调成 200 只是把"8 秒"变成"3 分钟"。根因是**事件粒度错了**——
"发了一次信号"不等于"发生了一件事"。

### 9.2 方案：历史按事件聚合

**Episode（一次连续终止时段）** 是这里的基本单位：

```python
@dataclass
class Episode:
    name: str
    rule: str
    first_at: float
    last_at: float
    count: int          # 窗口内被终止的次数
    pids: int           # 累计涉及的根进程数
```

规则：

1. 某个 name **第一次**被终止 → 写一条 `kill` 事件（立刻出现在 `recent`）+ 一条审计行；
2. **同一窗口（60s）内**再次被终止 → 只 `count += 1`、更新 `last_at`，**不写事件、不写审计**；
3. 窗口过期（每轮扫描 sweep 一次）→ 写一条 `kill.summary {name, count, duration}` 并从表里移除；
4. 下一次被终止 → 新 episode，重新开始。

**效果**：一个每秒被杀一次的应用，从 86400 条/天降到 **2880 条/天**（每 60 秒 2 条），
且 `recent` 仍然能立刻看到"firefox 开始被终止"，之后看到"firefox ×47"。

### 9.3 `recent` 变成派生视图

删除 `engine._recent`（以及 `FreezeService` 的 `recent` 构造参数——又少一个）：

```
/status.recent          = EventBus 最近 N 条 kill/summary/deny 事件，按 name 聚合
/focus.recent_names[]   = 上面结果的扁平 name 数组（Unity 只能吃扁平字段）
```

wire 形状保持平行基础类型数组（项目既有规矩）：

```
recent_names[]     ["firefox", "steam"]
recent_counts[]    [47, 3]
recent_last_at[]   [1730000000.0, 1730000100.0]
recent_rules[]     ["blacklist.name:firefox", "blacklist.name:steam"]
```

### 9.4 为什么这仍然满足"无状态"

Episode 表是**观察性状态**：不参与"是否发信号"的任何判断（D6 有测试固定）。
它唯一的副作用是减少写入量，以及在窗口结束时补一条汇总。
决策路径仍然是 `snapshot → evaluate → safety → kill`，不读 episode。

### 9.5 与"锁定 bug"的三件套

| 问题 | 工具 |
|---|---|
| 这个 pid 现在会被杀吗？为什么？ | `focused --explain <pid>` / `GET /decisions?pid=N` |
| 这一轮为什么一个都没杀？ | `ScanReport.by_reason`（`/status.scan_report`） |
| 它被杀了多少次、从什么时候开始？ | `kill` / `kill.summary` episode + `/status.recent` |

三者合起来才能回答"为什么我的浏览器每秒死一次"。

---

## 10. 契约变更（直接切换，不做兼容）

本轮只改后端，**不保留旧字段、不留过渡期**。旧客户端（chillfocus / focused-extension）
会读不到字段，需要在下一次前端更新时同步——这是刻意的：保留两套语义会让
"为什么它没被杀"重新变成需要读三个文件的问题。

| 面 | 现在 | 目标 |
|---|---|---|
| 配置段 | `freeze.{enabled,max_per_scan,confirm_timeout_ms,use_units,state_path}` | `enforcement.{enabled,mode,max_per_scan}` |
| 配置项 | `dry_run: bool` | **删除**，由 `enforcement.mode` 取代 |
| `POST /thaw` | 全量解冻 | **删除路由**（404） |
| `POST /scan` | `{ok, frozen}` | `{ok, killed, denied, throttled, mode, gate, by_reason[], by_reason_counts[]}` |
| `GET /focus/frozen` | 冻结清单 | **删除**；`GET /focus/recent` 返回 episode 列表 |
| `/status` | `frozen_count` / `frozen_recorded_count` / `frozen_names` / `frozen_units` / `frozen_total` / `freeze_enabled` / `state_path` / `unified_cgroup2` / `self_cgroup` | `killed_total`、`enforcement{enabled,mode,active,reason}`、`scan_report`、`recent_*` |
| `/focus` | `frozen_*`、`dry_run`、`recent_names[]` | `killed_total`、`enforcement_mode`、`recent_names[]` + `recent_counts[]` + `recent_last_at[]` + `recent_rules[]` |
| 插件 | `CAP_STATE_FROZEN` / `state.frozen()` | `CAP_STATE_HISTORY` / `state.history()` |
| CLI | `--thaw-all` / `--frozen` / `--keep-frozen` / `--dry-run` / `SIGUSR1` | 删除；新增 `--audit`、`--explain`、`--reasons` |
| systemd | `ExecStopPost=--thaw-all` | 删除 |

**注意 `GET /focus/frozen` 不是改名，是消失**：被终止的进程不再存在，
"当前冻结清单"这个概念没有对应物。替代它的是"最近被终止的"（episode 列表）。

---

## 11. 分阶段实施

### P0 — 纯核（~1 天，不接线）

`domain/reasons.py`、`domain/targets.py`、`domain/safety.py`（改写）、`core/killer.py`、
`core/proctree.py`、`ports.py`。
测试：`test_targets.py`（向下性、环、叶序、guard 三态）、`test_safety.py`（T3–T6 逐条）、
`test_killer.py`（SENT/GONE/REUSED/DENIED）。
验收：407+ 全绿；`git diff` 只有新增文件。

### P1 — 并行装配（~1.5 天）

`app/enforcer.py`；`app/scan.py` 增加终止分支，`mode` 默认 `audit`；冻结路径暂留。
验收：现有 e2e 仍绿；"audit 零信号"、"门控零信号"测试。

### P2 — 真杀开关（~1.5 天）

`mode=kill` 打通 + `confirm` 确认；真进程 e2e（探针消失、审计有记录、树杀且父进程存活）；
重复终止 e2e（重启后再死）。
验收：`--once` 杀探针；`audit` 模式下探针必须存活。

### P3 — 删除冻结与 cgroup（~2 天）

删 `core/freezer.py`、`app/freeze.py`、`core/cgroupfs.py`、5 个 thaw 分支、
`recover_frozen`/`close`/`thaw_stale`、`ExecStopPost`、CLI 参数、SIGUSR1；`dry_run` 并入 `mode`。
验收：源码中 `freeze|thaw|frozen|cgroup` 只剩历史文档；净减 ≥1800 行。

### P4 — 契约、历史、文档（~2 天）

episode 聚合 + `recent` 派生；`focused-protocol.md`、`ui.py`、`doctor-focused.sh`、
`known-issues.md`、`packaging/*`。
验收：`/thaw` 410、`/focus/terminated`、`killed_*`、`recent_*` 平行数组；
审计在每秒一次的场景下 ≤ 2880 行/天。

### P5 — 可解释性（~1 天）

`focused --explain <pid>`、`GET /decisions?pid=N`、`focused --reasons`。

---

## 12. 不变量与测试

| # | 不变量 | 层 |
|---|---|---|
| 1 | 树只含后代：目标绝不包含它的父进程 | domain/targets |
| 2 | 目标是守护进程的祖先 → 整体拒绝 | domain/safety |
| 3 | 树里出现自己或父进程 → 整体拒绝 | domain/safety |
| 4 | 树内任一成员命中保护名单 → 整体拒绝 | domain/safety |
| 5 | 树内任一成员异 uid → 整体拒绝 | domain/safety |
| 6 | 无 starttime guard 时不发任何信号 | domain/targets |
| 7 | starttime 不符时不发任何信号（根不符 → 整棵放弃） | core/killer |
| 8 | 代码里不存在 `killpg` | AST 断言 |
| 9 | 进程已消失返回 `GONE`，不算失败 | core/killer |
| 10 | `mode=audit` 时零信号 | app/enforcer |
| 11 | `enabled=false` 时零信号 | app/scan |
| 12 | break / paused / 无会话时零信号 | app/scan |
| 13 | 会话结束不触发任何动作 | app/scan |
| 14 | 同一轮同一 pid 只记录一次 | app/scan |
| 15 | `max_per_scan` 截断并记 `throttled` | app/enforcer |
| 16 | 同一进程连续两轮匹配 → 被终止两次（决策 1） | app/enforcer |
| 17 | 60 秒窗口内重复终止只写一条事件 + 一条汇总 | app/enforcer |
| 18 | episode 计数超阈值时行为不变，只有 health 变化（D6） | app/enforcer |
| 19 | `mode=kill` 缺少 `confirm` → 400 且不生效 | api |
| 20 | `/thaw` 410 且不改变任何状态 | api |

---

## 13. 不做的事

| 不要 | 为什么 |
|---|---|
| 用 cgroup 成员选目标 | 共享 scope 会把终端的 shell 带进来（T2/T8） |
| 向上遍历父进程 | 决策 7 禁止 |
| 发 SIGTERM 再"升级" | 决策 2：不等待、不升级 |
| 保留 `_handled` / 冷却 / 去重 | 决策 1/6：重复杀 |
| 每次信号都写一条审计 | §9：会把审计变成每秒一行的噪音 |
| 调大 `_recent` 的 maxlen | 治标；根因是事件粒度 |
| 建 `BaseEnforcer` 类层次 | 只剩一个动作 |
| 全局 sed `freeze` → `kill` | 掩盖真实变更 |
| 把旧 `dry_run:false` 迁移成 `mode:kill` | 升级即开杀 |
| 手动改 `packaging/arch/src`、`arch/pkg` | `.gitignore` 的构建产物 |

---

## 14. 剩余待确认

1. **`kill.summary` 的窗口取 60 秒**是否合适？（更短 = 更实时，更长 = 更省日志）
2. **客户端同步**：chillfocus 读 `frozen_count`/`recent_names`，focused-extension 读 `/focus`。
   是否同批改？若否需要一版双写。
3. **`confirm: "kill"` 的确认方式**：请求体字段（本文方案），还是必须走 CLI/UI 才能开？
