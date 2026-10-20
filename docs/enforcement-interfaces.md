# 执行模式后端接口契约（enforcement interfaces）

> 状态：**实施中**（分支 `feature/new-work`，P0 已完成）。配套：[enforcement-mode.md](enforcement-mode.md)（架构与阶段）。
> 范围：**只改 focused 后端**；契约直接切换，不保留旧字段。
> 约束：Python ≥ 3.9（`pyproject.toml`）、纯标准库、扁平 DTO、无 asyncio。

本文只回答一个问题：**每个模块有哪些类型、函数与签名，它们的职责边界在哪。**

---

## 0. 定稿

以下参数与命名已确认；本文其余部分若与之冲突，以本节为准。

| 项 | 定稿 |
|---|---|
| Episode 窗口 | **60 秒** |
| 树节点上限 | **512** |
| `respawn_storm` 阈值 | 同名 60 秒内 **> 10 次** |
| CLI | `--audit`（取代 `--dry-run`） |
| `--explain` | **读审计文件回放**，不读实时 `/proc` |

命名修订（代码已按此实现）：

| 旧称 | 定稿 | 原因 |
|---|---|---|
| `domain/safety.py` | `domain/preflight.py` | 旧的 `check_safety` 要活到 P3，避免同名共存；"预检"更准确 |
| `guards` / `guard` | `starttimes` | 直接说明是 start time |
| `Selection.pids` 叶→根 | **根在前**（`pids[0] == root`） | 先验证根，根不符即中止整棵树 |
| `kill_subtree -> Tuple[Result, ...]` | `-> Dict[int, KillResult]` | 顺序不再是隐式契约 |
| `KillResult` | 增加 `UNVERIFIED` | 缺 starttime 时不再与 `GONE` 混用 |

代码风格：**零注释、无文档字符串**。命名与结构承担说明职责。

P0 已完成（`492 passed, 5 skipped`，基线为 407）：`ports.py`、
`domain/{reasons,targets,preflight,history}.py`、`core/{killer,proctree}.py`、
`ProcFS.{supports_live_children,child_pids}`，以及 `test_{targets,preflight,history,killer,proctree}.py`。

---

## 1. 分层与依赖规则

```
api/  ──► app/  ──► domain/          （domain 不 import 任何其他层）
                 ▲
           ports.py（4 个 Protocol）
                 ▲
              core/                  （core 只 import domain + ports 的类型）
```

**一条硬规则**：`domain/` 只能 import `domain/`。

现状违反这条：`domain/safety.py` 目前 `from ..core.procfs import ProcessInfo`、
`from ..core.rules import Decision, EvalContext`。本方案用两个自有的值对象切断它：

- `domain.targets.ProcessFacts` —— 决策需要的那 6 个字段，由 `app` 从 `ProcessInfo` 转换；
- `domain.safety.Verdict` —— 决策结果，不引用 core 的 `Decision`。

`app` 负责把 `core` 的 `ProcessInfo`/`Decision` 翻成 domain 的值。翻译只有一个地方，
在 `app/scan.py` 里，约 8 行。

---

## 2. 文件清单

| 文件 | 动作 | 职责（一句话） | 目标行数 |
|---|---|---|---|
| `ports.py` | 新增 | 4 个 IO 边界 Protocol | 45 |
| `domain/reasons.py` | 新增 | 全部稳定字符串：拒绝原因、门控原因、结果、事件名 | 90 |
| `domain/targets.py` | 新增 | `ProcessFacts`/`Selection`/`GuardResolution` + 纯树算法 | 110 |
| `domain/safety.py` | 改写 | 预检：唯一说"不准杀"的地方 | 120 |
| `domain/history.py` | 新增 | Episode 聚合（观察性，不参与决策） | 90 |
| `domain/health.py` | 改写 | `kill_failures` / `respawn_storm` / `enforcement_enabled` | 70 |
| `core/killer.py` | 新增 | 唯一发 `SIGKILL`；信号前复检 starttime | 90 |
| `core/proctree.py` | 新增 | 执行前重读子树 | 55 |
| `app/enforcer.py` | 新增 | 单目标执行 + 上限 + 模式 + 聚合 + 记录 | 170 |
| `app/scan.py` | 改写 | 门控 + 遍历 + 调 Enforcer + `ScanResult` | 150 |
| `app/status.py` | 改写 | 快照；`recent` 从事件环投影 | 200 |
| `app/config.py` | 改写 | 配置事务（`enforcement` 段 + confirm） | 190 |
| `app/state.py` | 改写 | 插件只读快照 | 45 |
| `config.py` | 改写 | `EnforcementConfig` 取代 `FreezeConfig` | 380 |
| `api/*` | 改 | 路由与 DTO（`/thaw`→410、`/scan` 新字段、confirm） | — |
| `core/freezer.py` | **删除** | — | −867 |
| `app/freeze.py` | **删除** | — | −323 |
| `core/cgroupfs.py` | **删除** | — | −317 |

