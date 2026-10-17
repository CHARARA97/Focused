# plugins — 插件持久化与加载

- `registry.py` — `PluginRecord`、token、能力、`plugins.json`、`PluginRegistry`。
- `loader.py` — 进程内 Python 模块加载、`load_addons`、失败清理。
- `__init__.py` — 兼容 re-export；`from focused.plugins import ...` 不变。

边界：

- registry 管“谁被允许做什么”，不管模块运行；
- loader 管模块运行，不直接改规则；
- HTTP 注册只允许 `kind=http`；
- 插件 token 只在注册响应出现。

改这里先看 [../plugin_api/AGENTS.md](../plugin_api/AGENTS.md) 和 [../../docs/plugin-api.md](../../docs/plugin-api.md)。
