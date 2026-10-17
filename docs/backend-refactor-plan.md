# 后端重构执行计划（focused only）

> 配套：[architecture-refactor.md](architecture-refactor.md)（目标架构）、[known-issues.md](known-issues.md)（问题清单）。
> 本文是**可执行计划**：每一步都列出改哪些文件、加哪些测试、怎么验收、怎么回滚。
> 约束：保持 Python + 标准库；保持 `/api/v1/*`、`config.json`、`frozen.json` 兼容；每一步结束时全部测试全绿。
> 当前范围：**仅后端（focused）**；游戏插件（C#）与浏览器扩展不在本轮。

---

## 0.1 总任务与当前进度（仅后端）

| # | 任务 | 状态 |
|---|---|---|
| 1 | 扫描性能（RuleMatcher / 基准） | ✅ 完成 |
| 2 | P0/P1 安全修复 | ✅ 完成 |
| 3 | 领域模型（claims / health / safety） | ✅ 完成 |
| 4 | 插件能力层（规则源 / gateway / scan / API） | ✅ 完成 |
| 5 | Step 4b：规则与 claim 所有权迁移 | ✅ 完成 |
| 6 | Step 4b：手动会话状态机迁移 | ✅ 完成 |
| 7 | Step 4c：scan/freeze 编排抽离 | ✅ 完成 |
| 8 | Step 4d：events/config/status/插件生命周期收尾 | ✅ 完成 |
| 9 | Step 5：API/安全 + server/plugins 适配层拆分 | ✅ 完成 |
| 10 | Step 6：health / UI / doctor | ✅ 完成 |
| 11 | 文档、测试、基准收尾 | ✅ 完成 |
| 12 | 维护：`plugin_api` 拆分 | ✅ 完成 |
| 13 | BE-11：thaw 清理 handled | ✅ 完成 |
| 14 | BE-15：dry_run 只由 UI/CLI 改 | ✅ 完成 |
| 15 | BE-17：`--once` 默认 thaw | ✅ 完成 |
| 16 | BE-12：Freezer IO 锁外做 | ✅ 完成 |
| 17 | BE-18：frozen_count 语义 | ✅ 完成 |
| 18 | 继续拆分：server / api / plugin_api | ✅ 完成 |
| 19 | 边界情况审计 | ✅ 完成 |
| 20 | 修复 BE-40–BE-48 | ✅ 完成 |
| 21 | 修复 BE-49–BE-55 | ✅ 完成 |

**计划内进度**：**100%（11/11）**；维护任务 **7/7**；审计/修复 **3/3**。
**当前状态**：`engine.py` 580 行、`server.py` 290 行、`plugin_api`/`api` 均按职责拆分；测试 **405 项**通过（594 subtests）。
**当前任务**：无。
**基准**：500 进程 `evaluate` 0.8 ms，总扫描 9.7 ms。

---

## 0.2 架构符合度（对照最初要求）

| 最初要求 | 当前状态 | 判定 |
|---|---|---|
| 只改后端、保留 Python | 未改 C#；保持标准库 | ✅ |
| 性能可接受 | `evaluate` 0.6 ms，500 进程扫描 ~9 ms | ✅ |
| 安全第一、宁可不冻 | 规则/扫描/冻结都走安全闸门；无 guard 不冻；插件不直接发信号 | ✅ |
| 单一权威 | 规则→`RuleSourceService`，claim/会话→`SessionService`，扫描→`ScanService`，冻结→`FreezeService`，事件→`EventBus`，配置→`ConfigService`，状态→`StatusService`，插件运行时→`PluginRuntime`；engine 只做组合/兼容委托 | ✅ |
| 低耦合、小模块 | engine 580 行、server 457 行、plugins/registry 348 行、api 路由独立；仍有 `plugin_api` 411 行 | ✅ 基本满足 |
| 插件自由且安全 | 规则 add/remove/pids、state、claim、scan；能力 declared ∩ granted；无裸 freeze/thaw | ✅ |
| 可读/可调试 | 命名空间 API、reason code、`scan_ms`、审计事件 | ✅ |
| 测试保护 | 405 项通过，含真实冻结 e2e | ✅ |

**结论**：方向在执行，且没有偏离“安全优先 + 后端 only”；当前主要债务是 engine/server/plugins 三个文件仍偏大，以及事件/配置/插件生命周期还没有独立服务。

