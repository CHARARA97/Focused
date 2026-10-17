# domain — 纯决策值

- `claims.py` — 租约 claim 的过期/续租/最近 deadline。
- `health.py` — scan failures、recovery failures、degraded。
- `safety.py` — 最后一次安全闸门：unknown uid/starttime、slice/session-manager、shared unit、empty tree 全部拒绝。

这些模块尽量无 I/O、可单测。安全规则是 fail-closed：不能证明安全就拒绝。测试见 `tests/test_claims.py`、`test_health.py`、`test_safety.py`。
