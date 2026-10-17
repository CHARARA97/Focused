# api — HTTP 适配层

| 模块 | 职责 |
|---|---|
| `http.py` | 常量、ApiError、DTO/参数、插件 payload |
| `auth.py` | 应用 token / 插件 token / 能力检查 |
| `security.py` | Host/Origin/Content-Type 校验 |
| `read_routes.py` | GET 与旧 `/focus/rules`、PATCH config |
| `session_routes.py` | focus state/session/pause、scan、thaw |
| `core_routes.py` | POST config/urls，分派 session |
| `plugin_routes.py` | 插件注册与 `/plugins/{id}/*` |

新增端点：

1. 先判断属于 read/session/core/plugin；
2. 放进对应 routes，返回 `(status, payload)`；
3. 鉴权和 transport 由 handler 统一做，不在这里重复；
4. handler 只负责响应。

这里不写业务逻辑；调用 `engine`/`plugin_gateway`/`app` 服务。
