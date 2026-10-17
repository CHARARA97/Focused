# 架构重构方案 — ChillFocused / Focused

> 状态：**提案，尚未实施**。本文只改文档，不改运行代码；所有实验在 `.dshtmp/`。
>
> 目标不是“把代码拆小”，而是让**原始目标**在架构上成立：
> 专注时冻结干扰应用、结束后恢复、任何异常都不留下永久冻结、并且能解释“为什么冻/为什么没冻”。
>
> 配套文档：[known-issues.md](known-issues.md)（问题清单）、[freeze-mode.md](freeze-mode.md)（安全不变量）、
> [focused-protocol.md](focused-protocol.md)（现有协议）。
> 可运行原型：`.dshtmp/prototype/domain_model.py` + `test_domain_model.py`。

---

## 1. 原始目标与硬约束

重构必须以这些为验收标准，而不是以“模块数量”为标准。

### 1.1 产品目标

| # | 目标 | 验收 |
|---|---|---|
| G1 | 专注期间挂起干扰应用，结束后恢复 | 真实进程 `SIGSTOP` → `SIGCONT` |
| G2 | 绝不留永久冻结 | 崩溃/被杀/租约过期/退出都能恢复 |
| G3 | 绝不误伤桌面、游戏、终端、Wine、自身 | 保护名单只增不减 + 硬护栏 |
| G4 | 后端可独立使用 | CLI / 看板 / API 不依赖游戏 |
| G5 | 前端可选、多来源共存 | 插件、浏览器扩展、第三方互不覆盖 |
| G6 | 可解释、可审计 | 每次冻结/恢复/拒绝都有 reason |
| G7 | 无 root、仅 loopback、后端纯 stdlib | 配置校验 + 打包约束 |

### 1.2 硬约束

- 游戏在 Proton/Wine 里，插件看不到宿主进程 → 冻结必须在宿主侧。
- 插件运行在 Unity Mono / net472 / C# 7.3；不能引用游戏程序集。
- Unity 的 `JsonUtility` 只绑扁平字段/基础数组 → `/focus` 必须扁平。
- 游戏会销毁插件对象；后台线程必须独立于 Unity 生命周期。
- 后端只用 Python 标准库；不引入事件循环/框架。
- 用户可能通过 AUR 安装；文件布局与 systemd 单元是契约的一部分。

---

## 2. 架构诊断：问题的根因

当前代码的 bug 不是零散的，它们来自四个结构性原因。

### 2.1 一个概念有多个表示

| 概念 | 现状 | 后果 |
|---|---|---|
| 配置规则 | `config.rules` 与 `_sources["config"]` 两份 | PATCH 改了内存、`save()` 写旧的（BE-01/02） |
| 会话 | 插件 `SessionGate` 的 bool + 后端 `active` + 手动 session + claims | 语义重复、reason 丢失（X-04） |
| 规则推送 | headless 与 overlay 两条路径 | 重复请求、两套退避（X-02） |
| 连接状态 | overlay 的 `_connected` 与 reporter 的连接 | UI 与事实不一致（PLG-14） |
| 冻结计数 | `frozen_total`（累计）与 `frozen_count`（当前）混用 | HUD 误导（PLG-05） |

**原则：一个概念只能有一个权威表示，其余都是派生视图。**

### 2.2 判定与执行混在一起

- `engine._freeze_target()` 同时做：取 cgroup、判 plan、算树、构建 Target、调 Freezer。
- `Freezer` 同时做：状态文件、策略（backoff/refuse）、信号、systemctl、恢复。
- `Plugin.cs` 同时做：入口、配置、Harmony、线程、runner 生命周期、诊断。

**原则：纯判定（data → decision）与执行（decision → effect）分开；纯函数可被穷举测试。**

### 2.3 状态机是隐式的

- 会话相位是字符串 + 多个 bool；暂停/休息/完成/无信号靠组合推断。
- 冻结生命周期散落在 `_entries`、`_handled`、`_refused`、`refused_fails` 里。
- 插件用 `SessionActive` 静态 bool 表达“当前会话”。

**原则：显式枚举 + 显式转移；每个状态变化有 reason；只有转移才产生事件。**

### 2.4 热路径在做记账，失败路径在沉默

- 每 2 秒心跳：审计 + info 日志 + 注册表落盘（BE-06）。
- 扫描异常：只记 `degraded`，UI/doctor 不读（BE-16）。
- 规则推送失败：阻断心跳（X-03）。
- 配置保存失败：只返回 warning，界面刷新后丢失（BE-25）。

**原则：热路径只做必要工作；失败必须进入统一的 Health 模型，并被 UI/doctor 消费。**

---

## 3. 设计原则

