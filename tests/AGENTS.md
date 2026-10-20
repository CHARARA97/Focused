# 后端测试

```bash
cd focused
python3 -m pytest -q
```

按层组织，越靠下越便宜：

- 纯决策（毫秒级，无 I/O）：`test_intent.py`、`test_targets.py`、`test_preflight.py`、`test_reasons.py`、`test_manual_session.py`、`test_guards.py`。
- OS 适配（真 `/proc`、真信号）：`test_procfs.py`、`test_killer.py`、`test_proctree.py`。
- 组合层（4 个 fake）：`test_enforcer.py`（单目标）、`test_tick.py`（一轮流水线）、`test_history.py`。
- 引擎用例（配置/会话/规则/PID guard）：`test_focused_engine.py`。
- 端到端（真进程、真守护进程）：`test_lifecycle.py`。
- 规则/匹配：`test_rules.py`、`test_matching.py`、`test_rule_source.py`。
- claim/会话：`test_claims.py`、`test_session_service.py`。
- 插件/API：`test_plugin_api.py`、`test_focused_plugins.py`、`test_plugin_gateway.py`、`test_plugin_runtime.py`。
- HTTP/安全：`test_focused_protocol.py`、`test_api_security.py`、`test_file_security.py`。
- 配置：`test_config_service_edges.py`。
- UI/doctor：`test_ui_consistency.py`、`test_dashboard_script.py`、`test_doctor_health.py`。
- 跨仓库契约：`test_plugin_contract.py`（需要同工作区的 `chillfocus` 源码；不在时 skip）。
- 打包/CLI/安装：`test_arch_packaging.py`、`test_cli_help.py`、`test_cli_install.py`。
- 架构纪律：`test_architecture.py`（依赖方向、domain 全部 frozen、domain 无 IO）。

失败路径必须加测试；不要 mock 掉安全闸门。
端到端只留少量，其余行为用 `test_enforcer.py` 的 fake 覆盖——真进程会让测试慢且脆。
