# 后端测试

```bash
cd focused
python3 -m pytest -q
```

- 引擎/真实冻结：`test_focused_engine.py`、`test_frontend_e2e.py`。
- 规则/匹配：`test_rules.py`、`test_matching.py`、`test_rule_source.py`。
- Freezer/恢复：`test_freezer.py`、`test_freeze_service_edges.py`。
- claim/会话：`test_claims.py`、`test_session_service.py`。
- 插件/API：`test_plugin_api.py`、`test_focused_plugins.py`、`test_plugin_gateway.py`、`test_plugin_runtime.py`。
- HTTP/安全：`test_focused_protocol.py`、`test_api_security.py`、`test_file_security.py`。
- 配置：`test_config_service_edges.py`。
- UI/doctor：`test_ui_consistency.py`、`test_dashboard_script.py`、`test_doctor_health.py`。
- 跨仓库契约：`test_plugin_contract.py`（需要同工作区的 `chillfocus` 源码；不在时 skip）。

失败路径必须加测试；不要 mock 掉安全闸门。