1. **单一权威（Single Source of Truth）**：每个概念只有一个写入口。
2. **纯核 + 命令式外壳**：`domain/` 无 IO、无时钟、无全局状态；适配器做 IO。
3. **显式状态机**：`Phase`、`Claim`、`FreezePlan`、`Health` 都是数据；转移函数是纯函数。
4. **一个所有者**：会话租约、规则推送、冻结记录各有一个 owner。
5. **边界契约**：wire DTO + reason code + contract fixtures；两端测试都读。
6. **失败可见**：任何“看起来正常但没工作”的路径都必须进入 `Health`。
7. **幂等副作用**：续租不写审计；重复 thaw 成功；重复 stop 成功。
8. **可逆优先**：不确定就 thaw；冻结前先证明可撤销。
9. **小模块、单理由变更**：一个文件只对一个角色/原因负责；但不为了“小”而碎片化。
10. **保留已验证的好设计**：租约、状态文件、保护并集、扁平 DTO、e2e 测试。

### 3.1 命名与代码形状

这部分不是风格洁癖，它直接决定“读代码能不能看懂意图”。

| 规则 | 反例（现状） | 正例（目标） |
|---|---|---|
| 名字表达领域，不用 `Manager`/`Helper`/`Utils` | `FocusedEngine` 同时管会话/规则/扫描/插件 | `SessionService`、`RuleService`、`ScanService` |
| 用数据表达状态，不用多个 bool | `working + running + phase(string)` | `TimerSnapshot` + `Phase` 枚举 |
| 决策返回带 reason 的值，不只返回 bool | `_freeze_target(...) -> bool` | `FreezeOutcome(status, plan, reason)` |
| 领域错误是值，不是异常 | `apply_rules` 静默接受 `*` | `RuleProblem(code, detail, pattern)` |
| 异常只在边界 | `freezer.thaw` 吞掉所有 `OSError` | 适配器把 `OSError` 翻译成 `Outcome`，上层决定退避 |
| 注释解释“为什么/反例” | `# increment` | `# 先写记录再报告成功：崩溃后才能恢复` |
| 一个文件一个变更理由 | `engine.py` 1575 行 | `scan.py`、`freeze.py`、`session.py` 各 100–250 行 |
| 字符串不散落 | `"X-ChillFocused-Token"`、`"scan failed"` | `protocol/constants.py`、`Reason.SCAN_FAILED` |
| 热路径不写日志/审计 | 每 2s 心跳写 3 处 | 只在状态转移时写；心跳进 metrics |
| 不隐藏全局可变状态 | `HeadlessRunner.SessionActive` | `StatusStore` 的不可变快照 + 显式 owner |

一个实用标准：**如果一段代码需要读完三个文件才能回答“它为什么这样做”，它就应该被抽成一个有名字的函数/类型。**

### 3.2 关键决策（以及被否决的替代方案）

| # | 决策 | 为什么 | 否决了什么 |
|---|---|---|---|
| D1 | **会话权威留在插件**，后端保存 `SessionIntent` | 只有插件看得见游戏；后端搬过去只会多一份语义 | 后端根据 `active` 猜相位 / 后端读存档 |
| D2 | **纯 domain + 命令式 shell** | 安全规则可穷举测试；IO 失败可隔离 | 在 `engine.py` 里边读 `/proc` 边判定 |
| D3 | **每个概念一个 writer** | 双表示是 BE-01/02、X-02 的根因 | “两边都写、最后合并” |
| D4 | **规则来源显式化**，catch-all 在 domain/API/config 三层拒绝 | `*` 是唯一能一次性冻住大量进程的输入 | 只靠文档警告 / 只靠插件提示 |
| D5 | **Freezer 拆成 store / suspender / recovery** | 状态文件、信号、systemctl、恢复是四种失败模式 | 继续让一个 692 行类承担全部 |
| D6 | **Health 是一等公民** | “看起来正常但没工作”是本项目最危险的失败 | 只写日志 / 只返回 `degraded` 字段没人读 |
| D7 | **UI 只读不可变 snapshot + 命令队列** | 消除 overlay 与 reporter 的双 writer | 让设置窗直接发心跳/规则 |
| D8 | **两端共享 contract fixtures** | 头名/字段/会话语义不能单边漂移（X-01 的教训） | 两边各自写 JSON 断言 |
| D9 | **保留 stdlib / net472 / 扁平 DTO** | 这是安装与兼容的硬约束 | 引入 asyncio、JSON 库、嵌套 DTO |
| D10 | **先修 P0/P1，再动结构** | 结构重构不能顺手改变行为；否则无法判断回归来源 | 一次性大重写 |

---

## 4. 后端目标架构

### 4.1 依赖方向

```
api/ (HTTP, CLI, dashboard)  ──►  app/ (use cases)  ──►  domain/ (pure)
                                      │                     ▲
                                      ▼                     │
                                 ports/ (Protocol) ─────────┘
                                      ▲
                                      │
                                 adapters/ (procfs, cgroup, systemd, JSON, audit)

composition.py 负责把 adapters 注入 app，把 app 注入 api。
domain/ 不 import 任何其他层。
```

### 4.2 目录建议

