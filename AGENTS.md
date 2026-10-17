# focused 后端

独立的专注应用：规则匹配、冻结/恢复、看板、CLI、HTTP API、插件平台。

- 包分层：[focused/AGENTS.md](focused/AGENTS.md)
- 测试：[tests/AGENTS.md](tests/AGENTS.md)
- 文档：[docs/AGENTS.md](docs/AGENTS.md)
- 脚本：[scripts/AGENTS.md](scripts/AGENTS.md)
- 示例：[examples/AGENTS.md](examples/AGENTS.md)
- 打包：[packaging/AGENTS.md](packaging/AGENTS.md)

入口：`focusedd.py`（CLI/daemon）、`focused/server.py`（HTTP）、`focused/engine.py`（组合根）。

```bash
# 安装后的命令；源码树里是 python3 focusedd.py
focused --help       # 旧名 focusedd 仍兼容
python3 -m pytest -q
```

不要因为“顺手”把业务逻辑写回 `engine.py`；先看 `docs/backend-refactor-plan.md`。