**新代码的放置规则**（避免再次把逻辑塞回 engine）：

- 纯规则/状态逻辑 → `domain/`；
- 规则、会话、扫描、冻结、事件、配置的用例 → `app/`；
- HTTP/进程/插件加载 → `adapters`（`server.py`、`plugins.py`、`core/`）；
- engine 只做组合、委托和跨服务编排；
- 冻结信号只能由 `FreezeService` → `Freezer` 发出；
- 插件只能通过 `PluginGateway` 进入应用层。

---

## 0. 目标与非目标

### 目标

| # | 目标 | 可验收标准 |
|---|---|---|
| G1 | 扫描性能 | 500 进程时 `scan_ms` p50 < 15 ms（当前约 121 ms） |
| G2 | 行为不变 | 现有测试语义不变；新增匹配等价测试 |
| G3 | 单一权威 | 配置规则、会话、冻结记录各只有一个写入口 |
| G4 | 低耦合 | `engine.py` 只做编排；纯逻辑不依赖 I/O |
| G5 | 可读性 | 命名表达领域；一个文件一个变更理由；无 `Manager/Helper/Utils` |
| G6 | 可调试 | `scan_ms` 进 `/status`；`--benchmark-scan` 可在真机测量 |
| G7 | 可回滚 | 每步独立提交；旧行为可用测试/开关回退 |

### 非目标（本轮不做）

- 不改插件（ChillFocused.dll）。
- 不改协议字段（只允许新增可选字段）。
- 不重写看板视觉；不改 `frozen.json` 语义。
- 不引入第三方依赖、asyncio、C 扩展。
- 不做“防关闭游戏/防停专注”功能。

---

## 1. 目标模块图（渐进式，不搞大搬家）

保留 `focused/core/` 的现有路径，避免一次性改所有 import；新增 `domain/`、`app/`、`ports.py`，让 `engine.py` 逐步退化为 facade。

```
focused/focused/
  core/                 # 低层算法 + 系统适配器（现有路径保留）
    matching.py         # 新增：RuleMatcher（精确名 set + 通配符预编译）
    rules.py            # RuleSet/Decision/evaluate（改用 matcher）
    procfs.py cgroupfs.py freezer.py audit.py protect.py urlfilter.py
  domain/               # 新增：应用级纯模型（无 IO）
    session.py          # Phase/TimerMode/SessionIntent/SessionPolicy
    claims.py           # Claim/ClaimBook（租约、过期、幂等续租）
    health.py           # Health：degraded/恢复失败/问题列表
    safety.py           # 最终安全闸门 + choose_mode（slice/user-manager 永不冻）
  app/                  # 新增：用例编排（依赖 core/domain/ports）
    config_rules.py     # 配置来源规则 + 持久化协调（BE-01/02）
    session.py          # 手动会话 + claims
    scan.py             # 一次扫描
    freeze.py           # FreezeCoordinator
    recovery.py         # 启动恢复 / thaw-all
    health.py           # HealthService
    events.py           # 审计/事件/webhook 分流
    plugins.py          # 插件平台用例
  plugin_api.py         # 新增：进程内插件窄接口（rules/session/status/log）
  diagnostics.py        # 新增：event=... key=... 结构化日志
  ports.py              # 新增：IO Protocol（按需增长）
  engine.py             # 逐步缩成 composition + 兼容 facade
  config.py server.py ui.py plugins.py focusedd.py
```

**为什么不全量搬到 `domain/adapters`**：当前 256 项测试直接 import `focused.core.*`。一次性搬家会把“重构”变成“重命名 + 修 import”，噪音大、回滚难。先抽应用层，再按需移动底层。

---

## 2. 代码规范（本重构的硬要求）

1. **命名**：`SessionService`、`RuleService`、`ScanReport`、`FreezePlan`、`FrozenRecord`；禁止 `Manager/Helper/Utils/Common`。
2. **状态**：用枚举/不可变 dataclass，不用多个 bool 组合；`active/phase/reason` 一起出现。
3. **返回**：决策返回带 `reason` 的值（`Decision`/`FreezeOutcome`/`ScanReport`），不返回裸 bool。
4. **错误**：领域错误是值（`RuleProblem`），边界翻译成 HTTP 400/409；不要吞 `OSError`。
5. **纯函数**：`core/`、`domain/` 不读时钟、不读文件、不发信号；需要时通过参数传入。
6. **热路径**：不写审计、不打 info 日志、不落盘；只有状态转移才记账。
7. **日志**：结构化字段 + reason code，不拼句子。
8. **文件大小**：目标 100–300 行；超过 400 行必须能说出“为什么不能拆”。
9. **注释**：写“为什么/反例”，不写“做什么”。
10. **兼容**：新增字段可选；旧字段保留；配置键不重命名。

