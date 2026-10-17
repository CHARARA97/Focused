# plugin_api — 进程内插件契约

| 模块 | 职责 |
|---|---|
| `api.py` | `PluginAPI` 身份、注册缓冲、宿主委托 |
| `namespaces.py` | `api.rules/session/state/scan/events` |
| `capabilities.py` | 能力常量、别名、`normalize_capabilities` |
| `manifest.py` | `PluginManifest` |
| `validation.py` | 规则输入校验（names/cmdline/pids） |
| `errors.py` | `PluginError` |

边界：

- 插件拿不到 engine/freezer；
- 授权在 `app/plugin_gateway.py`，这里只做形状校验和缓冲；
- 危险能力（`process.control`、`focus.override`）默认不授予；
- 新增 API 方法先加能力、namespace、测试和 `docs/plugin-api.md`。
