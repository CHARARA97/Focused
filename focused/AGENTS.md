# focused 包

分层地图（从上到下）：

- [domain/](domain/AGENTS.md) — 纯决策值：claims、health、safety。
- [core/](core/AGENTS.md) — OS/进程/cgroup/规则/Freezer 适配；危险层。
- [app/](app/AGENTS.md) — 用例与状态所有者：规则、会话、扫描、冻结、事件、配置、插件网关。
- [api/](api/AGENTS.md) — HTTP 路由、鉴权、传输安全。
- [plugins/](plugins/AGENTS.md) — 插件注册表与加载器。
- [plugin_api/](plugin_api/AGENTS.md) — 进程内插件契约。

顶层文件：

- `engine.py` — 只做组合、兼容委托；不是新逻辑的落点。
- `server.py` — HTTP handler；路由在 `api/`。
- `config.py` — 配置模型与合法性。
- `ui.py` — 内嵌看板。
- `diagnostics.py` — 结构化日志 `event=...`。

依赖尽量向下：`api/app` 不反向依赖 `server`；`core` 不 import `app/api`；插件只能经 `plugin_api → PluginGateway`。

文档与契约：`../docs/`。