```
focused/
  focused/
    domain/
      session.py          # Phase/Mode/SessionIntent/SessionPolicy/decide_intent
      claims.py           # Claim/ClaimBook（租约、过期、幂等续租）
      rules.py            # RuleSet/Decision/evaluate/merge/validate
      freeze_policy.py    # FreezePlan/plan_freeze（纯决策）
      health.py           # Health/Degraded/Reason
      events.py           # DomainEvent（kind/reason/severity）
    ports.py              # 所有 IO 的 Protocol（一个文件即可，避免过度拆分）
    adapters/
      procfs.py
      cgroupfs.py
      pid_suspender.py
      unit_suspender.py
      composite_suspender.py
      json_frozen_store.py
      json_config_store.py
      json_plugin_registry.py
      jsonl_audit.py
      webhook_notifier.py
      system_clock.py
    app/
      scan.py             # ScanService
      freeze.py           # FreezeCoordinator
      session.py          # SessionService（手动会话 + claims + pause）
      rules.py            # RuleService（来源合并 + 校验 + 持久化协调）
      recovery.py         # RecoveryService
      plugins.py          # PluginService
      health.py           # HealthService
      events.py           # EventBus（审计/事件/webhook 分流）
    api/
      auth.py             # token、Host/Origin 校验
      dto.py              # 扁平 wire DTO
      routes.py           # 路由 → app 服务
      dashboard.py        # 静态 UI 托管
    protocol/
      constants.py        # header/path/reason code
      schema.json         # 可选：机器可读契约
    ui/
      index.html
      app.js
      style.css
    config.py             # 配置模型 + typed readers + 校验 + 0600 保存
    composition.py        # build_app(config) -> App
  focusedd.py             # 只做 CLI 解析与生命周期
```

**文件数量不是目标**：`ports.py` 一个文件放 6-8 个 Protocol，避免 `ports/` 下十几个空壳；`app/` 每个服务 100–250 行。

### 4.3 领域模型（已在 `.dshtmp/prototype/` 验证）

```python
# domain/session.py
class Phase(str, Enum): WORK, BREAK, COMPLETE, PAUSED, IDLE, UNKNOWN
class TimerMode(str, Enum): POMODORO, COUNTUP, UNKNOWN
@dataclass(frozen=True)
class TimerSnapshot: known, working, running, phase, mode, read_at
@dataclass(frozen=True)
class SessionIntent: active, phase, reason, observed_at
@dataclass(frozen=True)
class SessionPolicy: enabled, always_on, fresh_seconds, countup_is_active
def decide_intent(snapshot, now, policy) -> SessionIntent: ...
```

关键点：

- `countup_is_active` 是**显式策略**，不是散落在 `if` 里的隐含行为（解决 X-04）。
- `reason` 是稳定字符串枚举，进入 `/status`、HUD、审计和日志。
- `observed_at` 与 `read_at` 使用同一个单调时钟（解决 PLG-06）。

```python
# domain/claims.py
@dataclass(frozen=True)
class Claim: source, until, ttl, intent
class ClaimBook:
    def claim(...) -> bool   # True 仅当 active/phase/reason 真正变化
    def withdraw(...) -> bool
    def expire(now) -> list[str]
    def live(now) -> dict
```

关键点：续租不产生审计；只有转移（新增/相位变化/取消/过期）才是事件（解决 BE-06）。

```python
# domain/freeze_policy.py
@dataclass(frozen=True)
class FreezePlan: mode, reason, unit, pids
def plan_freeze(*, pid, tree, cgroup, unit, self_cgroup,
                unified, unit_is_dedicated, use_units) -> FreezePlan: ...
```

关键点：`unit_is_dedicated` 由适配器算好传入，纯函数只做安全判定；`slice`/`user@`/`self-cgroup` 的拒绝可以穷举测试。

### 4.4 应用服务

| 服务 | 职责 | 不做什么 |
|---|---|---|
| `SessionService` | 手动会话（countdown/pomodoro/stopwatch）、`ClaimBook`、pause、`active()`、`intent_by_source()` | 不扫描、不冻结 |
| `RuleService` | 来源规则、合并、校验（catch-all/shadowed）、持久化协调 | 不读 `/proc` |
| `ScanService` | 一次扫描：快照 → evaluate → 调 `FreezeCoordinator`；更新 `Health` | 不拥有会话状态机 |
| `FreezeCoordinator` | plan → suspend → record → emit；处理部分失败与回滚 | 不做规则判定 |
| `RecoveryService` | 启动恢复、`--thaw-all`、失败报告 | 不依赖运行中的 app |
| `PluginService` | 注册/禁用/移除/webhook/进程内插件 | 不直接改规则存储（委托 RuleService） |
| `HealthService` | 聚合 scan/recovery/freeze/token/cgroup 状态 | 不做业务决策 |
| `EventBus` | 动作事件 → audit + event stream + webhook；心跳 → metrics | 不在热路径写审计 |

**Freezer 拆分**（当前 692 行 → 四个小件）：

```
FrozenStore        load/save/list（frozen.json，0600/原子写）
PidSuspender       SIGSTOP/SIGCONT + starttime guard
UnitSuspender      probe → systemctl freeze → cgroup.freeze → confirm；
                   thaw → confirm frozen=0；失败退避
FreezeCoordinator  plan → suspend → record → emit
RecoveryService    load → thaw → 报告未恢复项
```