---

## 3. 分步实施

### Step 1 — 匹配器性能（本轮先做）

**问题**：`evaluate()` 对每个进程遍历 453 条保护规则，每条对最多 3 个名字候选做 `fnmatchcase` 正则匹配。500 进程 ≈ 68 万次正则/扫描。

**实现**：

1. 新增 `core/matching.py`：
   - `RuleMatcher`：把规则分成 `exact`（无通配符）与 `wildcard`（预编译 `re.compile(fnmatch.translate(p))`）。
   - 保留**排序后的首个匹配**语义：精确匹配与通配符匹配各记 `order`，取最小 order。
   - 处理 `COMM_MAX_LEN=15` 截断：精确模式长度 >15 时，用 `pattern[:15]` 建索引，候选 comm 为 15 字符时可 O(1) 命中。
   - `protect_match(candidates)` / `blacklist_match(candidates)` 用 `lru_cache` 记忆化（按候选元组）。
   - `cmdline_match` 用一次 `proc.cmdline_text.lower()`，按 tuple 顺序返回首个命中。
2. `matcher_for(rules)`：按 `(names, protect_names, cmdline_substrings, protect_cmdline_substrings)` 缓存 `RuleMatcher`（`lru_cache(maxsize=32)`）。
3. `core/rules.py`：`evaluate()`、`protect_reason()` 改用 matcher；`name_matches`/`cmdline_matches` 保留为单模式工具（测试用）。
4. 新增 `tests/test_matching.py`：
   - 与旧实现（测试内保留 reference loop）逐条比较 `Decision.action/rule/reason`。
   - 覆盖精确名、通配符、长名截断、protect 优先、排序首匹配、空规则、catch-all。
5. `tests/test_focused_engine.py` 增加 `scan_ms` 断言（见 Step 1b）。

**验收**：

```
python3 .dshtmp/bench/synthetic_bench.py
  evaluate: 当前 112 ms -> 目标 < 5 ms（500 进程）
  total:    当前 121 ms -> 目标 < 15 ms
pytest -q  # 256+ 全绿
```

**回滚**：`evaluate` 保留旧循环的分支/测试；如出现语义差异，恢复 `rules.py` 的两个函数即可。

### Step 1b — 可测量性

1. `engine.scan_once()` 记录 `last_scan_ms`，保留最近 60 次样本，`status()` 输出 `scan_ms`（p50）与 `last_scan_ms`。
2. `focused --benchmark-scan [N]`：不启 HTTP、不冻结，跑 N 次 `snapshot + evaluate`，输出 processes / protect / p50 / p95 / min / max。
3. 测试：`status()` 含 `scan_ms`；`--benchmark-scan` 的解析/输出冒烟。

### Step 2 — 配置单一权威 + P0 修复

1. `RuleService`（先放 `app/config_rules.py`）：
   - `sources["config"]` 与 `config.rules` 不再各存一份；`ConfigStore.save(config, rules=...)` 用同一份。
   - `update_config` 改为调用 service；PATCH 后 `Config.load()` 仍在。
2. `RuleSet.problems()` / `RuleRejected`：
   - catch-all 在 `Config._validate`、`engine.apply_rules`、API 三层拒绝。
   - `shadowed_names` 变成 warning，不再死代码。
3. BE-05：`freeze.enabled=false` 时 `thaw_all("freeze-disabled")`。
4. BE-06：`ClaimBook.claim()` 返回 `changed`；只有 changed 才 audit/log/registry save。
5. 测试：BE-01/02/03/05/06 每条一个回归测试。

### Step 3 — 抽纯 domain

1. `domain/claims.py`：`Claim`、`ClaimBook`（从 `engine._claims` 抽出）。
2. `domain/health.py`：`Health`（scan_failures/degraded/recovery_failures）。
3. `domain/session.py`：`Phase`、`SessionIntent`、`SessionPolicy`（为后端保存前端 intent 做准备）。
4. `engine` 委托；旧属性保留为只读视图，避免测试/API 破坏。
5. 测试：domain 单测 + engine 现有 claim 测试全绿。