新增 ≈ 1090 行，删除 ≈ 1507 行，净减约 400 行；`engine.py` 的委托同步瘦身。

---

## 3. 接口契约

### 3.1 `ports.py`

```python
from __future__ import annotations
from typing import Optional, Protocol, Sequence, Tuple
from .domain.reasons import ActionEvent


class Clock(Protocol):
    def now(self) -> float: ...


class ProcessRow(Protocol):
    """一次快照里的一行。ProcessInfo 结构上满足它，无需继承。"""
    pid: int
    ppid: Optional[int]
    uid: Optional[int]
    comm: str
    state: str
    starttime: Optional[int]

    def display_name(self) -> str: ...


class ProcessReader(Protocol):
    def snapshot(self) -> Sequence[ProcessRow]: ...
    def starttime(self, pid: int) -> Optional[int]: ...

    def children(self, pid: int) -> Optional[Tuple[int, ...]]:
        """直接子进程；返回 None 表示本内核不支持（调用方回退到快照）。"""


class SignalSender(Protocol):
    def send(self, pid: int, signum: int) -> None: ...
        # 只在 core/killer.py 里被调用一次；ProcessLookupError 由调用方翻译成 GONE


class Recorder(Protocol):
    def write(self, event: ActionEvent) -> None: ...
```

**为什么是 Protocol 而不是基类**：`ProcessInfo`、`os.kill`、`AuditLog` 都是既有实现，
结构匹配即可，不需要改它们的继承关系。测试用 20 行的 fake 类。

### 3.2 `domain/reasons.py` —— 全部稳定字符串

```python
from enum import Enum


class Gate(str, Enum):
    NO_SESSION = "no-session"
    BREAK = "break"
    PAUSED = "paused"
    DISABLED = "disabled"


class Deny(str, Enum):
    # 硬护栏（与现在一致，字符串不改，便于对比旧日志）
    PID_LE_1 = "hard:pid<=1"
    ZOMBIE = "hard:zombie"
    KERNEL_THREAD = "hard:kernel-thread"
    SELF = "hard:self"
    FOREIGN_UID = "hard:foreign-uid"
    UNKNOWN_DAEMON_UID = "unknown-daemon-uid"
    UNKNOWN_UID = "unknown-uid"
    PROTECTED = "protected"
    # 本方案新增（T3–T7）
    TARGET_IS_ANCESTOR = "target-is-ancestor"
    SELF_IN_TREE = "self-in-tree"
    TREE_PROTECTED = "tree-protected"
    TREE_FOREIGN_UID = "tree-foreign-uid"
    EMPTY_TREE = "empty-tree"
    UNKNOWN_STARTTIME = "unknown-starttime"
    ROOT_REUSED = "root-reused"


class Result(str, Enum):
    SENT = "sent"
    GONE = "gone"
    REUSED = "reused"
    DENIED = "denied"


class Outcome(str, Enum):
    KILLED = "killed"
    AUDIT = "audit"
    DENIED = "denied"


class EventKind(str, Enum):
    GATE = "enforce.gate"
    DENY = "enforce.deny"
    WOULD_KILL = "enforce.would-kill"
    KILL = "enforce.kill"
    KILL_SUMMARY = "enforce.kill.summary"
    SIGNAL_FAILED = "enforce.signal-failed"
    MODE = "enforce.mode"
    SCAN_FAIL = "scan.fail"
    SCAN_DEGRADED = "scan.degraded"


@dataclass(frozen=True)
class ActionEvent:
    """one 审计行 = 一个事件；字段即 `event=... key=value` 的 key。"""
    kind: EventKind
    at: float
    name: str = ""
    pid: int = 0
    pids: Tuple[int, ...] = ()
    rule: str = ""
    reason: str = ""
    count: int = 0
    duration_s: float = 0.0
    mode: str = ""

    def fields(self) -> Dict[str, Any]:
        """展开成 key=value；空值跳过（供 diagnostics.log_event 使用）。"""
```