`UnitSuspender` 的“解冻确认”补上 BE-10；`PidSuspender` 只负责信号；`FrozenStore` 不再和信号混在一个类里。

### 4.5 关键流程

**会话 claim（修复 X-02/X-03 后的插件侧）**

```
reporter 每 2s:
  snapshot = game_source.snapshot()
  intent   = decide_intent(snapshot, clock.now(), policy)
  backend  = client.get_focus()            # 可选；失败不阻断
  client.post_state(intent, ttl=30)        # 永远先发
  rules.publish_if_dirty(backend.pid)      # 独立 try/catch，失败只记 warning
```

后端：

```
POST /focus/state {active, phase, reason, mode, ttl} 
  → SessionService.claim(source, intent, ttl)
  → ClaimBook.claim 返回 changed
  → 只有 changed 才 audit/log
  → active() = manual_session.active ∨ any(claim.active)
```

**一次扫描**

```
ScanService.scan_once():
  with scan_lock:                    # 竞争时返回 busy，不静默 0
    activity = session.active(); paused = session.paused()
    Recovery/Transition: 结束/休息/放行 → coordinator.thaw_all(reason)
    if not activity or paused: return report
    if not freeze_enabled: coordinator.thaw_all("freeze-disabled"); return
    snapshot = processes.snapshot()
    for proc in snapshot:
      decision = evaluate(proc, rules.merged, ctx)
      if decision.match:
        plan = plan_freeze(...)      # 纯
        outcome = coordinator.freeze(proc, plan, decision.rule)
    health.record_scan(...)          # 成功/失败/降级
```

**配置更新（修复 BE-01/02）**

```
RuleService.update_config_source(names, cmdline, protect):
  next = RuleSet(...)
  problems = validate(next)            # catch-all、shadowed、guard
  if problems: raise RuleRejected(problems)
  sources["config"] = next             # 运行时唯一写入口
  config_store.save(config, rules=next) # 同一个 next 写盘（0600、原子写）
  merged = recompute()
  coordinator.thaw_stale(merged)
```

> 关键：**不要让 `Config` 和 `RuleService` 各存一份规则**。`Config` 只在加载时提供初始规则；
> 之后 `RuleService` 是唯一权威，`ConfigStore.save` 的 `rules` 参数就是落盘内容。
> 这样不存在“界面显示已保存、文件没写”的中间态，也不需要“同步两份副本”。

**恢复**

```
启动：RecoveryService.recover() → FrozenStore.load() → 逐个 thaw
      → 失败项保留并报告 → Health.recovery_failures
退出：focusedd finally → RecoveryService.thaw_all("shutdown")
崩溃：systemd ExecStopPost → focused --thaw-all（不依赖 app）
信号：SIGUSR1 → RecoveryService.thaw_all("signal")
```

### 4.6 可观测性

统一 `Health` 模型：

```python
@dataclass(frozen=True)
class Health:
    scan_failures: int
    last_scan_error: str
    degraded_after: int = 3
    freeze_enabled: bool = True
    recovery_failures: tuple[str, ...] = ()
    @property degraded -> bool
    @property problems -> tuple[str, ...]
```

- `/status` 返回 `health`（兼容字段 `degraded` 保留）。
- 看板顶栏红色横幅 + “为什么”文案。
- `focused-doctor` 直接查 `health`，不再 grep 日志。
- 每条拒绝（shared-unit/self-cgroup/slice/not-cgroup-v2）都有 reason，进入事件流。

### 4.7 API / 协议

- `protocol/constants.py`：`TOKEN_HEADER = "X-Focused-Token"`、路径、reason code。server 与测试都 import 它。
- `api/dto.py`：扁平 DTO；`focus_payload()` 只做字段映射。
- `api/auth.py`：token + `Host` 校验 + 可选 `Origin` 校验；`GET /` 策略明确（见开放问题）。
- 旧 `/focus/*` 与新 `/plugins/{id}/*` 共用 `SessionClaimIn` / `RuleSetIn` DTO，避免两套语义。

### 4.8 看板

- `ui/` 放静态文件，用 `importlib.resources` 读取；`ui.py` 的 1000 行字符串删除。
- `api()` 统一处理 token（sessionStorage）、401、错误提示。
- 顶栏显示 `Health`；应用名单/设置页的错误不再被 `refreshAll` 覆盖。
- 无构建步骤、无外部资源，保持原目标。

### 4.9 可调试性（设计的一部分，不是事后补日志）

“为什么我的浏览器没被冻结？”必须能用一条命令回答。目标架构提供三层：

1. **决策链可查询**：`focused --explain <pid>`（离线，不依赖运行中的实例）打印：
   ```
   pid=4242 comm=firefox
   hard rails:      pass
   protect list:    no match
   blacklist:       match rule=blacklist.name:firefox
   process tree:    4242,4243,4244
   cgroup plan:     unit=app-firefox-7.scope dedicated=true self-cgroup=false
   mode:            unit (use_units=true)
   action:          freeze
   ```
   同样的信息通过只读 `GET /api/v1/decisions?pid=N` 暴露。
