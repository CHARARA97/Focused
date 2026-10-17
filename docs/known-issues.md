# Known issues — ChillFocused / Focused

> 状态：2026-10-17 审计结果。  
> 范围：`chillfocus/ChillFocused`（游戏插件）与 `focused`（后端）。  
> 每一行都标了**证据类型**：`verified` = 当场复现；`code-read` = 代码路径明确；`doc` = 文档与实现不一致。  
> 这是重构的输入，不是 bug 工单的替代：修完一项就把状态改成 `fixed` 并补上回归测试。

---

## 0. 摘要

| 级别 | 含义 | 条目数 |
|---|---|---|
| P0 | 数据丢失，或可能一次性冻结大量进程 | 7 条（PLG-01 与 X-01 是同一问题；BE-30/35/40 已修复） |
| P1 | 常见场景下核心功能/安全不变量被破坏 | 12 条（PLG-02/03/04 与 X-02/03/04 是同一问题；BE-04/31/41/49 已修复） |
| P2 | 可靠性、可观测性、安全加固 | 39 条（BE-07/08/09/10/11/12/13/14/15/16/17/18/29/32/33/34/36/37/38/39/42/43/44/45/46/47/50/51/52/53/54 已修复） |
| P3 | 清理、文档、边界情况 | 19 条（BE-28/48/55 已修复） |

**已修复（2026-10-17）**：BE-01 / BE-02 / BE-03 / BE-05 / BE-06 / BE-09 / BE-14 / BE-29 / BE-30 / BE-31 / BE-32 / BE-33 / BE-34 / BE-04 / BE-07 / BE-08 / BE-10 / BE-11 / BE-12 / BE-13 / BE-15 / BE-16 / BE-17 / BE-18 / BE-35 / BE-40 / BE-41 / BE-42 / BE-43 / BE-44 / BE-45 / BE-46 / BE-47 / BE-48 / BE-49 / BE-50 / BE-51 / BE-52 / BE-53 / BE-54 / BE-55 / BE-36 / BE-37 / BE-38 / BE-39；后端测试 412 项全绿。

**先修哪三个**：`BE-01`（配置改了不落盘）、`BE-03`（`*` 不拒绝）、`X-01`（令牌头不匹配）。这三个都能让用户“以为配置好了，其实没有”。

---

## 1. 跨项目问题（X）