`Deny` 的字符串**与现有 reason 保持一致**（`hard:zombie` 等），这样旧日志与新日志能直接比。
新增的只有 T3–T7 那几条。

### 3.3 `domain/targets.py`

```python
@dataclass(frozen=True)
class ProcessFacts:
    pid: int
    uid: Optional[int]
    comm: str
    name: str
    is_zombie: bool
    is_kernel_thread: bool


@dataclass(frozen=True)
class GuardResolution:
    ok: bool
    guards: Mapping[int, int]      # pid -> starttime，只含已解析的
    missing: Tuple[int, ...]       # 缺 starttime 的 pid


@dataclass(frozen=True)
class Selection:
    facts: ProcessFacts
    pids: Tuple[int, ...]          # 叶 → 根；pids[-1] == facts.pid
    guards: Mapping[int, int]
    rule: str

    @property
    def root(self) -> int: ...


def descendants(root: int, parents: Mapping[int, Optional[int]]) -> Tuple[int, ...]:
    """root + 全部后代，叶在前。**永不包含 ppid**；环安全；子进程按 pid 排序。"""

def ancestors(pid: int, parents: Mapping[int, Optional[int]]) -> FrozenSet[int]:
    """pid 的祖先链（不含自己），到 1 为止；环安全。"""

def resolve_guards(
    pids: Sequence[int], starttimes: Mapping[int, Optional[int]]
) -> GuardResolution:
    """每个 pid 都必须有 starttime，否则整体不通过（fail-closed）。"""
```

`descendants` 与 `ancestors` 都是纯函数，输入是 `pid -> ppid` 映射，可以被穷举测试。

### 3.4 `domain/safety.py` —— 唯一说"不准杀"的地方

```python
@dataclass(frozen=True)
class SafetyContext:
    self_pid: int
    self_ppid: int
    self_uid: Optional[int]
    hard_protect_pids: FrozenSet[int]
    parents: Mapping[int, Optional[int]]
    uids: Mapping[int, Optional[int]]      # 树内成员的 uid（来自同一快照）
    names: Mapping[int, str]               # 树内成员的显示名
    is_protected: Callable[[str], bool]    # 保护名单匹配（精确/通配/comm 截断）


@dataclass(frozen=True)
class Verdict:
    allow: bool
    reason: Optional[Deny] = None
    detail: str = ""


def check_safety(selection: Selection, ctx: SafetyContext) -> Verdict: ...
```

检查顺序（**固定，且有测试锁住顺序**）：

| # | 检查 | 失败 reason |
|---|---|---|
| 1 | `ctx.self_uid is None` | `unknown-daemon-uid` |
| 2 | `facts.pid <= 1` | `hard:pid<=1` |
| 3 | `facts.pid == self_pid/self_ppid` 或在 `hard_protect_pids` | `hard:self` |
| 4 | `facts.is_zombie` | `hard:zombie` |
| 5 | `facts.is_kernel_thread` | `hard:kernel-thread` |
| 6 | `facts.uid is None` | `unknown-uid` |
| 7 | `facts.uid != self_uid` | `hard:foreign-uid` |
| 8 | `selection.root in ancestors(self_pid, parents)` | `target-is-ancestor` |
| 9 | `{self_pid, self_ppid} & set(pids)` 非空 | `self-in-tree` |
| 10 | `pids` 为空 | `empty-tree` |
| 11 | 树内任一 `uids[pid]` 缺失或 != self_uid | `tree-foreign-uid` |
| 12 | 树内任一 `is_protected(names[pid])` | `tree-protected` |

顺序原则：**便宜的、无依赖的先做；涉及全树扫描的放后面**。1–9 是 O(1)/O(深度)，10–12 是 O(树大小)。

### 3.5 `domain/history.py`

```python
@dataclass
class Episode:
    name: str
    rule: str
    first_at: float
    last_at: float
    count: int
    roots: int


class EpisodeBook:
    def __init__(self, window: float = 60.0) -> None: ...

    def note(self, name: str, rule: str, now: float) -> bool:
        """记录一次终止。返回 True 表示本窗口内首次（调用方据此写事件与审计）。"""

    def sweep(self, now: float) -> Tuple[Episode, ...]:
        """关闭过期窗口，返回需要写 `kill.summary` 的 Episode（count > 1）。"""

    def snapshot(self) -> Tuple[Episode, ...]:
        """当前未关闭的窗口，供 /status 展示。"""
```

### 3.6 `domain/health.py`