2. **扫描报告可解释**：`ScanService.scan_once()` 返回 `ScanReport`，包含 `evaluated / matched / protected / frozen / skipped_by_reason`。拒绝原因按 `Reason` 计数（`shared-unit`、`self-cgroup`、`slice`、`not-cgroup-v2`），不再只有“冻了 0 个”。
3. **健康状态可消费**：`Health` 同时出现在 `/status`、看板、doctor；任何降级/恢复失败都有 reason 和下一步命令。

配套规则：

- 每条 `Decision`/`FreezePlan`/`Outcome` 都有 `reason` 字段；日志只打印这些结构化字段，不拼接句子。
- `audit.jsonl` 只记动作（freeze/thaw/session/rules/config/recovery），不记心跳；需要心跳统计时用 `stats`。
- 插件侧同样提供 `--explain` 的等价物：HUD 的 Detailed 模式显示 `phase/reason/freshness`，设置窗显示最近一次心跳的 intent。

### 4.10 安全边界

loopback 不是用户隔离：同一台机器上的任何本地进程都能连 `127.0.0.1:8766`。因此：

| 边界 | 目标做法 |
|---|---|
| 浏览器 CSRF / DNS rebinding | 校验 `Host` 必须是 loopback；要求自定义头（触发 preflight）或校验 `Origin`；`_read_json` 只在 `Content-Type: application/json` 时解析 |
| 多用户机器 | token 作为唯一隔离手段；首次运行生成 token 并把 0600 写在 `config.json`，插件首次启动需要用户填入（或提供 `--print-token`） |
| 插件冒充 | 列表接口 redact token；token 只在注册响应出现；HTTP 注册禁止 `kind=python` |
| 文件泄露 | `config.json`、`plugins.json`、`frozen.json` 全部 0600；审计已经是 0600 |
| 外部回调 | webhook 默认只允许 loopback/显式 allowlist；至少拒绝 `file://` 与非 http(s) |
| 误操作 | `names=["*"]` 三层拒绝；`--thaw-all`/SIGUSR1 永远可用 |

> 如果决定保持“默认无 token、单用户”的现状，必须在文档与 doctor 里明确说明“任何本地进程都能控制 Focused”，并把 Host/Origin 校验作为必做项。

---

## 5. 插件目标架构

### 5.1 依赖方向

```
PluginHost (composition root)
   ├─ Platform/*   （Unity/BepInEx/Harmony/HTTP/反射）
   ├─ App/*        （SessionReporter、RulePublisher、StatusStore、Commands）
   ├─ Domain/*     （SessionGate、RuleSet、Lease、Intent —— 无 Unity）
   └─ Ui/*         （HUD、设置窗、选择器 —— 只读 StatusStore）
```

`Domain` 与 `App` 不 `using UnityEngine`；测试项目直接编译它们，谁引入 Unity 谁编译失败（保留现有做法）。

### 5.2 目录建议

```
chillfocus/ChillFocused/
  PluginHost.cs              # 入口 + 组合根 + Harmony 安装
  Domain/
    SessionPhase.cs
    SessionIntent.cs
    SessionGate.cs           # 纯判定：snapshot + policy -> intent
    RuleSet.cs
    Lease.cs
  Ports/
    IGameStateSource.cs
    IGameClock.cs
    IBackendClient.cs
    IConfigStore.cs
    ILog.cs
  App/
    AppHost.cs               # 进程级单例（明确生命周期）
    SessionReporter.cs       # 唯一心跳/规则所有者
    RulePublisher.cs         # 单一推送路径 + 退避 + pid 变化检测
    StatusStore.cs           # 不可变 snapshot，Volatile 交换
    Commands.cs              # thaw/scan/dry-run/follow-timer 队列
  Platform/
    GameTimerProbe.cs        # Harmony/反射 -> TimerSnapshot
    GameServices.cs
    UnityFrameSource.cs      # FrameHook/Update/sceneLoaded -> Tick
    BepInExConfigStore.cs
    HttpTransport.cs
    WireJson.cs              # 唯一序列化
  Ui/
    OverlayController.cs
    SettingsWindow.cs
    AppPicker.cs
    AdvancedPane.cs
    HudRenderer.cs
    PanelSpec.cs / PanelText.cs / ...
  Diagnostics/
    InstallCheck.cs
  ChillFocused.Tests/
    Domain/
    App/                     # 假 ports：顺序、幂等、失败隔离
    Contract/                # 共享 fixtures
```

### 5.3 单一所有者与线程模型

```
Unity 主线程                         reporter 线程
  Overlay.OnGUI                       loop:
    read StatusStore.Snapshot()         snapshot = game_source.Snapshot()
    enqueue Commands                    intent = SessionGate.Decide(...)
                                        status = client.GetFocus()
                                        client.PostState(intent)      # 先发
                                        publisher.PublishIfDue()      # 后发，失败隔离
                                        StatusStore.Publish(...)
                                        wait 2s or wake
```