| ID | 级别 | 类型 | 问题 | 证据 | 影响 | 修复方向 |
|---|---|---|---|---|---|---|
| X-01 | P0 | bug | 插件发送 `X-ChillFocused-Token`，后端只读 `X-Focused-Token` | [HttpTransport.cs:115](../../chillfocus/ChillFocused/Core/HttpTransport.cs#L115) vs [server.py:56](../focused/server.py#L56)；单测 [HttpTransportTests.cs:176](../../chillfocus/ChillFocused.Tests/HttpTransportTests.cs#L176) 还断言了错误头 | 配置 `http.token` 后所有插件请求 401；心跳发不出 → 完全不冻结。`focused-doctor` 只比较 token 值，给假绿 | 统一常量；补一个对真实 `server.py` 读头逻辑的契约测试 |
| X-02 | P1 | design | 插件有两条规则推送路径，后端无法区分来源 | [HeadlessRunner.cs:279](../../chillfocus/ChillFocused/Core/HeadlessRunner.cs#L279) 与 [FocusRunner.cs:617](../../chillfocus/ChillFocused/Core/FocusRunner.cs#L617)；两者都落 `apply_rules(body)` 的默认 source `builtin`（[server.py:217](../focused/server.py#L217)） | 重复请求/日志；两套退避策略；两份设置快照可能互相覆盖 | 规则推送只保留一个所有者（headless/reporter）；或给 payload 增加显式 source |
| X-03 | P1 | bug | 规则推送失败会阻断心跳 | [HeadlessRunner.cs:279-303](../../chillfocus/ChillFocused/Core/HeadlessRunner.cs#L279-L303)：`PUT rules` 在 `POST state` 之前，且无 try/catch | 一条坏规则 → 本轮心跳不发出 → 30s 租约到期 → 全部解冻；表现为“功能悄悄停摆” | 先发 heartbeat，规则推送独立 try/catch；两者互不阻断 |
| X-04 | P1 | design/doc | 会话语义在两处实现，且“正向计时是否拦截”自相矛盾 | `SessionGate.Active` 没有 `TimerMode` 分支；`NEXT-SESSION.md` §1.3 说“正向计时一律不拦”，插件 README 说“正向计时同理” | 同一份产品规则有两个说法，测试无法判定哪个对 | 把 `countup_is_active` 变成显式策略字段；用共享契约 fixture 固化 |
| X-05 | P2 | doc | 协议文档与实现漂移 | `docs/focused-protocol.md` 仍描述旧的 `delegate` 配置；`docs/focused-design.md` 写 196 项测试，实际 256；`rules.py` 顶部声称 `*` 不会杀人（实际会） | 后来者按文档理解行为会错 | 文档纳入测试（数字/端点/头名由测试校验）；见 §3 |
| X-06 | P2 | design | 两端没有共享契约 fixture | 插件测试与后端测试各自造 JSON；头名、DTO 字段、会话语义都可能单边漂移 | 单测全绿、集成必挂（X-01 就是例子） | 建 `contract/`：`session-cases.json`、`focus-payload.json`、`error-codes.json`，两端测试都读 |

---

## 2. 后端 Focused（BE）

### 2.1 P0 / P1

| ID | 级别 | 类型 | 问题 | 证据 | 影响 | 修复方向 |
|---|---|---|---|---|---|---|
| BE-01 | P0 | bug（数据丢失） | 黑名单/保护名单的 PATCH/CLI 修改不落盘 | [engine.py:1300-1314](../focused/engine.py#L1300-L1314) 只改 `_sources["config"]`；[config.py:278-286](../focused/config.py#L278-L286) 的 `to_dict()` 读的是未更新的 `config.rules`；`save()` 在 [config.py:289](../focused/config.py#L289) | 实测：PATCH `["firefox"]` → 内存/界面显示 `["firefox"]`，`config.json` 仍是 `[]`；重启即丢。`focused --add-name` 同样失效 | **已修复（2026-10-17）**：`update_config` 在 `apply_rules` 后同步 `config.rules` 再 `save()`；测试覆盖 “PATCH → `Config.load()` → 仍在” |
| BE-02 | P1 | bug | 连续/部分修改会互相覆盖 | 同一根因：`update_config` 用 `_config_blacklist(config)`（未更新）做回退 | 实测：先加 `firefox`，再加 `chromium` → 只剩 `chromium`；先加名字，再加 cmdline → 名字被清空 | **已修复（2026-10-17）**：同 BE-01；另外禁止“部分 patch 用陈旧副本回填” |
| BE-03 | P0 | bug（安全） | `names=["*"]` 不被拒绝，会匹配所有未保护进程 | [rules.py:44](../focused/core/rules.py#L44) 定义 `is_catch_all_pattern` 但全项目无调用；[config.py:223](../focused/config.py#L223) 不校验规则；`apply_rules` 不校验 | 实测：`evaluate(firefox, names={"*"})` 返回 `match`。446 条内置保护挡住桌面，但浏览器/编辑器会被逐轮冻结 | **已修复（2026-10-17）**：`RuleSet.problems()` + `RuleRejected`；`Config._validate`、`engine.apply_rules`、`PUT /focus/rules` 三层拒绝（配置错误 / 400） |
| BE-04 | P1 | bug（可用性） | 配置 `http.token` 后看板完全打不开 | [server.py:127-131](../focused/server.py#L127-L131) 对 `/` 也要求 token；[ui.py:340-350](../focused/ui.py#L340-L350) 的 `api()` 从不发送 token | 实测：`GET /` 无头 → 401；设置页是 write-only token，没有登录入口。用户会把自己锁在门外 | **已修复（2026-10-17）**：`GET /` 无需 token；看板 `api()` 用 `sessionStorage` token，401 时提示输入 |
| BE-05 | P1 | bug（安全/语义） | 关闭 `freeze.enabled` 不会解冻已冻结进程 | [engine.py:876-889](../focused/engine.py#L876-L889) 在 `_housekeeping` 之后才看 `freeze_enabled`；[engine.py:1284-1288](../focused/engine.py#L1284-L1288) 只改标志 | 实测：注入冻结记录 → `freeze.enabled=false` → `scan_once()` → 记录仍在，`thawed=0`。与 [freeze-mode.md §2](../docs/freeze-mode.md) 的“enabled=0 → 全部解冻”矛盾 | **已修复（2026-10-17）**：`update_config` 关闭时立即 `thaw_all("freeze-disabled")`；`_scan_locked` 再做一次防御性 thaw |
| BE-30 | P0 | bug（安全） | `PLAN_SKIP` 被当成 pid 计划，slice / user manager 的成员可能被进程树冻结 | 旧 `_freeze_target` 只判断 `plan.plan == PLAN_UNIT`，其他一律 `MODE_PID`；`plan_for` 的 `slice`/`session-manager` 拒绝被忽略 | 可能冻到不属于目标的 slice/会话成员；与 freeze-mode §4.1 H4 冲突 | **已修复（2026-10-17）**：`domain/safety.py:choose_mode` 把 `slice`/`session-manager` 变为 `skip`；安全闸门拒绝；其余安全 skip reason 才降级为 pid |
| BE-31 | P1 | bug（安全） | 无法证明 uid/starttime 时仍可能冻结 | `evaluate` 只在 `proc.uid` 非空时比较；`Freezer._freeze_pids` 在 `expected is None` 时仍发 SIGSTOP | 可能冻结异 uid 或 PID 复用后的陌生进程 | **已修复（2026-10-17）**：`domain/safety.py` 对 `unknown-uid` / `unknown-starttime` 一律拒绝；`_freeze_target` 要求整棵树都有 starttime；`Freezer._freeze_pids` 对缺少 guard 的 pid 也拒绝发信号 |

| BE-40 | P0 | safety | `thaw_all` 取消 in-flight freeze 后，若撤销解冻失败，记录被丢弃 | `Freezer.freeze` 的 cancelled 分支；`.dshtmp/audit/edge_cases.py` | 进程可能仍处于冻结态，系统中却没有记录可恢复 | **已修复（2026-10-17）**：撤销失败/异常时保留 entry 并 `_save()` |
| BE-41 | P1 | correctness | `thaw_stale()` 忽略 `Freezer.thaw()` 返回值，失败也计数并清 handled | `app/freeze.py` 的 `thaw_stale`；`.dshtmp/audit/edge_cases.py` | 状态显示“已恢复”，记录仍在；handled 被清导致语义错误 | **已修复（2026-10-17）**：只有 thaw 成功才 `_forget`/计数 |

| BE-49 | P1 | safety | `check_safety` 不拒绝 `ctx.uid is None` | `domain/safety.py` | 缺少 daemon UID 时可能跳过 foreign-uid 检查 | **已修复（2026-10-17）**：`ctx.uid is None` → `unknown-daemon-uid` |

### 2.2 P2

| ID | 级别 | 类型 | 问题 | 证据 | 影响 | 修复方向 |
|---|---|---|---|---|---|---|
| BE-06 | P2 | design/perf | 心跳热路径被当成“动作”记账 | [engine.py:263-307](../focused/engine.py#L263-L307) 每次 `set_state` 都审计+info 日志；[plugins.py:245-251](../focused/plugins/registry.py#L245-L251) 无条件 `save()` | 实测 5 次心跳 → 5 条日志 + 5 条审计 + 1 次注册表写盘。现场 `audit.jsonl` 48,073 行中 27,493 条是 `state`（57%） | **已修复（2026-10-17）**：`set_state` 只在 claim 发生转移时 audit/log；`note_session` 续租只更新内存（`persist=False`）；`touch` 不再落盘；`note_rules` 仅计数变化时保存 |
| BE-07 | P2 | security | 无 `Origin`/`Host` 校验，且 `_read_json` 不检查 `Content-Type` | [server.py:104-120](../focused/server.py#L104-L120)；整份 server 无 Origin/Host 读取 | 无 token 时，任意网页可用 `text/plain` 简单请求触发 `/focus/state`、`/config`（CSRF）；DNS rebinding 可读响应。加 token 可挡，但会触发 BE-04 | **已修复（2026-10-17）**：`api/security.py` 校验 loopback Host/Origin；JSON body 要求 `application/json` |
| BE-08 | P2 | security | `config.json`、`plugins.json` 以 0644 落盘，且含 token | `config.py` / `plugins/registry.py` 的 save；现场权限 0644。对比 [audit.py:50](../focused/core/audit.py#L50) 明确 0600 | 同机其他用户可读取 `http.token`/插件 token；配合 loopback 可操纵冻结 | **已修复（2026-10-17）**：config/plugins 原子替换前 chmod 0600 |
| BE-09 | P2 | security | `GET /api/v1/plugins` 返回每个插件的 token | [plugins.py:62](../focused/plugins/registry.py#L62)；看板并不使用 | 任何能读 API 的本地进程都能冒充插件 | **已修复（2026-10-17）**：`to_public_dict()` 去掉 token、保留 `token_set`；列表/status 脱敏，token 只在注册响应出现 |
| BE-10 | P2 | bug（安全） | 解冻只发信号/写 0，不确认 `frozen 0` | [freezer.py:534-559](../focused/core/freezer.py#L534-L559) 对比冻结的 `_confirm_frozen`（512-530） | 若 `systemctl thaw` 返回成功但实际未解冻，记录会被删除 → 永久冻结且无凭据 | **已修复（2026-10-17）**：`_confirm_thawed()` 轮询 `cgroup.frozen 0`，未确认保留记录 |
| BE-11 | P2 | bug | `_handled` 的 30s TTL 在解冻时不清空 | [engine.py:50](../focused/engine.py#L50)、[engine.py:801-818](../focused/engine.py#L801-L818)、[engine.py:746](../focused/engine.py#L746) | 停止后 30s 内重开会话，目标可能不被重新冻结 | **已修复（2026-10-17）**：`FreezeService.thaw_all` 清空 handled；`thaw_stale` 按 entry.pids 清理 |
| BE-12 | P2 | concurrency | `Freezer.freeze/thaw` 在 `systemctl` + 1.5s 确认 + 落盘期间持有 `RLock` | [freezer.py:292-306](../focused/core/freezer.py#L292-L306)、[freezer.py:362-414](../focused/core/freezer.py#L362-L414) | `/status`、`/thaw` 可被阻塞数秒；HTTP 线程堆积 | **已修复（2026-10-17）**：`freeze`/`thaw` 的慢 IO 移出 `_lock`；`_inflight`/`_thawing` 防重入；`thaw_all` 用 generation 取消并发 freeze |
| BE-13 | P2 | bug | `_server_token_setter` 从未被赋值 | [engine.py:1279](../focused/engine.py#L1279)；`FocusedServer` 只在 `start()` 时设置 `httpd.token` | 看板改 token 只写文件，运行中的 server 仍用旧 token，且没有“需重启”提示 | **已修复（2026-10-17）**：`FocusedServer.set_token()` 接到 `_server_token_setter`，运行中的 server 立即切换 token |
| BE-14 | P2 | security | HTTP 注册允许 `kind="python"`，而 `verify()` 对 python 插件无条件放行 | [server.py:276](../focused/server.py#L276)、[plugins.py:265](../focused/plugins/registry.py#L265) | 该 id 的插件端点可绕过 token；虽然注册本身需要 app token，但这是不必要的混淆 | **已修复（2026-10-17）**：HTTP 注册强制 `kind=http`（400）；python 只能由加载器注册 |
| BE-15 | P2 | design | `dry_run` 是全局开关，任何前端的心跳都能覆盖 | [server.py:346-360](../focused/server.py#L346-L360) + [engine.py:604](../focused/engine.py#L604) | 用户在看板设了 dry-run，插件下一轮心跳把它关掉 | **已修复（2026-10-17）**：`/focus/state` 忽略 `dry_run`；只有 `PATCH /config`（UI/CLI）能改 |
| BE-16 | P2 | observability | `degraded` 不进看板/doctor | `status` 有 [engine.py:1512](../focused/engine.py#L1512)，但 `ui.py` 与 `doctor-focused.sh` 都不读 | “扫描每秒崩溃但界面全绿”仍然存在 | **已修复（2026-10-17）**：看板 `healthBanner` 读取 `status.health`；doctor 第 8 步直接查 `health`/`degraded`，保留 journalctl 回退 |
| BE-17 | P2 | UX | `--once` 冻结后直接退出，只打印提示 | [focusedd.py:239-253](../focusedd.py#L239-L253) | 用户以为预演，结果进程留在冻结态 | **已修复（2026-10-17）**：`--once` 默认 thaw；`--keep-frozen` 显式保留 |
| BE-18 | P2 | semantics | `frozen_count` 是“记录里的 pid 数”，不是“此刻 T 状态进程数” | [freezer.py:271-288](../focused/core/freezer.py#L271-L288) | unit 模式新生成的进程不计、已退出进程仍计；DoD“= ps T 数量”只在 pid 模式成立 | **已修复（2026-10-17）**：`recorded_pids()` / `stopped_pids()` 分开；`frozen_count()` = 实际 T/t；unit 模式读取 cgroup 当前成员；status 增 `frozen_recorded_count` |
| BE-29 | P2 | perf | 保护名单匹配是 `O(进程数 × 规则数)` 的正则扫描；500 进程约 112 ms/scan | `.dshtmp/bench/synthetic_bench.py`、[rules.py](../focused/core/rules.py#L224) | 1s 间隔约 12% 单核；0.2s 间隔约 60%，不可用 | **已修复**：`core/matching.py` 精确名集合 + 通配符预编译；500 进程 `evaluate` 0.6 ms、总扫描 ~9.6 ms（267 项测试全绿） |
| BE-32 | P2 | design/security | 进程内插件拿到整个 engine，可直接调用冻结/解冻/内部状态 | 旧 `load_addons` 调用 `register(engine)` / `on_event(engine, event)` | 插件权限过大、接口不稳定、难以编写与测试 | **已修复（2026-10-17）**：`plugin_api.py` 窄接口（rules/session/status/log/events），输入校验、能力声明、注册期缓冲、失败隔离；失败/禁用/移除时清理规则与租约；`docs/plugin-api.md` + 示例 |
| BE-33 | P2 | protocol | 配置了应用 token 后，插件 token 无法读取 `/focus`、`/events`，与“插件可读状态”的文档不符 | 旧 `_authorised_for` 只允许 app token 访问非插件路径 | HTTP 插件无法按文档读取状态/事件 | **已修复（2026-10-17）**：`PluginRegistry.verify_any`；只对 `/focus`、`/events` 放行只读插件 token |
| BE-34 | P2 | security | `webhook_url` 未校验 scheme，可填 `file://` 或任意地址 | [server.py](../focused/server.py) 注册/更新 webhook | 插件可让后端向任意 URL 发事件（SSRF/本地文件尝试） | **已修复（2026-10-17）**：注册与更新都只接受 http(s) |
| BE-35 | P0 | bug（安全） | PID 规则没有 `pid_guards` 时 `evaluate` 仍返回 MATCH | [rules.py](../focused/core/rules.py) 的 pid 分支；`apply_rules` 接受裸 pids | 可能冻结一个被复用的 PID（陌生进程） | **已修复（2026-10-17）**：无 guard → `SKIP`；`apply_rules` 自动填 starttime guard，拒绝 gone/reused/pid≤1 |
| BE-36 | P2 | security | 插件能力由 manifest 自声明，宿主没有独立的 grant | `plugin_api.py` / `plugins.py` | 插件可自封 `process.control` 等危险能力 | **已修复（2026-10-17）**：`declared_capabilities` ∩ `granted_capabilities`；HTTP 注册只授 safe set；危险能力默认关闭 |
| BE-37 | P2 | design | 插件缺少规则 add/remove/pids、state、scan 接口 | 旧 `PluginAPI` / server 插件路由 | ChillFocused 无法自由管理名单、读状态、请求扫描 | **已修复（2026-10-17）**：`RuleSourceService` + 命名空间 API + `/rules`、`/rules/add`、`/rules/remove`、`/state`、`/scan` |
| BE-38 | P2 | safety/perf | 扫描请求无冷却，多插件可放大扫描工作 | `app/scan.py` | 插件可用 scan 请求制造 CPU 压力 | **已修复（2026-10-17）**：`ScanRequester` 全局 1s 冷却 |
| BE-39 | P2 | regression | config 中 `pids` 无 guard 时被静默 `SKIP`（安全正确，但功能静默失效） | config source 初始化 | 用户配置的 PID 冻结不生效 | **已修复（2026-10-17）**：`RuleSourceService` 启动时填 live guard；已消失的 PID 保留无 guard 并 warning |

| BE-42 | P2 | consistency | 配置更新非事务：`dry_run` 已改，随后 blacklist 拒绝 | `ConfigService.update`；`.dshtmp/audit/edge_cases.py` | 请求 400 但运行时配置已部分生效 | **已修复（2026-10-17）**：candidate Config + `RuleSourceService.preview`，全部验证后提交 |
| BE-43 | P2 | robustness | `scan_interval=nan/inf` 被接受 | `ConfigService.update`；`.dshtmp/audit/edge_cases.py` | nan 可忙循环，inf 可停止扫描 | **已修复（2026-10-17）**：`math.isfinite` + `[0.1,3600]`；`ScanService.run_forever` 防御回退 1s |
| BE-44 | P2 | correctness | 插件 `register()` 连续两次 `rules.add` 只保留最后一次 | `PluginAPI._pending_rules`；`.dshtmp/audit/edge_cases.py` | 插件规则静默丢失 | **已修复（2026-10-17）**：`_pending_rules_actions` 有序队列，绑定后按序 dispatch |
| BE-45 | P2 | validation | `url_patterns="ab"` 被拆成 `["a","b"]` | `RuleSourceService.replace`；`.dshtmp/audit/edge_cases.py` | URL 过滤被错误拆分 | **已修复（2026-10-17）**：`_urls()` 拒绝 str/bytes，序列逐项校验 |
| BE-46 | P2 | usability | source 含已消失 PID 时，add/remove name 被整体拒绝 | `RuleSourceService.replace` 全量重验；`.dshtmp/audit/edge_cases.py` | 插件无法编辑自己的名单 | **已修复（2026-10-17）**：已有 pid 保留 guard 不重读；只校验新 pid；新增 `preview()` |
| BE-47 | P2 | security | `frozen.json` 以 0644 落盘 | `Freezer._save`；`.dshtmp/audit/edge_cases.py` | 同机用户可读冻结进程信息 | **已修复（2026-10-17）**：替换前 chmod 0600 |
| BE-50 | P2 | security | 缺少 `Host` 头仍被接受 | `api/security.py` | HTTP/1.0 或无 Host 客户端绕过 loopback Host 校验 | **已修复（2026-10-17）**：无 Host → 400 |
| BE-51 | P2 | safety | 单字符 `cmdline_substring` 可近似 catch-all | `core/rules.py` | 插件可用 `"a"` 匹配大量进程 | **已修复（2026-10-17）**：blacklist cmdline 长度 <2 → `rule.cmdline-short` |
| BE-52 | P2 | capacity | 单 source 没有规则数量/大小上限 | `RuleSourceService` | 插件可提交极多规则，增加扫描成本 | **已修复（2026-10-17）**：单 source 上限 5000 条 |
| BE-53 | P2 | robustness | webhook 每个事件起一个线程 | `EventBus._post_webhook` | 高频事件可产生大量线程 | **已修复（2026-10-17）**：有界队列 100 + 单 daemon worker，满队列丢弃并告警 |
| BE-54 | P2 | safety | `Freezer._save` 失败只记日志，freeze 仍返回成功 | `core/freezer.py` | 崩溃后可能留下无记录的冻结 | **已修复（2026-10-17）**：保存失败先撤销；撤销失败保留内存记录并返回 entry |

### 2.3 P3

| ID | 级别 | 问题 | 证据 | 修复方向 |
|---|---|---|---|---|
| BE-19 | P3 | 手动 `/scan` 撞上正在扫描时静默返回 0 | [engine.py:850-874](../focused/engine.py#L850-L874) | 返回 `busy: true` 或等待 |
| BE-20 | P3 | 配置 API 类型校验不完整（`bool("false")`、`int`/`float` 混用） | [engine.py:1246-1296](../focused/engine.py#L1246-L1296) | 用显式 typed setter，统一在边界返回 400 |
| BE-21 | P3 | `__init__` 里 `_sources["config"]` 连续赋值两次 | [engine.py:137-160](../focused/engine.py#L137-L160) | 删除重复块 |
| BE-22 | P3 | `shadowed_names` / `pids_without_guard` / `adopt` / `engine.close` 无调用 | [rules.py:120-135](../focused/core/rules.py#L120-L135)、[plugins.py:197](../focused/plugins/registry.py#L197) | 接进配置校验，或删除 |
| BE-23 | P3 | 无租约的 claim（`ttl=0`）永不过期 | [engine.py:263-282](../focused/engine.py#L263-L282) | 默认要求 ttl；显式 `allow_permanent` 才允许 |
| BE-24 | P3 | `_freezable_pids` 文档说“never ... anything on the protect list”，实际只剔除自身/pid≤1 | [engine.py:820-843](../focused/engine.py#L820-L843) | 改文档，或对树成员再做一次 protect 判定 |
| BE-25 | P3 | 看板读取 `config.warnings`，但 `GET /config` 不返回它 | [ui.py:891](../focused/ui.py#L891) vs [engine.py:1329-1370](../focused/engine.py#L1329-L1370) | `config_dict` 带上 warnings，或 UI 不等它 |
| BE-26 | P3 | 文档测试数量/协议头/旧 `delegate` 配置过期 | `docs/focused-design.md`、`docs/focused-protocol.md` | 文档生成/校验，纳入测试 |
| BE-27 | P3 | 进程内插件 `register()` 可被 HTTP 注册伪造（见 BE-14） | 同上 | 同上 |
| BE-28 | P3 | `frozen.json` 0644，暴露用户应用名 | 现场权限 | **已修复（2026-10-17）**：与 BE-47 同修，替换前 chmod 0600 |
| BE-48 | P3 | `PluginAPI.pids` 静默丢弃 0/-1 | `plugin_api/validation.py`；`.dshtmp/audit/edge_cases.py` | **已修复（2026-10-17）**：pid<=0 抛 `PluginError("rules.pid")` |
| BE-55 | P3 | 重复 freeze 已有记录时可能重复累计 frozen/recent | `FreezeService.freeze_target` | **已修复（2026-10-17）**：已有记录直接 mark_handled + skip |

---



## 3. 插件 ChillFocused（PLG）

| ID | 级别 | 类型 | 问题 | 证据 | 影响 | 修复方向 |
|---|---|---|---|---|---|---|
| PLG-01 | P0 | bug | token 头不匹配（= X-01） | 同上 | 同上 | **已修复（2026-10-18）**：`X-Focused-Token`，测试覆盖 |
| PLG-02 | P1 | bug | 规则推送失败阻断心跳（= X-03） | 同上 | 同上 | **已修复（2026-10-18）**：规则/心跳独立 try；集成测试 |
| PLG-03 | P1 | design | 两条规则推送路径（= X-02） | 同上 | 同上 | **已修复（2026-10-18）**：只保留 HeadlessRunner/RuleSync；overlay 删除 PushRules |
| PLG-04 | P1 | design/doc | 没有 countup 分支；与 README/NEXT-SESSION 说法冲突（= X-04） | `SessionGate.Active`；`GameTimerProbe` 只读 `PomodoroService` | 正向计时可能被误冻 | **已修复（2026-10-18）**：`TimerMode` + countup 查询；未知模式 fail-closed |
| PLG-05 | P2 | UI 正确性 | HUD 的“已冻结”用累计值，面板用当前值 | [FocusRunner.cs:723](../../chillfocus/ChillFocused/Core/FocusRunner.cs#L723)（`frozen_total`）vs [FocusRunner.cs:734](../../chillfocus/ChillFocused/Core/FocusRunner.cs#L734)（`frozen_count`）；HUD 用在 282-284/303 | 同一标签两个含义，排查时误导 | **已修复（2026-10-18）**：HUD 显示当前冻结，另列累计处理 |
| PLG-06 | P2 | bug | 两个时钟基准混用 | `ReadAt` = Unity `unscaledTime`（[GameTimerProbe.cs:282](../../chillfocus/ChillFocused/Core/GameTimerProbe.cs#L282)）；`now` = runner 构造起的秒数（[HeadlessRunner.cs:322](../../chillfocus/ChillFocused/Core/HeadlessRunner.cs#L322)） | 新鲜度窗口被“进程启动→插件加载”的差值放大 | **已修复（2026-10-18）**：`GameClock` 共享单调时钟 |
| PLG-07 | P2 | observability | 后端 `degraded` 进不了插件 | `FocusStateResponse` 无该字段（[FocusedProtocol.cs:64-85](../../chillfocus/ChillFocused/Core/FocusedProtocol.cs#L64-L85)） | 扫描崩溃时 HUD 仍显示正常 | **已修复（2026-10-18）**：DTO 加字段，HUD 显示警告 |
| PLG-08 | P2 | dead feature | `GameProbe` 读 `CurrentMainState` 只写日志 | [Plugin.cs:459-469](../../chillfocus/ChillFocused/Plugin.cs#L459-L469) | 注释暗示“按模式判定”，实际不参与决策 | 删除或接进 `SessionGate` |
| PLG-09 | P3 | cleanup | 死代码/字段：`ReportSessionAsync`、`_frozenUnits`、`_connectionError`、`Heartbeat(force)` 的 `force`、`_freezeEnabled` 恒为 true | [FocusedClient.cs:101](../../chillfocus/ChillFocused/Core/FocusedClient.cs#L101)、[FocusRunner.cs:69](../../chillfocus/ChillFocused/Core/FocusRunner.cs#L69) | 认知负担；误导读者以为存在第二条心跳/字段有用 | **已修复（2026-10-18）**：删除未用方法/字段/镜像 heartbeat |
| PLG-10 | P3 | concurrency | `HeadlessRunner.SessionActive` 是普通静态 bool，后台写、主线程读 | [HeadlessRunner.cs:86](../../chillfocus/ChillFocused/Core/HeadlessRunner.cs#L86) | 理论可见性问题；实践通常无害 | **已修复（2026-10-18）**：删除静态状态；overlay 读后端 status |
| PLG-11 | P3 | security | `FocusedUrl` 不校验 loopback | `FocusSettings.Validate` 只查空 | 用户可把规则/状态发到远端；与后端“loopback only”的约束不对称 | **已修复（2026-10-18）**：`IsLoopbackUrl` 校验；非法 URL 回退 127.0.0.1 |
| PLG-12 | P3 | robustness | runner wiring 的 `RequestImmediateSync` 捕获 `_headless`，在 `StartHeadlessSync` 之前可能为 null | [Plugin.cs:524](../../chillfocus/ChillFocused/Plugin.cs#L524) vs 585 | 极端时序下 NRE，日志可见但功能降级 | **已修复（2026-10-18）**：调用前检查 `_headless`，null 时 no-op |
| PLG-13 | P3 | design | 两套 JSON 序列化（手工 `RuleJson` + `JsonUtility`） | [HeadlessRunner.cs:412](../../chillfocus/ChillFocused/Core/HeadlessRunner.cs#L412)、`FocusedClient` | 字段/转义规则可能分叉 | 统一一个 wire serializer port |
| PLG-14 | P3 | UX | Overlay 的 `_connected` 与 reporter 的连接状态是两份 | `FocusRunner.MarkConnected` vs `HeadlessRunner` | 一条路径卡住时 HUD 可能说“未连接”而实际在冻结 | 单一 `StatusSnapshot` |
| PLG-15 | P2 | feature gap | “防止关闭游戏/停止专注”未实现 | 插件对计时器方法只打 postfix 观察 | 与文档 §7#5 一致；如果仍是目标，需要单独模块 | 在明确产品决定后，按 `QuitGate`/`TimerLock` 独立模块实现，保留逃生口 |
| PLG-16 | P3 | UX | 重复安装只警告，不阻止 | `PluginInstallCheck` | 两份插件同时运行，双 HUD/双轮询 | 检测到重复时禁用自身或醒目提示 |

---

## 4. 必须保留的好设计（重构时不要弄丢）

这些是当前架构里已经被验证有效的部分，重构应保留并加固：

1. **冻结而非终止**：`SIGSTOP/SIGCONT` 与 `cgroup.freeze`，任何时候都能恢复。
2. **租约而不是布尔**：前端崩溃/被杀/被冻后自动收手；`fail-open` 是安全属性。
3. **状态文件先写**：`frozen.json` 是崩溃恢复的唯一凭据；`ExecStopPost=--thaw-all`。
4. **保护名单只增不减**：任何前端都不能削弱内置 446 条护栏。
5. **规则按来源并集**：插件撤下自己的规则不影响别人。
6. **纯判定与平台分离**：`rules.py`、`cgroupfs.py`、`SessionGate.cs` 可以脱离游戏/系统单测。
7. **扁平 `/focus` DTO**：Unity `JsonUtility` 的硬约束，不要改成嵌套。
8. **测试金字塔已有基础**：412 项后端 + 272 项插件 + 真实进程 e2e；重构要保住这些测试的语义。
9. **`focused-doctor` 的思路**：把“静默失败”变成一条条可执行的检查。

---

## 5. 回归测试清单（每修一项就加一条）

| 问题 | 回归测试 |
|---|---|
| X-01 | 插件 `HttpTransport` 对 stub server 断言 `X-Focused-Token`；后端 `test_protocol` 断言只认该头 |
| X-02 | 只允许一个 reporter 发送规则/心跳；overlay 发出规则请求即为失败 |
| X-03 | 让 `PUT rules` 返回 400，断言 `POST state` 仍然发出 |
| X-04 | 共享 `session-cases.json`：两端对同一组 timer 快照得到同样的 `active/phase/reason` |
| BE-01 | PATCH 黑名单 → `Config.load()` 重新读 → 仍在 |
| BE-02 | 连续两次 `--add-name`；先加 name 再加 cmdline；断言两者都在 |
| BE-03 | `names=["*"]` → API 400 / 配置加载失败；`evaluate` 层也拒绝 |
| BE-04 | 带 token 启动，`GET /` 可加载且 JS 能带 token 调 API |
| BE-05 | 冻结一条记录 → `freeze.enabled=false` → 记录消失 |
| BE-06 | 连续 10 次心跳 → 0 条 audit、0 条 info（只有第一次/相位变化记账） |
| BE-07 | 带 `Origin: https://evil` 或非 loopback `Host` 的请求被拒绝 |
| BE-08 | 新建 config/plugins → 权限 0600 |
| BE-10 | `systemctl thaw` 返回 0 但 `cgroup.events` 仍 frozen=1 → 记录保留 |
| BE-11 | 冻结 → thaw → 立即新会话 → 目标在下一秒内重新冻结 |
| PLG-05 | HUD 文案测试：当前冻结 1 条、累计 5 条时显示“1”而不是“5” |
| PLG-06 | 用假时钟把 `now` 与 `read_at` 拉到同一基准，断言窗口精确 25s |

---

## 6. 复现脚本

已确认的问题都有可重复执行的脚本，放在 `.dshtmp/repro/`，使用 `.dshtmp/tmp/` 下的临时目录，不改系统状态：

```bash
python3 .dshtmp/repro/repro_backend_issues.py
```

当前输出（2026-10-17）：

```
X-01 plugin sends=X-ChillFocused-Token backend reads=X-Focused-Token -> MISMATCH
BE-01 in-memory=['firefox'] on-disk=[] -> LOST
BE-02 after two adds: ['chromium'] (expected both)
BE-02 after cmdline-only patch: names=[] (expected firefox/chromium kept)
BE-03 names=['*'] -> match (blacklist.name:*)
BE-04 GET / without token -> 200; GET /api/v1/status without token -> 401, with token -> 200
BE-05 entries before=[4242] after=[4242] -> STILL FROZEN
BE-06 5 heartbeats -> log=5 audit=5 registry=True
```

---

## 7. 备注

- 本文件只记录**已确认**的问题；不确定的猜测写在“待验证”里，不要混进主表。
- 每条修复都应同时更新本表状态（`open` → `fixed`）并链接提交/测试。
- 如果重构改变了某个问题的根因（例如移除 `config.rules` 双表示），在条目里写“由架构变更消除”，而不是简单标 fixed。
