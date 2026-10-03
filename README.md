# Focused

**专注时把打扰你的应用挂起来，结束后原样恢复。**

Focused 是一个独立的后端应用：它读 `/proc`、按规则查找应用、把它们**冻结**（`SIGSTOP` 或
cgroup v2 的 `cgroup.freeze`），并在专注结束时**原样恢复**。它不依赖游戏，也不需要浏览器
—— 自带看板、命令行和 HTTP API，两个前端（游戏插件、浏览器扩展）都连它：

| 组件 | 仓库 |
|---|---|
| 后端应用（本仓库） | `focused` |
| 游戏插件（《放松时光：与你共享 Lo-Fi 故事》的 BepInEx Mod） | [chillfocused](https://github.com/CHARARA97/ChillFocused-Linux) |
| 浏览器扩展（专注期间关标签页） | [focused-extension](https://github.com/CHARARA97/Focused-Extension) |

> **动作只有一个：冻结。** 没有 kill 路径，没有"结束进程"开关；任何异常路径都会解冻
> （租约到期、进程被杀、机器重启）。

---

## 目录

- [安装](#安装)
- [三分钟上手](#三分钟上手)
- [专注会话的两种模式](#专注会话的两种模式)
- [看板](#看板)
- [配置](#配置)
- [写一个插件](#写一个插件)
- [安全模型](#安全模型)
- [命令行](#命令行)
- [API](#api)
- [开发](#开发)
- [许可](#许可)

---

## 安装

### Arch Linux（AUR）

```bash
yay -S focused            # 或 focused-git 跟踪 main 分支
systemctl --user enable --now focused
xdg-open http://127.0.0.1:8766/
```

### 其他发行版

```bash
# 一行安装：下载 Release 里的 wheel，建 venv，写启动器与 systemd user 单元
curl -fsSL https://github.com/CHARARA97/Focused/releases/latest/download/install.sh | sh
```

或者自己来（纯标准库，无运行时依赖）：

```bash
uv tool install "focusd @ https://github.com/CHARARA97/Focused/releases/latest/download/focusd-0.1.0-py3-none-any.whl"
# 或从源码： uv tool install git+https://github.com/CHARARA97/Focused
focusedd --write-default-config ~/.config/focused/config.json
focusedd
```

系统要求：Linux（用到 `/proc`、cgroup v2 与 `SIGSTOP`）、Python ≥ 3.9、systemd 用户会话。
不需要 root。

## 三分钟上手

1. 打开 <http://127.0.0.1:8766/>
2. 「应用名单」里搜索并勾选你不想被打扰时打开的应用
3. 顶栏点「番茄钟 25 分钟 · 4 轮」或「正向计时」
4. 「概览」出现已冻结列表即为生效；「结束专注」或「立即恢复全部」随时放人

想先确认自己不会误冻：在「设置」里打开**只记录，不冻结**跑一轮，看「日志」命中了谁。

## 专注会话的两种模式

| 模式 | 行为 |
|---|---|
| **番茄钟** | 专注 / 休息交替；**只有专注阶段是生效的** —— 进休息时应用立刻恢复，休息结束自动重新冻上。轮数 1 = 只做一轮（等同于单次倒计时）；轮数 0 = 一直循环；可「跳过休息」 |
| **正向计时** | 从 0 开始一直走，不设终点，自己按「结束专注」才停 |

时长全部可自定义：看板上直接填（带 15/25/45/60/90 预设），或写进 `config.json` 的 `session`。
顶栏按钮显示的数字**就是它将要使用的数字**（改了设置文案跟着变）。

两个细节：

- **放行不吃专注时间**：点「放行 10 分钟」会把当前阶段截止时间往后推 10 分钟。
- **休息 = 恢复**：番茄钟休息阶段 `active` 为 false，所以应用与浏览器标签页都会放行。

## 看板

<http://127.0.0.1:8766/> —— 六个页面：

| 页面 | 作用 |
|---|---|
| 概览 | 专注会话面板、谁开的会话、已冻结列表、运行环境自检、事件记录 |
| 应用名单 | **屏蔽模式**（从运行中的进程点选要冻结的）+ **保护模式**（管理永不被冻结的） |
| 网址名单 | 发布给浏览器扩展读取的名单（Focused 自己不碰浏览器） |
| 插件 | 已连接插件、最后心跳、贡献规则数、是否在认领会话；可禁用/移除；一键注册新插件 |
| 设置 | 模式与时长、放行时长、扫描间隔、HTTP 令牌；保存即写 `config.json` |
| 日志 | 事件流（冻结 / 恢复 / 会话 / 插件 / 错误） |

## 配置

`~/.config/focused/config.json`（首次运行生成；看板「设置」页改的就是它）：

```jsonc
{
  "http": { "enabled": true, "host": "127.0.0.1", "port": 8766, "token": "" },
  "scan_interval": 1.0,
  "dry_run": false,                    // true = 只记录，不冻结
  "blacklist": { "names": ["firefox"], "cmdline_substrings": [], "pids": [] },
  "protect":   { "names": [], "cmdline_substrings": [] },   // 只增
  "freeze":    { "enabled": true, "max_per_scan": 20, "use_units": true,
                 "state_path": "~/.local/share/focused/frozen.json" },
  "session":   { "mode": "pomodoro", "default_minutes": 25, "pause_minutes": 10,
                 "pomodoro_work_minutes": 25, "pomodoro_break_minutes": 5,
                 "pomodoro_cycles": 4 },
  "urls":      { "patterns": [] },
  "plugins_path": "~/.config/focused/plugins"
}
```

额外保护名单：`~/.config/focused/protect_names.txt`（每行一个名字）。数据在
`~/.local/share/focused/`：`frozen.json`（冻结记录）、`plugins.json`（插件注册表）、`audit.jsonl`。

## 写一个插件

任何语言三行起步：

```bash
# 1) 注册，拿到自己的 id 与令牌
curl -s -X POST http://127.0.0.1:8766/api/v1/plugins/register \
     -H 'Content-Type: application/json' -d '{"name":"pomodoro"}'

# 2) 贡献规则（与别人的名单取并集，互不覆盖）
curl -s -X PUT http://127.0.0.1:8766/api/v1/plugins/pomodoro/rules \
     -H "X-Focused-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"names":["firefox"],"url_patterns":["reddit.com"]}'

# 3) 认领一段专注：30 秒租约，**必须续期**；不续期就自动解冻
curl -s -X POST http://127.0.0.1:8766/api/v1/plugins/pomodoro/session \
     -H "X-Focused-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"active":true,"ttl_seconds":30}'
```

需要更深的能力（在扫描里做判断、冻结时顺带做事）可以写**进程内 Python 插件**，放在
`~/.config/focused/plugins/*.py`，实现 `register(focused)` 与 `on_event(focused, event)`；
加载失败或反复抛异常会被记事件并在连续 5 次失败后自动禁用，绝不影响主循环。
完整说明见 [docs/focused.md](docs/focused.md)。

## 安全模型

**只对当前用户的进程发信号，不需要任何特权。**

| 场景 | 兜底 |
|---|---|
| 专注结束 / 手动结束 | 立刻全部解冻 |
| 前端租约过期（崩溃 / 被杀 / 被冻） | 当成会话结束 → 全部解冻 + 审计 |
| `focusedd` 被 `SIGKILL` | systemd `ExecStopPost=… --thaw-all` 仍会执行 |
| 机器重启 / 下次启动 | 按 `frozen.json` 把上一轮遗留全部解冻 |
| 规则改动、目标不再命中 | 立刻解冻它 |
| PID 被复用 | 逐 PID 校验 `starttime`，绝不对陌生进程发 `SIGCONT` |
| 冻结没被内核确认 | 回滚解冻并记审计 |
| 自己 / 自己的 cgroup | 绝不冻结 |
| 一次扫描命中太多 | `freeze.max_per_scan` 上限 |
| 插件反复报错 | 记事件、计数、连续 5 次失败自动禁用 |

HTTP API 默认只绑 `127.0.0.1`；配置 `http.token` 后所有请求都要带 `X-Focused-Token`。
**不要把它暴露到公网**：这个 API 能挂起你机器上的进程。

## 命令行

```bash
focusedd --session 25              # 做一轮 25 分钟，结束后自动恢复并退出
focusedd                           # 常驻（systemd 已经在跑）
focusedd --status / --frozen       # 现在什么状态 / 现在冻着谁（不依赖实例）
focusedd --thaw-all                # 全部恢复（幂等）
focusedd --add-name firefox        # 追加屏蔽名单
focusedd --add-url reddit.com      # 追加网址名单
kill -USR1 <pid>                   # 紧急全部恢复
```

自检（推荐第一次装完就跑）：

```bash
focused-doctor
```

逐项检查后端、游戏目录、BepInEx、winhttp override、Mod DLL、日志加载记录、两端地址与令牌，
每条 ✗ 都给出确切命令。

## API

契约见 [docs/focused-protocol.md](docs/focused-protocol.md)（三个前端共用同一套）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v1/focus` | 前端读的扁平状态（`active` / `pause_until` / `url_patterns` / `frozen_count` …） |
| GET | `/api/v1/status` | 完整状态（冻结列表、来源、插件、claim、统计） |
| PUT | `/api/v1/focus/rules` | 内置来源的规则 |
| POST | `/api/v1/focus/state` | 内置来源的会话 claim（可带 `ttl_seconds`） |
| POST | `/api/v1/focus/session` `/pause` `/scan` `/thaw` | 开始 / 放行 / 扫描 / 全部恢复 |
| GET / PATCH | `/api/v1/config` | 读写配置（原子保存） |
| GET | `/api/v1/processes` | 运行中进程（并行数组 + 对象数组） |
| GET | `/api/v1/events?since=N` | 事件流 |
| POST | `/api/v1/plugins/register` | 注册插件 → id + 令牌 + 端点清单 |

## 开发

```bash
scripts/run-tests.sh          # 全部测试（引擎 / 应用 / 插件平台 / 脚本）
scripts/run-tests.sh --offline   # 无网络时用本地包缓存
```

测试覆盖：真进程冻结/解冻、cgroup 规则、PID 复用保护、遗留恢复、会话与租约、多来源规则合并、
插件注册与隔离、配置 API、事件流、看板脚本一致性、以及**端到端**（前端推规则 + 心跳 →
真进程被挂起 → 松开 / 租约到期 / 被杀后自动恢复）。

文档：[docs/focused.md](docs/focused.md)（使用）、[docs/focused-design.md](docs/focused-design.md)（设计）、
[docs/freeze-mode.md](docs/freeze-mode.md)（为什么冻结而不是关闭）、[docs/design.md](docs/design.md)（整体设计）。

## 发布

维护者发布流程（构建、Release 资产、AUR）见 [docs/releasing.md](docs/releasing.md)。

## 许可

MIT。详见 [LICENSE](LICENSE)。

> 它会**挂起**你指定的进程；虽然所有异常路径都会尝试恢复，请先用「只记录，不冻结」确认名单正确。