规则：

- **只有 reporter 发 `POST /focus/state`**；overlay 不再发心跳/规则。
- **只有 reporter 发 `PUT rules`**；`RulePublisher` 是它的组件，不是第二个 runner。
- Unity 主线程只读 `StatusStore` 和写命令队列；不直接 HTTP（除紧急 thaw 可走独立 lane，需明确）。
- `AppHost` 是进程级单例；`PluginHost.OnDestroy` 不停止 reporter（游戏会销毁对象），只在进程退出时停。
- 去掉 `HeadlessRunner.SessionActive` 静态 bool；状态在 `StatusStore` 的不可变 snapshot 里。

### 5.4 生命周期

```
PluginHost.Awake
  config = ConfigStore.Load()
  app = AppHost.CurrentOrCreate(config)   // 启动 reporter
  app.Start()
  InstallFrameHook / GameProbe / GameTimerProbe
  EnsureOverlay()                          // 可能被游戏销毁
PluginHost.OnDestroy
  DestroyOverlay()
  // AppHost 继续运行：它持有租约，必须活到进程结束
```

`EnsureOverlay` 由 `SceneManager.sceneLoaded` 与 reporter 的 `OnCycle` 触发；overlay 重建后从 `StatusStore` 立即拿到当前状态，不需要等下一次心跳。

### 5.5 会话判定（显式策略）

`SessionGate.Decide(policy, snapshot, now) -> SessionIntent`：

- `enabled=false` → 释放；
- `always_on` → 生效（不需要游戏）；
- `known=false` → 释放（fail-open）；
- `mode=countup` → 由 `countup_is_active` 决定（显式产品决策）；
- `break/complete/paused` → 释放；
- `work + running + fresh` → 生效；过期 → 释放并给 `stale` reason。

`GameTimerProbe` 增加 `TimerMode` 读取（`TimerMode()` 反射），读不到就 `UNKNOWN`，由策略决定保守行为。**不要在 `SessionGate` 里猜**。

### 5.6 配置与 UI

- `ConfigStore` 监听 mtime（保留现有热重载），返回不可变 `PluginConfig`；校验：loopback URL、catch-all、间隔下限。
- 设置窗通过 `IConfigStore` 写回 `.cfg`；reporter 下一轮看到变化 → `RulePublisher.MarkDirty()`。
- HUD 使用 `StatusStore`：显示 `Degraded`、当前 phase/reason、**当前**冻结数（不是累计）。
- AppPicker 通过 reporter 的查询 lane 拉 `/processes`，结果进 `StatusStore`；不直接开第二个 client。
- 文案/布局热重载保留（PanelSpec/PanelText），但移到 `Ui/` 下，避免与 App 混在一起。

---

## 6. 两端契约

### 6.1 共享 fixtures（解决 X-06）

```
contract/
  session-cases.json     # TimerSnapshot + policy -> expected SessionIntent
  focus-payload.json     # /focus 的字段集合（扁平）
  headers.json           # {"token": "X-Focused-Token"}
  reason-codes.json      # 两端共用的 reason 枚举
  error-codes.json       # 400/401/409 的含义
```

- C# `DomainTests` 与 Python `test_contract.py` 读同一份 JSON。
- 任何一端改字段/语义，另一端测试先红。
- `docs/focused-protocol.md` 从 fixtures 生成或校验（至少校验头名/字段）。

### 6.2 会话上报格式（向后兼容）

```json
POST /api/v1/focus/state
{
  "active": true,
  "phase": "work",
  "reason": "work-running",
  "mode": "pomodoro",
  "observed_at": 12345.6,
  "ttl_seconds": 30,
  "source": "chillfocused"
}
```

旧插件只发 `active/dry_run/source/ttl` 仍然工作（字段缺失 → `UNKNOWN`）。新后端把 `phase/reason/mode` 存进 claim，`/status` 与看板可以解释“谁在专注、为什么”。

### 6.3 规则来源

- `RuleSetIn` 增加可选 `source`；缺省 `legacy`。
- 插件只保留一个 `RulePublisher`；未来若使用 `/plugins/{id}/rules`，`source` 由 URL 决定。
- 后端在 `RuleService.apply` 统一校验 catch-all/shadowed/guard，不再依赖调用方自觉。

### 6.4 兼容与打包