```python
@dataclass
class Health:
    scan_failures: int = 0
    last_scan_error: str = ""
    degraded_after: int = 3
    kill_failures: int = 0
    respawn_storm: Tuple[str, ...] = ()
    enforcement_enabled: bool = True

    def record_scan_failure(self, error: str) -> None: ...
    def record_scan_success(self) -> None: ...
    def record_kill_failure(self, name: str) -> None: ...
    def note_respawn(self, name: str, count: int, threshold: int) -> None: ...
    @property
    def degraded(self) -> bool: ...
    @property
    def problems(self) -> Tuple[str, ...]: ...
    def to_dict(self) -> dict: ...
```

`problems` 的值：`scan-degraded` / `enforcement-disabled` / `kill-failures` / `respawn-storm`。

### 3.7 `core/killer.py`

```python
class Killer:
    def __init__(
        self,
        *,
        sender: SignalSender,
        reader: ProcessReader,
        logger: Optional[logging.Logger] = None,
    ) -> None: ...

    def kill(self, pid: int, expected_starttime: int) -> Result: ...

    def kill_tree(self, selection: Selection) -> Tuple[Result, ...]:
        """叶 → 根。根 REUSED/DENIED 时立即停止并返回已发送的部分结果。"""
```

`killer.kill` 的内部顺序（不可调换）：

```
current = reader.starttime(pid)
if current is None:        return GONE
if current != expected:    return REUSED        # 绝不对陌生人发信号
try:    sender.send(pid, SIGKILL); return SENT
except ProcessLookupError: return GONE
except PermissionError:    return DENIED
```

### 3.8 `core/proctree.py`

```python
def live_tree(
    root: int,
    reader: ProcessReader,
    *,
    fallback: Optional[Tuple[int, ...]] = None,
    max_nodes: int = 512,
) -> Tuple[int, ...]:
    """执行前重读子树；`reader.children` 返回 None 时用 fallback（快照树）。
    超过 max_nodes 时截断并返回已收集部分（调用方记 warning）。"""
```

### 3.9 `app/enforcer.py`

```python
@dataclass(frozen=True)
class Outcome:
    status: Outcome           # killed | audit | denied
    reason: Optional[Deny] = None
    results: Tuple[Result, ...] = ()


class Enforcer:
    def __init__(
        self,
        *,
        killer: Killer,
        recorder: Recorder,
        clock: Clock,
        config: EnforcementConfig,
        health: Health,
        history: EpisodeBook,
        stats: MutableMapping[str, int],
        logger: Optional[logging.Logger] = None,
    ) -> None: ...            # 8 个依赖，对比 FreezeService 的 15 个

    def enforce(self, selection: Selection, *, mode: str, now: float) -> Outcome: ...
    def sweep(self, now: float) -> None: ...
```

`enforce` 的完整流程（**这是全系统最值得读的 30 行**）：

```
if mode == "audit":
    recorder.write(ActionEvent(WOULD_KILL, ...))
    return Outcome(AUDIT)

results = killer.kill_tree(selection)
for r in results:
    if r == DENIED: health.record_kill_failure(name)

first = history.note(facts.name, selection.rule, now)
if first:
    recorder.write(ActionEvent(KILL, ...))          # 窗口首条
health.note_respawn(name, history.count(name), RESPAWN_THRESHOLD)
return Outcome(KILLED, results=results)
```

### 3.10 `app/scan.py`

```python
@dataclass(frozen=True)
class ScanResult:
    gate: str = ""                  # "" 表示真的跑了；否则是 Gate 值
    mode: str = "audit"
    killed: int = 0
    denied: int = 0
    throttled: int = 0
    by_reason: Mapping[str, int] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_wire(self) -> Dict[str, Any]: ...


class ScanService:
    def scan_once(self) -> ScanResult: ...
    def scan_ms(self) -> Dict[str, float]: ...
    def run_forever(self, stop_event: threading.Event) -> None: ...
```

**命名冲突修复**：现有 `app/scan.py` 里已经有一个 `ScanReport`（`ScanRequester` 的返回值，
字段 `accepted/frozen/reason`）。本方案把它改名为 `ScanRequestReport`，把 `scan_once` 的
返回值叫 `ScanResult`。两个不同语义共用一个名字是当前的隐性 bug 源。

### 3.11 `app/status.py`

