# Focused —— 独立的专注应用

> 一句话：**它就是那个"发现打扰你的应用，然后把它挂起来"的本体程序**，
> 可以完全脱离游戏和浏览器单独使用，也对外开放接口让你（或别人）写小插件接进来。

当前形态（[设计文档见 focused-design.md](focused-design.md)）：

```
        ┌── ChillFocused（游戏插件，BepInEx mod）──┐
        │                                          │
浏览器扩展 Focused ─────────────────────►  Focused（后端应用）
        │                                          │
        └── 你自己的小插件（任何语言 / HTTP）──────┘
                                                   │
                              看板 http://127.0.0.1:8766/ · CLI focusedd
```

Focused 自己就是完整应用：选名单、开始专注、挂起、恢复、看日志、管插件，全都自带。

---

## 1. 安装与启动

```bash
scripts/install-focused.sh --start     # 装到 ~/.local/share/focused/app 并启动 user service
systemctl --user status focused        # 看状态
```

- **看板**：<http://127.0.0.1:8766/>（概览 / 应用名单 / 网址名单 / 插件 / 设置 / 日志）
- 配置：`~/.config/focused/config.json`（**黑名单默认为空**，装完不会冻任何东西）
- 进程内插件目录：`~/.config/focused/plugins/*.py`
- 数据：`~/.local/share/focused/`（`frozen.json` 冻结记录、`plugins.json` 插件注册表、`audit.jsonl` 审计）

## 2. 看板（不用终端也能用全部功能）

| 页面 | 能做什么 |
|---|---|
| **概览** | **专注会话面板**（三种模式、自定义时长、预设、正在跑的表盘、+5 分钟 / 跳过休息 / 放行 / 结束）、谁开的会话（自己 / 哪个插件）、已冻结列表、运行环境自检、最近事件 |
| **应用名单** | 从**本机正在运行的进程**里勾选（可搜索、受保护进程灰显），或手输通配符；按启动参数匹配；保护名单只增不减 |
| **网址名单** | 发布给浏览器插件读取的名单（Focused 自己不会关标签页） |
| **插件** | 每个已连接插件的最后联系时间、贡献规则数、是否正在认领会话、错误数；可禁用/移除；一键注册新插件并显示令牌与端点 |
| **设置** | 演练模式、**默认模式与各种时长**（倒计时时长、番茄钟专注/休息/轮数、放行时长）、扫描间隔、HTTP 令牌；保存即写 `config.json`（需要重启的项会明确提示） |
| **日志** | 事件流（可过滤、自动跟随） |

顶栏常驻：状态灯 + 当前状态 + **开始 25 分钟 / 自定义 / 放行 10 分钟 / 结束专注 / 全部恢复**。

## 3. 专注会话的三种模式

| 模式 | 行为 | 适合 |
|---|---|---|
| **番茄钟** | 专注 / 休息交替，**只有专注阶段是 active**：进休息时应用立刻解冻，休息结束自动重新冻结；**轮数 1 = 只做一轮，等同于单次倒计时**；轮数 0 = 一直循环；可"跳过休息" | 标准的 25/5 节奏，或"我再写 25 分钟" |
| **正向计时** | 从 0 开始一直走，不设终点，自己按"结束专注"才停 | "我写到累为止" |

> 顶栏按钮的数值**直接来自设置**：按钮上写着"番茄钟 50 分钟 · 3 轮"，就一定会按 50/10 ×3 开始；
> 改了设置，按钮文案跟着改，不需要重新记数字。

时长**全部可自定义**：看板上直接填（带 15/25/45/60/90 预设），或写进 `settings`/`config.json`
的 `session`（`mode` / `default_minutes` / `pomodoro_work_minutes` / `pomodoro_break_minutes` /
`pomodoro_cycles`），启动时不带参数就用这些默认值。

两个细节：

- **放行不吃专注时间**：点「放行 5 分钟」时会把当前阶段的截止时间往后推 5 分钟——
  休息是你的，不该从专注里扣。
