# app — 用例、所有者与组合

每个服务只有一个职责，engine 只委托：

| 模块 | 所有者 |
|---|---|
| `rules.py` | 规则来源、合并、版本号 |
| `rule_entries.py` | 校验并准备一个来源条目：类型、上限、PID guard |
| `rule_merge.py` | 纯合并：多来源取并集、保护项、URL 顺序、guard |
| `session.py` | 前端租约、放行窗口、与计时器的组合 |
| `manual_session.py` | 计时器的所有者：持有 `SessionState`，应用纯转移并落审计 |
| `scan.py` | 扫描循环、扫描锁、`Intent`/`Policy` 构造、采样、`ScanRequester` |
| `tick.py` | 一轮流水线 `run_tick`：遍历 → 判定 → 执行 → 报告 |
| `targeting.py` | 把一个进程解析成 `Candidate`（树 + 事实 + 预检上下文） |
| `enforcer.py` | 单目标执行：kill/audit、episode、失败上报 |
| `reporting.py` | 审计 + 事件总线 + journal + 计数 + 健康的唯一出口 |
| `timing.py` | `Timeline`：墙钟与单调钟，扫描计时都用它 |
| `runtime_info.py` | `RuntimeInfo`：pid、版本、addons、事件序号 |
| `history.py` | `EpisodeBook` / `DenyBook`：窗口聚合（观察性，不参与决策） |
| `health.py` | 扫描失败、杀不掉、重生风暴 |
| `claims.py` | 租约的过期、续租、最近 deadline |
| `state.py` | 插件只读快照 |
| `status.py` | status/focus 视图 |
| `config_view.py` | `GET /config` 的响应形状 |
| `plugin_view.py` | 插件列表的响应形状 |
| `process_view.py` | 进程选择器的聚合与图标 |
| `events.py` | 事件 ring、分发、webhook 队列 |
| `config.py` | 配置更新事务 |
| `plugin_gateway.py` | 能力校验、所有权、审计、限流 |
| `plugin_runtime.py` | 插件加载/失败/清理/启停 |

边界：

- 这里不发信号；只能经 `Enforcer → Killer`。
- 这里不解析 HTTP；API 层负责协议。
- 这里不 import `api/` 或 `engine`；依赖方向由 `tests/test_architecture.py` 强制。
- 新业务进对应服务，不要塞回 engine。

测试：`tests/test_enforcer.py`（单目标）、`test_tick.py`（一轮）、`test_manual_session.py`（纯转移）。