### Step 4 — 抽 app 服务

1. `app/rules.py`：来源合并、校验、持久化协调。
2. `app/session.py`：手动会话状态机 + claims + pause。
3. `app/health.py`：扫描失败/恢复失败聚合。
4. `app/events.py`：动作事件 → audit/event/webhook；心跳只进 metrics。
5. `engine` 变成组合根 + facade；逐步删除重复方法。

### Step 5 — 扫描/冻结执行层

1. `app/scan.py`：`ScanReport`、`scan_once()`、拒绝原因统计。
2. `app/freeze.py`：`FreezeCoordinator`（plan → suspend → record → emit）。
3. `core/freezer.py` 按需拆 `FrozenStore` / `PidSuspender` / `UnitSuspender`；先加 `_confirm_thawed()`。
4. 测试：不变量 1–6（见 architecture-refactor §7.2）。

### Step 6 — 收尾

1. `api/protocol` 常量：`X-Focused-Token`、reason code、路径。
2. `status` 的 Health 进看板/doctor（后端侧；看板视觉可后续）。
3. 删除死代码：重复 `_sources["config"]`、`engine.close`、`plugins.adopt`、无调用的 helper。
4. 文档/测试数量同步。

---

## 4. 性能预算与测量

| 指标 | 当前 | 目标 |
|---|---:|---:|
| `snapshot()` 500 进程 | 8.7 ms | ≤ 10 ms |
| `evaluate()` 500 进程 | 112 ms | ≤ 5 ms |
| `scan_ms` p50 | ~121 ms | ≤ 15 ms |
| 1s 间隔 CPU | ~12% 单核 | ≤ 2% |
| 0.2s 间隔 CPU | ~60% 单核 | ≤ 10% |

测量方法：

- 开发期：`.dshtmp/bench/synthetic_bench.py`（合成 500 进程，可重复）。
- 真机：`focused --benchmark-scan 20`（真实 `/proc`，不冻结）。
- 运行期：`GET /api/v1/status` 的 `scan_ms`。

---

## 5. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 匹配器改变 `Decision.rule` 字符串 | 等价测试逐条比较；保留 reference 实现 |
| 配置持久化改动破坏旧配置 | 只新增同步，不改文件格式；`Config.load` 兼容 |
| 拆 engine 导致循环 import | `engine` 只 import `app`；`app` import `core/domain/ports`；禁止反向 |
| 测试依赖私有属性 | 先保留只读属性，Step 6 再清理 |
| 性能回退 | 每步跑 benchmark；`scan_ms` 进 status |
| 大提交难回滚 | 每步一个独立提交；测试先红后绿 |

---

## 6. 本轮执行清单

- [x] `core/matching.py` + `matcher_for`
- [x] `core/rules.py` 改用 matcher
- [x] `tests/test_matching.py` 等价测试
- [x] `engine.scan_once` 计时 + `status.scan_ms`
- [x] `focused --benchmark-scan`
- [x] `pytest -q` 全绿
- [x] `.dshtmp/bench/synthetic_bench.py` 复测
- [x] 更新本计划与 known-issues 状态

### Step 1 结果（2026-10-17）

| 指标 | 重构前 | 重构后 |
|---|---:|---:|
| `snapshot()` 500 进程 | 8.7 ms | 9.0 ms |
| `evaluate()` 500 进程 | 112.3 ms | **0.6 ms** |
| `scan_ms` p50（合计） | ~121 ms | **~9.6 ms** |
| 1s 间隔 CPU | ~12% 单核 | **~1% 单核** |
| 0.2s 间隔 CPU | ~60% 单核 | **~4.8% 单核** |

测试：**267 passed**（原 256 + `tests/test_matching.py` 10 项 + `scan_ms` 1 项，另有 594 个 subtest）。

涉及文件：

- 新增 `focused/core/matching.py`（`RuleMatcher`、`matcher_for`）
- 修改 `focused/core/rules.py`（`evaluate`/`protect_reason` 使用 matcher；旧 `name_matches`/`cmdline_matches` 保留给测试与 reference）
- 修改 `focused/engine.py`（`scan_ms` 采样与 `/status` 字段）
- 修改 `focused/focusedd.py`（`--benchmark-scan`）
- 新增 `tests/test_matching.py`；`tests/test_focused_engine.py` 增加 `scan_ms` 断言