- **休息 = 解冻**：番茄钟的休息阶段 `active` 为 false，所以被挂起的应用会回来、
  浏览器扩展也会放行标签页；下一轮开始再冻上。

API：

```bash
# 开始（模式 + 各自的时长）
curl -X POST .../api/v1/focus/session -d '{"mode":"pomodoro","work_minutes":50,"break_minutes":10,"cycles":3}'
curl -X POST .../api/v1/focus/session -d '{"mode":"stopwatch"}'
curl -X POST .../api/v1/focus/session -d '{"mode":"countdown","minutes":45}'
# 运行中
curl -X POST .../api/v1/focus/session -d '{"extend_minutes":5}'   # 加时间
curl -X POST .../api/v1/focus/session -d '{"skip_break":true}'    # 跳过休息
curl -X POST .../api/v1/focus/session -d '{"stop":true}'          # 结束
```

`GET /api/v1/focus` 会带上扁平的会话字段：`session_mode` / `session_phase` /
`session_started_at` / `session_until` / `session_phase_ends_at` / `session_cycle` /
`session_cycles` / `session_work_minutes` / `session_break_minutes` /
`session_elapsed` / `session_remaining`（游戏插件也能直接读）。

## 4. 命令行

```bash
focusedd --session 25              # 就地做一轮 25 分钟，结束后自动恢复并退出
focusedd                           # 常驻，由看板 / 插件 / 系统会话驱动
focusedd --once [--dry-run]        # 跑一次扫描（预演）
focusedd --status / --frozen       # 问正在运行的实例 / 现在冻着什么（不依赖实例）
focusedd --thaw-all                # 全部恢复（幂等；systemd ExecStopPost 就是跑这个）
focusedd --add-name firefox        # 追加黑名单（脚本友好）
focusedd --add-url reddit.com      # 追加网址名单
kill -USR1 <pid>                   # 紧急全部恢复
```

## 5. API

自带前端与第三方插件用的是**同一套**接口；鉴权走 `X-Focused-Token`（配置了 `http.token` 时必需）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 看板 |
| GET | `/api/v1/focus` | **前端读的扁平状态**：`active` / `pause_until` / `url_patterns` / `frozen_count` / `delegated_source` … |
| GET | `/api/v1/status` | 完整状态（冻结列表、来源、插件、claim、统计） |
| GET | `/api/v1/focus/frozen` | 当前冻结列表 |
| PUT | `/api/v1/focus/rules` | 内置来源的规则（自带前端用） |
| POST | `/api/v1/focus/state` | 内置来源的会话 claim（可带 `ttl_seconds`） |
| POST | `/api/v1/focus/session` | 开始（`mode` = `pomodoro` / `stopwatch`，或脚本用的 `countdown` 单轮）+ `minutes` / `work_minutes` / `break_minutes` / `cycles`，以及 `extend_minutes` / `skip_break` / `stop` |
| POST | `/api/v1/focus/pause` · `/scan` · `/thaw` | 放行 / 立即扫描 / 全部恢复 |
| GET/PATCH | `/api/v1/config` | 读 / 局部改配置（原子保存） |
| GET | `/api/v1/processes` | 运行中进程（并行基础数组 + 对象数组） |
| GET | `/api/v1/events?since=N` | 事件流（`seq` 单调递增） |
| GET | `/api/v1/plugins` | 已连接插件列表 |
| POST | `/api/v1/plugins/register` | 注册插件 → `id` + 令牌 + 端点清单 |
| PUT | `/api/v1/plugins/{id}/rules` | 该插件贡献规则（**并集**，互不覆盖） |
| POST | `/api/v1/plugins/{id}/session` | 该插件认领会话（`{"active":true,"ttl_seconds":30}`） |
| POST | `/api/v1/plugins/{id}/enable` · `/remove` · `/webhook` | 启用/禁用、移除、设置事件回调 |
| GET | `/api/v1/plugins/{id}` | 单个插件详情 |

### 多来源合并（关键）