```python
class StatusService:
    def status(self) -> Dict[str, Any]: ...
    def focus_state(self) -> Dict[str, Any]: ...
    def recent(self, limit: int = 8) -> List[Dict[str, Any]]:
        """从事件环投影，按 name 聚合；`_recent` 环形缓冲删除。"""
    def recent_arrays(self, limit: int = 8) -> Dict[str, List[Any]]:
        """recent_names[] / recent_counts[] / recent_last_at[] / recent_rules[]"""
```

### 3.12 API

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/v1/scan` | — | `ScanResult.to_wire()` + `frozen`（旧键，= killed） |
| PATCH | `/api/v1/config` | `{"enforcement":{"mode":"kill"},"confirm":"kill"}` | 缺 `confirm` → `400 {"error":"confirm required"}` |
| POST | `/api/v1/thaw` | — | `410 {"ok":false,"error":"gone","detail":"..."}` |
| GET | `/api/v1/focus/killed` | — | `{ok, count, killed:[...]}` |
| GET | `/api/v1/decisions?pid=N` | — | 决策链（P5） |

---

## 4. 日志规范

### 4.1 形状

沿用 `diagnostics.log_event`：`event=<kind> key=value ...`，值含空格时 JSON 引号。
**一行一个事件，不拼句子，不写多行。**

### 4.2 事件表

| event | 何时写 | 必填字段 | 级别 |
|---|---|---|---|
| `enforce.gate` | 门控**发生变化**时（不是每轮） | `reason` | info |
| `enforce.deny` | 每次拒绝 | `pid name reason rule` | warning |
| `enforce.would-kill` | audit 模式下每次匹配（窗口首条） | `pid name pids rule` | info |
| `enforce.kill` | 窗口内首次真正终止 | `pid name pids rule` | info |
| `enforce.kill.summary` | 窗口关闭且 count>1 | `name count duration_s` | info |
| `enforce.signal-failed` | 某个 pid 返回 DENIED | `pid name result` | error |
| `enforce.mode` | mode 改变 | `mode` | warning |
| `scan.fail` | 扫描抛异常 | `error` | error |
| `scan.degraded` | 连续失败达阈值 | `failures error` | error |

### 4.3 热路径规则

- **不每轮写日志**：`enforce.gate` 只在变化时写一次（记住上一次 reason）；
- **不每个信号写审计**：重复终止只累加计数，窗口关闭写一条汇总（见 heritage §9）；
- `scan.ok` 不写（每轮都有等于噪音），`scan.fail` 才写。

### 4.4 示例

```
event=enforce.gate reason=break
event=enforce.kill pid=4242 name=firefox pids=4242,4243,4244 rule=blacklist.name:firefox
event=enforce.kill.summary name=firefox count=47 duration_s=62.0 rule=blacklist.name:firefox
event=enforce.deny pid=1200 name=bash reason=protected rule=protect.name:bash
event=enforce.deny pid=3300 name=systemd reason=target-is-ancestor rule=blacklist.name:systemd
event=enforce.signal-failed pid=4400 name=steam result=denied
```

### 4.5 审计行

`audit.jsonl` 一行一个 `ActionEvent.to_dict()`，含 `ts/kind/...`。
与日志同源：`Recorder` 的实现同时写审计文件与事件环，两者不可能不一致。

---

## 5. 异常策略

| 层 | 策略 |
|---|---|
| `domain/` | **不抛异常**；返回 `Verdict` / `GuardResolution` / 计数值 |
| `core/` | 把 `OSError` 翻译成 `Result`；不向上抛 |
| `app/` | 捕获一切并转成 `Health`/`ScanResult`；扫描异常不杀守护进程 |
| `api/` | 协议错误翻译成 400/410；领域值不抛 |

---

## 6. 测试接缝（4 个 fake）

| Protocol | fake | 用来测 |
|---|---|---|
| `ProcessReader` | `FakeProcfs(rows, children_map)` | 树、guard、PID 复用 |
| `SignalSender` | `RecordingSender()` | 发了几个、发给谁、顺序 |
| `Clock` | `FakeClock(t)` | 窗口、限流 |
| `Recorder` | `ListRecorder()` | 事件内容与条数 |

`Enforcer` 的构造只需要这 4 个 + config + health + history + stats，**不需要 systemd、cgroup、真进程**。

---

## 7. 一次扫描的调用序列

```
ScanService.scan_once()                       app/scan.py
 ├ session.active() / session.paused()        app/session.py
 ├ _housekeeping(...)                         （只续租/过期）
 ├ [gate 不通过] → ScanResult(gate=...)       ← 只在这一刻写 enforce.gate（若变化）
 ├ procfs.snapshot()                          core/procfs.py
 ├ parents = {pid: ppid}                      app/scan.py
 ├ for row in snapshot:
 │   ├ evaluate(row, rules, ctx)              core/rules.py       → Decision
 │   ├ [非 MATCH] continue
 │   ├ live_tree(pid, reader, fallback)       core/proctree.py    → 树
 │   ├ resolve_guards(树, starttimes)         domain/targets.py   → GuardResolution
 │   ├ check_safety(selection, ctx)           domain/safety.py    → Verdict
 │   ├ [denied] → Enforcer 记 deny            app/enforcer.py
 │   └ enforcer.enforce(selection, mode, now) app/enforcer.py
 │        ├ killer.kill_tree(selection)       core/killer.py      → Result[]
 │        ├ history.note(...)                 domain/history.py
 │        └ recorder.write(ActionEvent(...))  ports.Recorder
 ├ enforcer.sweep(now)                        关闭过期窗口、写 summary
 └ return ScanResult
