# app — 用例与状态所有者

每个服务只有一个职责，engine 只委托：

| 模块 | 所有者 |
|---|---|
| `rules.py` | 规则源、合并、PID guard、preview |
| `session.py` | ClaimBook、手动会话状态机 |
| `scan.py` | 扫描循环、扫描锁、延迟采样、ScanRequester |
| `freeze.py` | freeze/thaw 编排、恢复、handled、safety 调用 |
| `state.py` | 只读状态快照 |
| `status.py` | status/focus/config 视图、进程列表 |
| `events.py` | 事件 ring、分发、webhook 队列 |
| `config.py` | 配置更新事务 |
| `plugin_gateway.py` | 能力校验、所有权、审计、限流 |
| `plugin_runtime.py` | 插件加载/失败/清理/启停 |

边界：

- 这里不直接发 `SIGSTOP/SIGCONT`；只能经 `FreezeService → Freezer`。
- 这里不解析 HTTP；API 层负责协议。
- 新业务进对应服务，不要塞回 engine。

测试：`tests/test_*service*`、`test_plugin_gateway.py`、`test_event_bus.py`、`test_config_service_edges.py`。