- **黑名单 = 并集**：每个来源各自持有一份，谁也不覆盖谁；想撤只撤自己那份。
- **保护名单 = 并集且只增**：内置护栏 + `protect_names.txt` 永远在，任何来源都削弱不了。
- **会话 = claim 模型**：`active = 自己起的会话 ∨ 任一未过期租约`。任一 claim 活着就冻结；全部过期即自动收手、全部解冻。
- **网址名单 = 并集**：浏览器插件去重后使用。

## 6. 写一个小插件

```bash
# 1) 注册（看板「插件」页也能点），拿到 id 与令牌
curl -s -X POST http://127.0.0.1:8766/api/v1/plugins/register \
     -H 'Content-Type: application/json' -d '{"name":"pomodoro"}' | jq

# 2) 贡献规则（只影响你自己那份）
curl -s -X PUT http://127.0.0.1:8766/api/v1/plugins/pomodoro/rules \
     -H "X-Focused-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"names":["firefox"],"cmdline_substrings":["--distract"],"url_patterns":["reddit.com"]}'

# 3) 认领一段专注（30 秒租约，记得续期；不续期就自动解冻）
curl -s -X POST http://127.0.0.1:8766/api/v1/plugins/pomodoro/session \
     -H "X-Focused-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"active":true,"ttl_seconds":30}'

# 4) 读状态与事件
curl -s http://127.0.0.1:8766/api/v1/focus
curl -s 'http://127.0.0.1:8766/api/v1/events?since=0'
```

**必须续期**：`ttl_seconds` 到了而没人续，Focused 会把这个 claim 当成"会话结束"→ 全部解冻。
这是刻意的：插件会崩、会被杀、会被系统冻住，应用不能因此永远挂着。

### 进程内 Python 插件（可选，能力更深）

放在 `~/.config/focused/plugins/xxx.py`：

```python
def register(focused):
    return {"name": "stats", "version": "0.1.0"}   # 可用 id/version 覆盖

def on_event(focused, event):                      # 每次事件回调，别阻塞
    focused.seen = getattr(focused, "seen", 0) + 1
```

会收到的事件：`state`、`session-started`、`freeze`、`thaw`、`rules`、`lease-expired`、`plugin`、`error`、`config`。
**隔离**：加载失败或反复抛异常 → 记事件、标记 failed、连续 5 次失败自动禁用，绝不影响主循环。
进程内插件与 Focused 同权限（完全信任）；不信任的请写 HTTP 插件。

## 7. 保护性兜底（为什么它不会把你的应用冻丢）

| 场景 | 兜底 |
|---|---|
| 专注结束 / 手动结束 | 立刻全部解冻 |
| 插件的租约过期 | 当成会话结束，立刻解冻并记审计 |
| `focusedd` 被 `SIGKILL` | systemd `ExecStopPost` 跑 `--thaw-all`（主进程已被杀也能跑） |
| 机器重启 / 下次启动 | 启动时按 `frozen.json` 把上一轮遗留全部解冻 |
| 规则变了、目标不再命中 | 立刻解冻它 |
| PID 被复用 | 逐 PID 校验 `starttime`，绝不对陌生进程发 `SIGCONT` |
| 冻结没被内核确认 | 回滚解冻并记审计，不假装成功 |
| 自己 / 自己的 cgroup | 绝不冻结（保护名单 + cgroup 自保护） |
| 单次扫描太多 | `freeze.max_per_scan` 上限 |
| 插件刷错误 | 记事件、计数、连续 5 次失败自动禁用 |

## 8. 测试

```bash
cd focused && PYTHONPATH=. python3 -m unittest discover -s tests -t .
```

覆盖：引擎（真进程冻结/解冻、cgroup 规则、PID 复用、遗留恢复）、会话与租约、**多来源规则合并**、
**插件注册/令牌/命名空间规则/会话 claim/禁用/移除**、配置 API、进程列表、事件流、进程内插件
（正常加载 / 加载失败隔离 / 事件抛异常自动禁用）、协议全端点与鉴权、看板冒烟。
