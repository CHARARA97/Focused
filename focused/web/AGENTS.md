# web — 看板

一个页面、无构建步骤、无外部资源。按 tab 拆开，装配在 `__init__.py::render()`。

| 文件 | 内容 |
|---|---|
| `text.py` | **所有用户可见文案**，一个 key 一条 |
| `style.py` | 样式表 |
| `shell.py` | `<head>`、页头、导航栏 |
| `script.py` | 共享 JS（`state`、`api`、列表渲染、`refreshAll`、启动代码） |
| `tabs/<name>.py` | 每个 tab 的 `OPENING` / `SECTION` / `SCRIPT` |

文案键是「模块.角色.序号」：`overview.button.3`、`apps.placeholder.1`、`shared.toast.2`。
改文案只改 `text.py` 的值；键不变。

两种引用：HTML 里写 `[[overview.heading.1]]`，渲染时替换；JS 里写 `T('overview.toast.2')`，
页面启动时注入 `TEXT` 与 `T()`。

时钟：`renderSessionClock` 从绝对时间（`until` / `phase_ends_at` / `started_at`）计算剩余，
`tickClock` 每秒调用一次，所以读秒与秒同步；数据仍每 3 秒刷新一次。

纪律：tab 模块里不出现中文，缺 key 会让渲染抛错（`tests/test_ui_text.py` 强制）。
