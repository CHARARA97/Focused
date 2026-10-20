# focused 包

分层地图（从上到下）：

- [domain/](domain/AGENTS.md) — 纯值与纯转移：intent、identity、tick、policy、targets、preflight、session_timer、reasons。
- [core/](core/AGENTS.md) — OS 适配与判定：/proc、信号、规则求值、PID guard。
- [app/](app/AGENTS.md) — 用例与状态所有者：规则、会话、扫描、执行、事件、配置、插件网关。
- [api/](api/AGENTS.md) — HTTP 路由、鉴权、传输安全。
- [plugins/](plugins/AGENTS.md) — 插件注册表与加载器。
- [plugin_api/](plugin_api/AGENTS.md) — 进程内插件契约。
- [web/](web/AGENTS.md) — 看板：按 tab 拆分的页面、共享脚本、文案目录。

顶层文件：

- `engine.py` — 只做组合、兼容委托；不是新逻辑的落点。
- `server.py` — HTTP handler；路由在 `api/`。
- `config.py` — 配置模型与校验；`config_io.py` 路径与读写、`config_parse.py` 类型读取、`config_errors.py` 错误类型。
- `ui.py` — 只做装配：`DASHBOARD_HTML = web.render()`。
- `diagnostics.py` — 结构化日志 `event=...`。

依赖尽量向下：`api/app` 不反向依赖 `server`；`core` 不 import `app/api`；插件只能经 `plugin_api → PluginGateway`。

文档与契约：`../docs/`。