Step 2 已完成（见下）。

### Step 2 结果（2026-10-17）

| 问题 | 修复 | 测试 |
|---|---|---|
| BE-01/02 配置不落盘/互相覆盖 | `update_config` 在 `apply_rules` 后同步 `config.rules` 再 `save()`；部分 patch 不再用陈旧副本回填 | `ConfigPersistenceTests` 4 项 |
| BE-03 `*` 不被拒绝 | `RuleSet.problems()` + `RuleRejected`；`Config._validate`、`engine.apply_rules`、`PUT /focus/rules` 三层拒绝 | `RuleValidationTests` 3 项 + `RuleValidationProtocolTests` 3 项 |
| BE-05 关闭 freeze 不解冻 | `update_config` 立即 `thaw_all("freeze-disabled")`；`_scan_locked` 防御性 thaw | `test_disabling_freeze_releases_the_recorded_targets` |
| BE-06 心跳放大记账 | `set_state` 只在 claim 转移时 audit/log；`note_session` 续租只更新内存；`touch` 不落盘；`note_rules` 仅计数变化时保存 | `HeartbeatIdempotencyTests` 2 项 |

测试：**279 passed**（含 594 个 subtest）。`focused-doctor` 与看板还未消费 Health，属于后续 Step。

### Step 3 结果（2026-10-17）

| 模块 | 内容 | 测试 |
|---|---|---|
| `domain/claims.py` | `Claim` / `ClaimBook`；续租、过期、最近截止时间/租期、描述 | `tests/test_claims.py` |
| `domain/health.py` | `Health`：scan 失败、degraded、freeze 关闭、恢复失败；`/status` 增加 `health` 对象 | `tests/test_health.py` |
| `domain/safety.py` | 最终安全闸门：hard rails、未知 uid、protect、skip plan、unit plan、空树、未知 starttime；`choose_mode` 修复 slice/user-manager 被当成 pid 的问题 | `tests/test_safety.py` |
| `diagnostics.py` | `event=... key=...` 结构化日志；claim/thaw/freeze/refuse/plugin 事件 | `tests/test_diagnostics.py` |
| `plugin_api.py` | 窄接口（rules/session/status/log）、能力声明、注册期缓冲、输入校验；loader/engine 改为传 `PluginAPI` 而不是 engine | `tests/test_plugin_api.py` + 插件测试 |
| 插件文档/示例 | `docs/plugin-api.md`、`examples/plugins/hello.py`、`examples/http_plugin.py`；插件 token 只读 `/focus`、`/events` | 手工 + 协议测试 |

安全性变化：`PLAN_SKIP` 的 `slice` / `session-manager` 现在直接拒绝；`unknown-uid`、
`unknown-starttime` 一律不冻（BE-30/31），`Freezer` 对缺少 guard 的 pid 也拒绝发信号。插件不再拿到 engine，只能贡献规则/租约；
插件失败/禁用时清理其规则与租约；插件 token 只读 `/focus`、`/events`；HTTP 注册禁止
`kind=python`，webhook 只接受 http(s)（BE-09/14/34）。

测试：**334 passed**（594 subtests）。下一步：Step 4（抽 `app/rules.py`、`app/session.py`、`app/health.py`）。

### Step 4（部分）— 插件能力层（2026-10-17）

按“插件自由但安全”的目标，新增一层窄服务，插件不再直接碰 engine：

| 模块 | 内容 |
|---|---|
| `app/rules.py` | `RuleSourceService`：**拥有** `_sources`、合并后的 `_merged`、`url_patterns`；replace/add/remove/clear；PID guard 自动填充也在服务内 |
| `app/scan.py` | `ScanRequester`：全局冷却（默认 1s）、返回 `ScanReport` |
| `app/session.py` | `SessionService`：**拥有** `ClaimBook`（live/claims/expire/nearest）；高层的 claim 审计/日志仍由 engine 统一处理 |
| `app/state.py` | `StateService`：summary / frozen / rules（只读） |
| `app/plugin_gateway.py` | `PluginGateway`：能力校验（declared ∩ granted）、所有权、审计、限流；进程内与 HTTP 共用 |
| `plugin_api.py` | 命名空间 `api.rules/session/state/scan/events`；旧 flat 方法保留为别名 |