| 项 | 规则 |
|---|---|
| `frozen.json` | 保持 `version` 字段；只加字段、不改语义；旧记录没有 `guards` 时按 pid+starttime 回退（现状已有） |
| `config.json` | 旧键继续接受；新增键有默认值；不重命名 `1. General` 等 BepInEx section（section 是配置标识符） |
| `/focus` | 旧字段全部保留；`phase/reason/mode` 是新增可选字段；Unity DTO 只加扁平字段 |
| 插件 DLL | 新后端接受只发 `active` 的旧插件；新插件对旧后端忽略未知响应字段 |
| 后端 UI | 若改成 `ui/` 静态文件，必须同步 `pyproject.toml` 的 package data、AUR PKGBUILD、`install-focused.sh` |
| 插件新目录 | `Domain/`、`App/`、`Platform/`、`Ui/` 需要更新 `ChillFocused.csproj` 与 `ChillFocused.Tests.csproj` 的 `<Compile Include>`；纯逻辑文件继续编进测试项目 |
| 测试 fixtures | C# 测试项目需要把 `contract/*.json` 复制到输出目录，Python 测试直接用相对路径 |

---

## 7. 测试策略与安全不变量

### 7.1 金字塔

| 层 | 内容 | 速度 |
|---|---|---|
| domain | 纯函数穷举：session、claims、rules、plan、health | 毫秒 |
| app | 假 ports：扫描顺序、失败隔离、持久化、幂等 | 毫秒 |
| adapters | 临时目录/假 `/proc`/假 `systemctl` | 毫秒 |
| api | HTTP 冒烟、鉴权、Host/Origin、错误码 | 秒 |
| e2e | 真 `focusedd` + 真进程 freeze/thaw/崩溃恢复 | 10–30s |
| contract | 两端共享 fixtures | 毫秒 |

### 7.2 必须固化的不变量

1. 无法撤销的冻结绝不开始（探测先于冻结）。
2. 共享单元绝不整冻；`slice`/`user@`/self-cgroup 绝不整冻。
3. 解冻失败过的单元永不立即重试（指数退避）。
4. 已消失的目标视为已了结。
5. 启动恢复必须报告未恢复项。
6. 冻结记录先落盘，再报告成功。
7. `names=["*"]` 在配置/API/domain 三处都被拒绝。
8. 配置修改 PATCH 后 `Config.load()` 仍在。
9. 心跳续租不产生审计/注册表写盘。
10. 规则推送失败不影响心跳。
11. 插件对象被销毁后，会话仍由 reporter 续租。
12. `degraded` 必须出现在 `/status`、看板、doctor 三处。

每条不变量对应一个测试；测试名就是不变量的句子。

---

## 8. 迁移路线（每一步都可发布）

> 不要一次性重写。先修“会丢数据/会误冻”的 bug，再抽纯核，再拆服务，最后动插件 UI。

### Phase 0 — 安全与数据修复（不改变结构）

1. X-01 token 头 + 契约测试。
2. BE-01/02 配置持久化 + reload 测试。
3. BE-03 catch-all 拒绝（domain/API/config 三处）。
4. BE-05 关闭 freeze 即解冻。
5. X-03 心跳与规则推送解耦。
6. BE-06 心跳记账幂等化。
7. BE-04 看板 token 支持。
8. 每项配回归测试；发 0.1.1。

**退出标准**：known-issues 的 P0/P1 全部 `fixed`；e2e 全绿。

### Phase 1 — 后端抽取纯 domain（无行为变化）

1. 新建 `domain/`，把 `SessionGate` 等价逻辑、`ClaimBook`、`RuleSet`/`evaluate`、`plan_freeze`、`Health` 放进去。
2. `FocusedEngine` 暂时保留为 facade，委托 domain。
3. 原有测试全部保留；新增 domain 测试。

**退出标准**：`domain/` 无 IO；旧测试不改语义全绿。

### Phase 2 — 后端拆应用服务与适配器

1. `ports.py` + `adapters/`：procfs、cgroup、suspender、store、audit、clock。
2. `ScanService`、`FreezeCoordinator`、`SessionService`、`RuleService`、`RecoveryService`、`PluginService`、`HealthService`。
3. `Freezer` 拆成 `PidSuspender`/`UnitSuspender`/`FrozenStore`/`RecoveryService`；补 thaw 确认。
4. `FocusedEngine` 删除或缩成 composition；更新测试。
5. `HealthService` 接进 `/status`。

**退出标准**：每个服务可单独测试；`engine.py` 不再是 1500 行。

### Phase 3 — API / 协议 / 看板

1. `protocol/constants.py` 统一 header/path/reason；server/测试共用。
2. `api/` 拆路由、鉴权、DTO；Host/Origin 校验。
3. 静态 `ui/` 文件；token 支持；Health 横幅。
4. `focusedd` 只做 CLI；`--once` 语义明确。
5. 文档与 fixtures 对齐。

**退出标准**：看板在 token 模式下可用；`doctor` 查 Health；协议测试读 fixtures。

### Phase 4 — 插件抽 domain + 单一 reporter

1. `Domain/SessionGate` 返回 `SessionIntent`；加 `TimerMode` 与 `countup_is_active`。
2. `App/SessionReporter` 成为唯一心跳/规则所有者；删除 overlay 的 `RuleSync` 推送。
3. `StatusStore` 替换 `HeadlessRunner.SessionActive` 静态 bool；overlay 只读。
4. 规则失败不影响心跳；命令队列统一。
5. 补 App 测试：顺序、幂等、失败隔离、对象销毁后继续。

