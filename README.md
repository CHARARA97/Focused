# Focused

**专注期间挂起干扰性应用，专注结束后恢复。**

Focused 是独立运行的后端应用。它读取 `/proc`，按规则匹配进程并**冻结**（`SIGSTOP` 或 cgroup v2
的 `cgroup.freeze`），会话结束时**恢复**。它不依赖游戏或浏览器，自带看板、命令行与 HTTP API；
两个前端（游戏插件、浏览器扩展）均通过回环 HTTP 与其通信：

| 组件 | 仓库 |
|---|---|
| 后端应用（本仓库） | `focused` |
| 游戏插件（《放松时光：与你共享 Lo-Fi 故事》的 BepInEx Mod） | [chillfocused](https://github.com/CHARARA97/ChillFocused-Linux) |
| 浏览器扩展（专注期间关闭标签页） | [focused-extension](https://github.com/CHARARA97/Focused-Extension) |

> **对进程执行的操作只有冻结。** 不存在终止路径，也没有「结束进程」开关；
> 所有异常路径（租约到期、进程被终止、机器重启）都会恢复进程。

---

## 目录

- [安装](#安装)
- [快速上手](#快速上手)
- [会话模式](#会话模式)
- [看板](#看板)
- [配置](#配置)
- [插件开发](#插件开发)
- [安全模型](#安全模型)
- [命令行](#命令行)
- [API](#api)
- [开发](#开发)
- [发布](#发布)
- [许可](#许可)

---

## 安装

### 一行安装（任意发行版）

```bash
curl -fsSL https://github.com/CHARARA97/Focused/releases/latest/download/install.sh | sh
systemctl --user enable --now focused
xdg-open http://127.0.0.1:8766/
```

该脚本从 Release 取得 wheel 并解包到 `~/.local/share/focused/app`，同时安装启动器、自检脚本与
systemd 用户单元。不需要 pip，也不需要虚拟环境。

### Arch Linux

`packaging/aur/` 下的 `focused` 与 `focused-git` 已就绪，但 AUR 目前关闭新用户注册，
因此尚未提交。可先从仓库本地构建：

```bash
git clone https://github.com/CHARARA97/Focused
cd Focused/packaging/aur/focused
updpkgsums && makepkg -si
systemctl --user enable --now focused
```

### 使用 uv 或 pipx 安装

```bash
uv tool install "focusd @ https://github.com/CHARARA97/Focused/releases/latest/download/focusd-0.1.0-py3-none-any.whl"
# 或从源码安装：
uv tool install git+https://github.com/CHARARA97/Focused
focusedd --write-default-config ~/.config/focused/config.json
focusedd
```

系统要求：Linux（使用 `/proc`、cgroup v2 与 `SIGSTOP`）、Python ≥ 3.9、systemd 用户会话。
不需要 root 权限。

## 快速上手

1. 打开 <http://127.0.0.1:8766/>
2. 在「应用名单」中搜索并勾选需要屏蔽的应用
3. 在顶栏选择「番茄钟 25 分钟 · 4 轮」或「正向计时」
4. 「概览」出现已冻结列表即表示已生效；「结束专注」与「立即恢复全部」可随时恢复进程

如需先确认命中范围：在「设置」中启用**只记录，不冻结**执行一轮，并在「日志」中查看命中记录。

## 会话模式

| 模式 | 行为 |
|---|---|
| **番茄钟** | 专注与休息交替；**仅专注阶段生效** —— 进入休息时恢复全部进程，休息结束后重新冻结。轮数为 1 时执行单轮（等同于单次倒计时）；为 0 时持续循环；支持「跳过休息」 |
| **正向计时** | 从 0 开始计时，不设上限；由用户手动结束 |

时长均可自定义：可在看板中填写（提供 15/25/45/60/90 预设），或写入 `config.json` 的 `session`。
顶栏按钮显示的值即实际使用的值。

两点说明：

- **放行不计入专注时长**：选择「放行 10 分钟」会将当前阶段的截止时间延后 10 分钟。
- **休息即恢复**：番茄钟休息阶段 `active` 为 false，因此进程与浏览器标签页均恢复。

## 看板

看板地址为 <http://127.0.0.1:8766/>，共六个页面：

| 页面 | 作用 |
|---|---|
| 概览 | 会话面板、会话来源、已冻结进程、运行环境自检、事件记录 |
| 应用名单 | **屏蔽模式**（从运行中的进程中选择要冻结的）与**保护模式**（维护永不冻结的名单） |
| 网址名单 | 下发给浏览器扩展的网址名单（Focused 不操作浏览器） |
| 插件 | 已连接插件、最后心跳时间、贡献规则数、是否认领会话；支持禁用与移除，并可注册新插件 |
| 设置 | 会话模式与时长、放行时长、扫描间隔、HTTP 令牌；保存后写入 `config.json` |
| 日志 | 事件流（冻结、恢复、会话、插件、错误） |

## 配置

`~/.config/focused/config.json`（首次运行生成；看板「设置」页即修改此文件）：

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

额外保护名单位于 `~/.config/focused/protect_names.txt`（每行一个名称）。数据目录为
`~/.local/share/focused/`，包含 `frozen.json`（冻结记录）、`plugins.json`（插件注册表）与
`audit.jsonl`。

## 插件开发

任何语言均可通过三个请求接入：

```bash
# 1) 注册，返回 id 与令牌
curl -s -X POST http://127.0.0.1:8766/api/v1/plugins/register \
     -H 'Content-Type: application/json' -d '{"name":"pomodoro"}'

# 2) 提交规则（按来源分别保存并取并集，不覆盖其他来源）
curl -s -X PUT http://127.0.0.1:8766/api/v1/plugins/pomodoro/rules \
     -H "X-Focused-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"names":["firefox"],"url_patterns":["reddit.com"]}'

# 3) 认领会话：租约 30 秒，必须续期；未续期则自动恢复
curl -s -X POST http://127.0.0.1:8766/api/v1/plugins/pomodoro/session \
     -H "X-Focused-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"active":true,"ttl_seconds":30}'
```

如需更细粒度的控制（在扫描过程中参与判断、在冻结时执行附加操作），可编写**进程内 Python 插件**，
置于 `~/.config/focused/plugins/*.py`，实现 `register(focused)` 与 `on_event(focused, event)`。
加载失败或连续抛出异常时记录事件；连续 5 次失败后自动禁用该插件，不影响主循环。
完整说明见 [docs/focused.md](docs/focused.md)。

## 安全模型

仅对当前用户的进程发送信号，不需要任何特权。

| 场景 | 恢复路径 |
|---|---|
| 会话结束或手动结束 | 立即恢复全部进程 |
| 前端租约过期（崩溃、被终止、被冻结） | 按会话结束处理，恢复全部进程并记录审计 |
| `focusedd` 被 `SIGKILL` | systemd `ExecStopPost=… --thaw-all` 仍会执行 |
| 机器重启或下次启动 | 按 `frozen.json` 恢复上一轮遗留的进程 |
| 规则变更后目标不再命中 | 立即恢复该进程 |
| PID 被复用 | 逐 PID 校验 `starttime`，不对其他进程发送 `SIGCONT` |
| 冻结未被内核确认 | 回滚为恢复并记录审计 |
| 自身或其 cgroup 被命中 | 不冻结 |
| 单次扫描命中过多 | 受 `freeze.max_per_scan` 限制 |
| 插件反复报错 | 记录事件与计数；连续 5 次失败后自动禁用 |

HTTP API 默认仅监听 `127.0.0.1`；配置 `http.token` 后，所有请求必须携带 `X-Focused-Token`。
**不应将该 API 暴露到公网**：它能够挂起本机进程。

## 命令行

```bash
focusedd --session 25              # 执行一轮 25 分钟会话，结束后恢复并退出
focusedd                           # 以常驻模式运行（通常由 systemd 托管）
focusedd --status / --frozen       # 当前状态 / 当前被冻结的进程（不依赖实例）
focusedd --thaw-all                # 恢复全部进程（幂等）
focusedd --add-name firefox        # 追加屏蔽名单
focusedd --add-url reddit.com      # 追加网址名单
kill -USR1 <pid>                   # 紧急恢复全部进程
```

自检（建议安装后执行）：

```bash
focused-doctor
```

逐项检查后端、游戏目录、BepInEx、winhttp 覆盖、插件 DLL、日志中的加载记录，
以及两端的地址与令牌；每一项不通过都会给出对应命令。

## API

协议定义见 [docs/focused-protocol.md](docs/focused-protocol.md)（三个前端共用）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v1/focus` | 前端读取的扁平状态（`active`、`pause_until`、`url_patterns`、`frozen_count` 等） |
| GET | `/api/v1/status` | 完整状态（冻结列表、来源、插件、claim、统计） |
| PUT | `/api/v1/focus/rules` | 内置来源的规则 |
| POST | `/api/v1/focus/state` | 内置来源的会话 claim（可带 `ttl_seconds`） |
| POST | `/api/v1/focus/session`、`/pause`、`/scan`、`/thaw` | 开始、放行、扫描、恢复全部 |
| GET / PATCH | `/api/v1/config` | 读取与修改配置（原子保存） |
| GET | `/api/v1/processes` | 运行中的进程（并行数组与对象数组） |
| GET | `/api/v1/events?since=N` | 事件流 |
| POST | `/api/v1/plugins/register` | 注册插件，返回 id、令牌与端点清单 |

## 开发

```bash
scripts/run-tests.sh             # 全部测试（引擎、应用、插件平台、脚本）
scripts/run-tests.sh --offline   # 无网络时使用本地包缓存
```

测试范围：真实进程的冻结与恢复、cgroup 规则、PID 复用保护、遗留恢复、会话与租约、
多来源规则合并、插件注册与隔离、配置 API、事件流、看板脚本一致性，以及端到端流程
（前端提交规则与心跳 → 真实进程被挂起 → 释放、租约到期或被终止后自动恢复）。

文档：[docs/focused.md](docs/focused.md)（使用）、[docs/focused-design.md](docs/focused-design.md)（设计）、
[docs/freeze-mode.md](docs/freeze-mode.md)（冻结而非终止的决策记录）、[docs/design.md](docs/design.md)（整体设计）。

## 发布

维护者发布流程（构建、Release 资产、AUR）见 [docs/releasing.md](docs/releasing.md)。
AUR 的 `focused` 与 `focused-git` 包已就绪，但 AUR 目前关闭新用户注册，暂未提交。

## 许可

MIT。详见 [LICENSE](LICENSE)。

> Focused 会挂起指定的进程。尽管所有异常路径都会尝试恢复，仍建议先启用「只记录，不冻结」
> 确认名单正确。