```

**调试按图索骥**：从 `/status.scan_report.by_reason` 的一个 reason 出发，
按上表往下找到唯一一个产生它的函数。

---

## 8. 现有 → 目标 签名对照

| 现在 | 目标 | 说明 |
|---|---|---|
| `FreezeService(**15 个关键字)` | `Enforcer(**8 个关键字)` | 依赖减半 |
| `freeze_target(proc, parents, starttimes, decision, ctx, now, dry_run) -> bool` | `enforce(selection, *, mode, now) -> Outcome` | 7 位置参数 → 2；bool → 带 reason 的值 |
| `Freezer.freeze(target, reason) -> Optional[Frozen]` | `Killer.kill(pid, expected_starttime) -> Result` | 无状态 |
| `Freezer.thaw(entry, reason) -> bool` | **删除** | 无恢复 |
| `Freezer.thaw_all(reason) -> int` | **删除** | 无恢复 |
| `Freezer.recover() -> int` | **删除** | 无状态文件 |
| `check_safety(proc, decision, ctx, *, plan_mode, plan_reason, plan_pids, guards_known) -> SafetyVerdict` | `check_safety(selection, ctx) -> Verdict` | 去 plan 概念；2 参数 |
| `cgroupfs.plan_for(path, self, dedicated, unified, allowed) -> FreezePlan` | **删除** | cgroup 不选目标 |
| `ScanService.scan_once() -> int` | `scan_once() -> ScanResult` | 从"杀了几个"到"完整报告" |
| `ScanReport`（requester，`accepted/frozen/reason`） | `ScanRequestReport` | 改名消除歧义 |
| `engine.frozen() -> List[Frozen]` | `engine.killed() -> List[Killed]` | — |
| `engine.thaw_all(reason) -> int` | **删除** | — |

---

## 9. 契约：直接切换

**不做兼容层。** 旧字段直接删除，旧客户端读不到就是读不到——保留两套语义
会让"为什么它没被杀"重新变成需要读三个文件的问题。完整对照见
[enforcement-mode.md §10](enforcement-mode.md)。

三个要点：

1. **`GET /focus/frozen` 不是改名，是消失**。被终止的进程不再存在，
   "当前冻结清单"没有对应物；替代物是 `GET /focus/recent`（最近被终止的 episode）。
2. **`dry_run` 概念删除**，由 `enforcement.mode` 取代。CLI 的 `--dry-run` 改为 `--audit`。
3. **`/status` 不再有"当前集合"**，只有 `killed_total`（累计）+ `recent_*`（最近）+ `scan_report`（本轮）。

---

## 10. 逐文件改动清单

### 10.1 新增（6 个源文件 + 5 个测试）

| 文件 | 内容 | 规模 |
|---|---|---|
| `ports.py` | 4 个 Protocol（§3.1） | ~45 |
| `domain/reasons.py` | `Gate`/`Deny`/`Result`/`Outcome`/`EventKind`/`ActionEvent` | ~90 |
| `domain/targets.py` | `ProcessFacts`/`Selection`/`GuardResolution`/`descendants`/`ancestors`/`resolve_guards` | ~110 |
| `domain/history.py` | `Episode`/`EpisodeBook` | ~90 |
| `core/killer.py` | `Killer.kill` / `kill_tree` | ~90 |
| `core/proctree.py` | `live_tree` | ~55 |
| `app/enforcer.py` | `Enforcer.enforce` / `sweep` / `Outcome` | ~170 |
| `tests/test_targets.py` | 向下性、环、叶序、guard 三态 | — |
| `tests/test_killer.py` | SENT/GONE/REUSED/DENIED + 顺序 | — |
| `tests/test_history.py` | 窗口首条/重复/汇总 | — |
| `tests/test_enforcer.py` | audit 零信号、限流、聚合、health | — |
| `tests/test_lifecycle_e2e.py` | 真进程：终止、树杀、重复杀 | — |

### 10.2 改写（按文件给出要改的符号）

| 文件 | 删掉 | 加上 |
|---|---|---|
| `config.py` | `FreezeConfig`、`Config.freeze`、`Config.dry_run`、`state_path` 校验、`use_units`/`confirm_timeout_ms` | `EnforcementConfig(enabled, mode, max_per_scan)`、`Config.enforcement`、`mode ∈ {audit,kill}` 校验、旧 `freeze` 段忽略 + warning |
| `engine.py` | `_freezer`、`_freeze_service`、`_recent`、`_self_cgroup`、`_unified_cgroup2`、`_dry_run`、`_freeze_enabled`；方法 `frozen`/`frozen_count`/`frozen_names`/`frozen_list`/`thaw_all`/`recover_frozen`/`close`/`_thaw_stale`/`_freezable_pids`/`set_dry_run`/`dry_run` | `_killer`、`_enforcer`、`_history`、`killed_total()`、`recent_episodes()`、`set_mode(mode)`、`_set_enforcement_enabled(enabled)`；`scan_once() -> ScanResult` |
| `app/scan.py` | `freezer`/`freeze_service`/`dry_run`/`freeze_enabled` 构造参数；`_housekeeping` 的 5 个 `thaw_all` 分支；`ScanReport`（requester）→ 改名 | `ScanResult`、`scan_once() -> ScanResult`、`ScanRequestReport`、`_housekeeping() -> str`（返回 `Gate` 值） |
| `app/status.py` | `frozen_count`/`frozen_recorded_count`/`frozen_names`/`frozen_units`/`frozen_total`/`freeze_enabled`/`state_path`/`unified_cgroup2`/`self_cgroup`/`dry_run`；`frozen_list()` | `killed_total`、`enforcement{enabled,mode,active,reason,since}`、`scan_report`、`recent()`、`recent_arrays()`、`recent_list()` |
| `app/state.py` | `summary()` 里的 `frozen_count`；`frozen()` | `killed_total`、`scan_report`、`history()` |
| `app/config.py` | `set_dry_run` 分支、`freeze.enabled → thaw_all` 分支 | `enforcement` 段事务、`mode=kill` 需 `confirm` |
| `api/session_routes.py` | `POST /thaw`、响应里的 `dry_run` | `POST /scan` 返回 `ScanResult.to_wire()` |
| `api/read_routes.py` | `GET /focus/frozen` | `GET /focus/recent` |
| `api/core_routes.py` | `/thaw` 路径常量 | — |
| `plugin_api/capabilities.py` | `CAP_STATE_FROZEN` | `CAP_STATE_HISTORY = "state.history"` |
| `plugin_api/namespaces.py` | `StateNamespace.frozen()` | `StateNamespace.history()` |
| `core/procfs.py` | — | `children(pid) -> Optional[Tuple[int, ...]]`（读 `/proc/<pid>/task/<tid>/children`，不支持返回 `None`） |
| `core/rules.py` | 文案里的 "refusing to freeze" | 文案改 "refusing to kill"；逻辑不变 |
| `focusedd.py` | `--thaw-all`、`--frozen`、`--keep-frozen`、`--dry-run`、`SIGUSR1` handler、启动 `recover_frozen`、退出 `thaw_all`、`--once` 的 thaw | `--audit`、`--explain`、`--reasons`；`--once` 打印 `ScanResult` |
| `ui.py` | "已冻结"文案与 `frozen_*` 字段 | "已终止"+ `killed_total` + `recent_*` + `scan_report` |
| `packaging/focused.service` | `ExecStopPost=… --thaw-all` | — |

### 10.3 删除（3 个源文件 + 3 个测试）

| 文件 | 原因 |
|---|---|
| `core/freezer.py`（867） | 状态+信号+systemctl+恢复四合一，全部由 `killer.py` + `enforcer.py` 取代 |
| `app/freeze.py`（323） | 由 `app/enforcer.py` 取代 |
| `core/cgroupfs.py`（317） | cgroup 不再参与选目标 |
| `tests/test_freezer.py`（221 处断言） | 随实现删除 |
| `tests/test_cgroupfs.py` | 同上 |
| `tests/test_freeze_service_edges.py` | 同上，边界已迁到 `test_enforcer.py` / `test_safety.py` |

### 10.4 测试改写面

按现有断言数量排序（`grep -ci "frozen\|freeze\|thaw"`）：

| 文件 | 断言数 | 处理 |
|---|---:|---|
| `test_focused_engine.py` | 47 | 重写为 `killed` / `ScanResult` 断言 |
| `test_rules.py` / `test_matching.py` | 39 / 27 | 多数是注释与文档字符串里的 "freeze"，只改词 |
| `test_frontend_e2e.py` | 12 | 改为"开 kill → 探针消失" |
| `test_plugin_api.py` | 11 | `state.frozen()` → `state.history()` |
| `test_focused_protocol.py` | 8 | 字段名替换 |
| `test_file_security.py` | 6 | 删掉 `frozen.json` 0600 用例（文件不再存在） |
| `test_event_bus.py` / `test_plugin_gateway.py` / `test_cli_help.py` | 5 / 4 / 4 | 字段与参数名替换 |
| `test_health.py` | 3 | `recovery_failures` → `kill_failures` |
| `test_scan_requester.py` / `test_focused_plugins.py` / `test_diagnostics.py` / `test_config_service_edges.py` | 各 1–2 | 词面替换 |
| `test_safety.py` | 1 | 整体重写（T3–T6 逐条） |
| `test_rule_source.py` / `test_plugin_contract.py` | 各 1 | 字段替换 |

---

## 11. 执行顺序与验证

基线：`python3 -m pytest -q` → **407 passed, 5 skipped, 594 subtests**。

### P0 纯核（不改任何现有行为）

写 `ports.py`、`domain/{reasons,targets,safety,history}.py`、`core/{killer,proctree}.py`
及 5 个新测试。**不接线**：没有任何现有文件被修改。

```bash
pytest -q tests/test_targets.py tests/test_killer.py tests/test_history.py tests/test_safety.py
pytest -q          # 407 仍然全绿；git diff 只有新增文件
```

### P1 装配（旧实现变死代码，测试仍全绿）

`config.py` 加 `EnforcementConfig`（默认 `mode=audit`）、`app/enforcer.py`、`app/scan.py` 接 Enforcer。

旧的 `core/freezer.py` / `app/freeze.py` **保留但不被引用**——它们自己的单测
（`test_freezer.py` 等）仍然全绿，只是没人调用它们了。同时改掉断言旧链路的三个测试：
`test_focused_engine.py`、`test_frontend_e2e.py`、`test_focused_protocol.py`。

```bash
pytest -q          # 全绿（用例数与断言会变，因为 scan 不再冻结）
```

### P2 真杀

`mode=kill` 打通 + `confirm`；写 `test_lifecycle_e2e.py`。

```bash
pytest -q tests/test_lifecycle_e2e.py
focused --config /tmp/probe.json --once    # 探针应消失；--audit 时应存活
```

### P3 删除死代码

删 `core/freezer.py`、`app/freeze.py`、`core/cgroupfs.py` 及其三个测试文件；
删 CLI 参数、SIGUSR1、`ExecStopPost`、启动/退出恢复、`_housekeeping` 的 5 个分支；
`dry_run` 并入 `mode`。

```bash
pytest -q
grep -rn "freeze\|thaw\|frozen" focused/ focusedd.py --include=*.py | wc -l   # 期望 0
```

### P4 契约与界面

`app/status.py`、`app/state.py`、`app/config.py`、`api/*`、`plugin_api/*`、`ui.py`、
`doctor-focused.sh`、`focused-protocol.md`。

```bash
pytest -q tests/test_focused_protocol.py tests/test_plugin_contract.py tests/test_frontend_e2e.py
```

### P5 可解释性

`--explain`、`GET /api/v1/decisions?pid=N`、`--reasons`。

```bash
focused --explain 1234     # 打印 evaluate → tree → guards → safety → action
```

---

## 12. 待确认

1. `EpisodeBook.window` 取 60s（更短更实时、更长更省日志）。
2. `max_nodes=512` 的树截断上限（Chromium 系通常 < 100）。
3. `Health.respawn_storm` 阈值：同名 60 秒内 > 5 次，还是 > 20 次？
4. CLI 里 `--dry-run` 改名 `--audit` 是否接受（`--dry-run` 是通用词，但会与 `mode` 概念并存）。
5. `--explain` 读实时 `/proc`（离线可用）还是只读审计（可回放）。