**退出标准**：只存在一条规则推送/心跳路径；插件对象销毁后会话不丢。

### Phase 5 — 插件 ports/platform/ui 整理

1. `Ports/` + `Platform/` 适配器；Domain/App 不依赖 Unity。
2. `Ui/` 只读 StatusStore；AppPicker 走查询 lane。
3. 配置校验（loopback、catch-all、下限）。
4. 保留 PanelSpec/PanelText 热重载与语言跟随。
5. 补 headless 生存测试、contract 测试。

### Phase 6 — 产品功能

1. Countup 策略（产品决定后写进 `SessionPolicy`，两端 fixture 固化）。
2. “防止关闭游戏/停止专注”（如果仍是目标）：独立 `QuitGate`/`TimerLock`，保留 thaw-all/SIGUSR1 逃生口。
3. HUD 显示 `degraded`/reason。
4. 可选：把插件注册为 HTTP 插件，获得 per-plugin token/可见性。

---

## 9. 取舍：不要做什么

| 不要 | 为什么 |
|---|---|
| 把会话判定搬到后端 | 后端看不见游戏；只会多一份语义。插件是权威，后端存 `intent`。 |
| 引入 asyncio/框架/依赖 | 后端纯 stdlib、插件 net472/C# 7.3 是硬约束；收益不抵风险。 |
| 为每个函数建一个 interface | 只对 IO 边界建 port；纯函数直接调用。 |
| 把 UI 改成 React/Vue | 静态 HTML+JS 已满足“无构建、跨桌面、SSH 可用”。 |
| 把 `frozen.json` 格式推倒重来 | 崩溃恢复依赖它；加字段可以，改语义要迁移。 |
| 让插件直接操作 `/proc`/cgroup | Wine 世界看不到宿主；越权且不可移植。 |
| 让看板直接发规则/心跳 | 两个 writer 必然冲突；看板只发命令。 |
| 用静态 bool 表达会话 | 对象会被销毁；用不可变 StatusSnapshot + 显式 owner。 |

---

## 10. 开放问题（需要产品/使用方式决定）

1. **Countup 是否拦截？** 建议默认不拦（无自然终点，锁住会永久困住用户），但必须写成 `countup_is_active=false` 并有测试；如果用户明确要“正向计时也锁”，改成 `true`，两端 fixture 一起改。
2. **看板 token 模式**：登录框 vs `GET /` 豁免 vs 只允许 CLI 配 token？建议看板加 token 输入（sessionStorage），既不锁死也不削弱 API。
3. **插件是否注册为 HTTP 插件？** 好处：独立 token、来源可见、禁用即撤规则；代价：需要 token 分发。建议 Phase 6 再做，先保留 legacy endpoint + `source`。
4. **`freeze.enabled=false` 的语义**：立即解冻并暂停（建议），还是只阻止新冻结？当前文档说前者。
5. **`dry_run` 是否按来源？** 建议全局，但只有 UI/CLI 能改；插件心跳不允许覆盖。
6. **“防止关闭游戏/停止专注”是否仍要做？** 若做，必须是独立模块 + 逃生口，不要塞进 `SessionGate`。

---

## 11. 重构完成定义（DoD）

1. known-issues 的 P0/P1 全 fixed，且有回归测试。
2. `domain/` 与插件 `Domain/` 无 IO/Unity 依赖，覆盖所有安全不变量。
3. 每个概念只有一个写入口：会话、规则、配置、冻结记录。
4. 心跳 2 秒一次时：0 条 audit、0 次注册表写盘（除非转移）。
5. `freeze.enabled=false`、会话结束、租约过期、崩溃、`--thaw-all` 都能恢复。
6. `degraded` 在 `/status`、看板、doctor 三处可见。
7. 两端共享 contract fixtures，头名/字段/会话语义不可能单边漂移。
8. `makepkg -f -si` + `focused-doctor` 全绿；e2e 真实进程冻结/恢复通过。
9. 插件对象被游戏销毁后，会话继续；HUD 重建后立即显示当前状态。
10. 源码树 / 本地仓库 / GitHub / 系统安装四处一致。

---

## 12. 一页纸总结

- **后端**：`domain/` 纯判定 → `app/` 用例 → `ports/` 接口 → `adapters/` 实现 → `api/` 边界。`config.rules` 与 `sources` 合成一个写入口；`Freezer` 拆成 store/suspender/recovery；`Health` 统一“看起来正常但没工作”。
- **插件**：`Domain/SessionGate` 纯判定 → `App/SessionReporter` 唯一心跳/规则 owner → `Platform/` 适配 Unity/反射/HTTP → `Ui/` 只读 `StatusStore`。删掉第二条规则推送和静态 `SessionActive`。
- **两端**：一份 `contract/` fixtures，头名/字段/会话语义由测试锁死；`/focus` 保持扁平，新增 `phase/reason/mode` 为可选字段。
- **顺序**：先修 P0/P1（数据丢失、误冻、token、解冻），再抽纯核，再拆服务，最后动 UI/功能。