安全前置条件：

- `evaluate` 对无 guard 的 PID 规则改为 `SKIP`；
- `apply_rules` 自动填 starttime guard，拒绝 gone/reused/pid≤1；
- HTTP 插件端点新增 `/rules`（GET/PUT）、`/rules/add`、`/rules/remove`、`/state`、`/scan`；
- 插件能力分 `declared` / `granted`，危险能力（`process.control`、`focus.override`）默认不授予；
- 扫描请求全局冷却，避免多插件放大扫描工作。

测试：**377 passed**（594 subtests）。下一步：#9 API/安全收尾。

### Step 4b（已完成）— 状态所有权迁移

第一步已经落地：

- `RuleSourceService` 现在真正持有 `_sources` / `_merged` / `url_patterns`；engine 的 `rules()`、`sources()`、`apply_rules()`、`clear_source()`、`set_url_patterns()` 全部变成委托；
- PID guard 自动填充、catch-all/类型校验、config source 初始化都在规则服务内完成；
- `SessionService` 现在真正持有 `ClaimBook` **和手动会话状态机**；engine 的 `live_claims / claims / delegated / delegated_source / nearest_* / expire` 以及 `start/extend/skip/stop/advance/session_state/pause` 全部委托；
- 删除了 engine 中重复的旧插件入口（`plugin_rules`、`plugin_session`、`replace_plugin_rules`、`plugin_status_snapshot` 等死代码）；
- `engine.py` 从 1818 行降到 1355 行；`app/rules.py` 358 行，`app/session.py` 444 行。

### Step 4c（已完成）— scan/freeze 编排抽离

- `ScanService`（[app/scan.py](../focused/app/scan.py)）持有扫描锁、采样、scan loop、session housekeeping；只调用 `FreezeService`，不发信号；
- `FreezeService`（[app/freeze.py](../focused/app/freeze.py)）持有 thaw_all/recover/thaw_stale/handled/plan/safety 调用，是 `Freezer.freeze/thaw` 的唯一扫描侧调用者；
- engine 的 `scan_once/scan_ms/run_forever/frozen/thaw_all/recover_frozen/close/_thaw_stale/_freezable_pids` 全部变成委托；
- `engine.py` 1355 → 999 行；`app/freeze.py` 300 行；`app/scan.py` 240 行。

下一步（#8）：把 events/config/status/插件生命周期从 engine 抽成 app 服务，让 engine 只剩组合与委托。

测试：**377 passed**（594 subtests）。

### Step 4d（已完成）— events/config/status/插件运行时

- `EventBus`（[app/events.py](../focused/app/events.py)）：事件 ring/seq、能力过滤、on_event 分发、webhook、失败回调；
- `PluginRuntime`（[app/plugin_runtime.py](../focused/app/plugin_runtime.py)）：加载/绑定/停止/失败计数/清理/启用/移除；
- `StatusService`（[app/status.py](../focused/app/status.py)）：`status` / `focus_state` / `config_dict` / 进程列表 / 插件 payload；
- `ConfigService`（[app/config.py](../focused/app/config.py)）：`update_config` 的校验、持久化与运行时同步；
- engine 的对应方法全部变成委托，`engine.py` 999 → 580 行。

测试：**377 passed**（594 subtests）。下一步：#9 API/安全收尾。

### Step 5（已完成）— 适配层拆分 + 安全收尾

- `server.py` 673 → 457 行；鉴权移到 [api/auth.py](../focused/api/auth.py)，HTTP helper/DTO 移到 [api/http.py](../focused/api/http.py)，插件路由移到 [api/plugin_routes.py](../focused/api/plugin_routes.py)；
- `plugins.py` 503 → 包：`plugins/registry.py` 348 行、`plugins/loader.py` 102 行；删除 `load_addons` 中不可达的旧加载循环；
- 现有 `focused.plugins` 导入通过 `__init__.py` 兼容；
- 测试 **394 passed**（594 subtests）。

安全收尾：

- BE-07：`api/security.py` 校验 loopback `Host`/`Origin`，JSON body 要求 `application/json`；
- BE-04：`GET /` 无需 token；看板 `api()` 用 `sessionStorage` token，401 时提示输入；
- BE-08：`config.json` / `plugins.json` 原子替换前 chmod 0600；
- BE-10：`_confirm_thawed()` 轮询 `cgroup.frozen 0`，未确认则保留记录重试；
- BE-13：`FocusedServer.set_token()` 接到 `_server_token_setter`，运行中的 server 立即使用新 token。

### Step 6（已完成）— health / UI / doctor

- 看板新增 `healthBanner`：读取 `status.health`，显示 `degraded`、`scan_failures`、`last_scan_error`、`recovery_failures`、`freeze_enabled=false`，并在 `scan_ms.p95 > 50ms` 时提示；
- `doctor-focused.sh` 第 8 步改为先查 `GET /api/v1/status` 的 `health` 字段，拿不到时回退到 journalctl；
- 新增 `test_doctor_health.py`、看板 health 断言；
- 测试 **397 passed**（594 subtests）。

#11 已完成：最终基准（evaluate 0.8 ms / 扫描 9.7 ms，500 进程）、文档链接检查、397 项全量回归。

### Step 7（进行中）— 维护：继续拆分 + 剩余 BE

- `plugin_api.py` 411 行 → 包：`api.py` 301、`capabilities.py` 64、`manifest.py` 43、`errors.py` 12、`__init__.py` 43；旧文件删除；
- BE-11：`FreezeService.thaw_all` 清空 handled，`thaw_stale` 按 entry.pids 清理；
- BE-15：`/focus/state` 不再接受 `dry_run`；只有 `PATCH /config` 能改；
- BE-17：`--once` 默认 thaw，新增 `--keep-frozen` 显式保留；
- 测试 **400 passed**（594 subtests）。

BE-12：`freeze`/`thaw` 的 systemctl、确认、落盘 IO 移出 `_lock`；`_inflight`/`_thawing` 防重入，`thaw_all` 用 generation 取消并发 freeze；新增“状态读取不被慢冻结阻塞”和“thaw_all 取消 in-flight freeze”测试。

BE-18：`recorded_pids()` 与 `stopped_pids()` 分开；`frozen_count()` 改为实际 T/t 进程数；status 增加 `frozen_recorded_count`；新增 recorded/stopped 差异测试。

### Step 8（已完成）— 继续拆分 server / api / plugin_api

- `server.py` 484 → **290 行**：只保留 handler、transport/auth、响应与异常映射；
- 新增 [api/read_routes.py](../focused/api/read_routes.py)、[api/session_routes.py](../focused/api/session_routes.py)、[api/core_routes.py](../focused/api/core_routes.py)；
- 插件路由、鉴权、HTTP helper、security 已在独立模块；
- `plugin_api/api.py` 301 → **188 行**：拆出 `validation.py`（50）和 `namespaces.py`（86）；
- 测试 **404 passed**（594 subtests），HTTP/e2e 全部通过。

### Step 9（已完成）— 边界审计与 BE-40–48

- 审计矩阵：`.dshtmp/audit/EDGE-CASES.md`，复现：`.dshtmp/audit/edge_cases.py`；
- F1→BE-41：`thaw_stale` 只在 thaw 成功时计数/清 handled；
- F2→BE-40：取消冻结且撤销失败时保留记录；
- F3→BE-42：`ConfigService` candidate + `RuleSourceService.preview`，验证后提交；
- F4→BE-43：`scan_interval` 有限数/范围校验，ScanService 防御回退；
- F5→BE-44：注册期规则调用改为有序队列；
- F6→BE-45：`url_patterns` 拒绝字符串；
- F7→BE-46：旧 pid 保留 guard，只校验新 pid；新增 `preview()`；
- F8→BE-47：`frozen.json` 0600；
- F9→BE-48：pid<=0 明确报错；
- 测试 **416 passed**（594 subtests），`verify_fixes.py` 全部 PASS。

### Step 10（已完成）— BE-49–BE-55

- BE-49：`ctx.uid is None` → `unknown-daemon-uid`；
- BE-50：缺少 `Host` → 400；
- BE-51：单字符 blacklist cmdline → `rule.cmdline-short`；
- BE-52：单 source 上限 5000 条；
- BE-53：webhook 改为有界队列（100）+ 单 daemon worker；
- BE-54：`frozen.json` 保存失败先撤销，撤销失败保留内存记录；
- BE-55：已有冻结记录直接 skip，避免重复累计；
- 测试 **405 passed**（594 subtests）。
