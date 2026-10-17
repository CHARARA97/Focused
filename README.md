# Focused

[![CI](https://github.com/CHARARA97/Focused/actions/workflows/ci.yml/badge.svg)](https://github.com/CHARARA97/Focused/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/CHARARA97/Focused)](https://github.com/CHARARA97/Focused/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**专注时挂起干扰性应用，专注结束后恢复。**  
不关闭应用，只暂停；自带看板、CLI、HTTP API 和插件平台。

适用于 Linux。默认只操作当前用户自己的进程，不需要 root。

---

## 先看这里

安装后**黑名单是空的**，不会自动冻结任何东西。你需要先添加要管理的应用，再开始一次专注。

推荐流程：

```bash
# 1. 安装
curl -fsSLO https://github.com/CHARARA97/Focused/releases/latest/download/install.sh
less install.sh          # 建议先读一遍
sh install.sh --start

# 2. 打开看板
xdg-open http://127.0.0.1:8766/

# 3. 在看板里添加应用，点击“开始专注”
```

如果不想用看板：

```bash
focused --add-name firefox
focused --add-name discord
focused --session 25
```

---

## 安装

### 方式 A：GitHub Release（推荐普通用户）

需要：

- Linux
- `python3` ≥ 3.9
- 推荐 systemd user service + cgroup v2
- `curl`

```bash
curl -fsSLO https://github.com/CHARARA97/Focused/releases/latest/download/install.sh
less install.sh
sh install.sh
```

安装器会创建：

```text
~/.local/bin/focused
~/.local/bin/focusedd          # 兼容别名
~/.local/bin/focused-launch
~/.local/bin/focused-doctor
~/.local/share/focused/app/
~/.config/focused/config.json
~/.config/systemd/user/focused.service
```

默认只安装，不自动启动。启动并设为登录后自启：

```bash
systemctl --user enable --now focused
```

或者安装时直接启动：

```bash
sh install.sh --start
```

如果 `~/.local/bin` 不在 `PATH`：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### 方式 B：pipx + wheel（适合已经会用 Python 工具的人）

从 Release 下载 wheel 后：

```bash
pipx install ./focusd-0.2.0-py3-none-any.whl
focused --help
```

这种方式只会安装 `focused` 命令，不会自动创建 systemd service；需要自己启动：

```bash
focused
```

### 方式 C：Arch Linux 本地包

不依赖 AUR；从仓库里的 `packaging/arch/` 直接构建：

```bash
git clone https://github.com/CHARARA97/Focused.git
cd Focused/packaging/arch
makepkg -si
```

安装后：

```bash
systemctl --user daemon-reload
systemctl --user enable --now focused
focused --help
```

包名和命令都是 `focused`。包内容：

```text
/usr/bin/focused
/usr/bin/focusedd
/usr/bin/focused-doctor
/usr/lib/systemd/user/focused.service
```

卸载：

```bash
systemctl --user disable --now focused
focused --thaw-all
sudo pacman -Rns focused
```

当前没有提交 AUR；`makepkg -si` 是本地安装方式。

### 方式 D：从源码运行（开发用）

```bash
git clone https://github.com/CHARARA97/Focused.git
cd Focused
python3 -m pytest -q
python3 focusedd.py --help
```

源码运行不需要安装，但需要自己处理路径和 service。

### 系统要求

- **Linux**；不支持 Windows/macOS。
- **Python 3.9+**。
- 推荐 **systemd user session** 和 **cgroup v2**，这样可以使用整 unit 冻结与 `ExecStopPost` 崩溃恢复。
- 没有 systemd 时可以退回单进程 `SIGSTOP`，但崩溃后的自动恢复能力较弱。
- 不需要 root；Focused 只处理当前用户有权处理的进程。
- 桌面环境/X11/Wayland 不限，但被冻结的图形应用可能让相关窗口暂时无响应。

---

## 基本使用

### 1. 打开看板（推荐）

```bash
systemctl --user start focused
xdg-open http://127.0.0.1:8766/
```

看板可以：

- 从当前进程里选择要加入黑名单的应用；
- 管理网址名单（供浏览器扩展读取）；
- 开始/暂停/结束专注；
- 选择倒计时、番茄钟或正向计时；
- 查看当前冻结了哪些应用；
- 编辑配置、查看事件日志。

### 2. 命令行做一次专注

```bash
focused --session 25
```

这会：

1. 启动一个 25 分钟的倒计时会话；
2. 扫描并挂起黑名单应用；
3. 会话结束后恢复所有由它挂起的应用；
4. 退出。

如果 systemd service 正在运行，**不要同时再运行** `focused --session`；先停服务：

```bash
systemctl --user stop focused
focused --session 25
```

### 3. 快速试运行（不冻结）

```bash
focused --once --session 5 --dry-run
```

只扫描并记录“本来会冻谁”，不会真的挂起。

### 4. 查看状态

```bash
focused --status       # 查询正在运行的实例
focused --frozen       # 读取状态文件，列出当前挂起的应用；不依赖 daemon
focused --print-config # 打印当前配置
```

### 5. 恢复 / 紧急解冻

```bash
focused --thaw-all
```

`--thaw-all` 是离线命令，不依赖 daemon；systemd 的 `ExecStopPost` 也会在异常退出后自动执行它。

如果服务正在运行，建议先停服务再执行，避免和 daemon 争抢：

```bash
systemctl --user stop focused
focused --thaw-all
systemctl --user start focused
```

### 6. 添加/修改黑名单

服务未运行时，可以用 CLI：

```bash
focused --add-name firefox
focused --add-name discord
focused --add-url youtube.com
```

服务正在运行时，优先用看板的“应用名单/网址名单”页面；CLI 修改配置文件后需要重启服务：

```bash
systemctl --user restart focused
```

配置文件位置：

```text
~/.config/focused/config.json
```

### 7. 不用 Web

```bash
focused --no-http
focused --session 25 --no-http
```

适合服务器、脚本或不想开端口的环境。

---

## 常用命令速查

| 命令 | 作用 |
|---|---|
| `focused` | 前台常驻，启动扫描和 HTTP API |
| `focused --session 25` | 前台跑一轮 25 分钟专注 |
| `focused --once --session 5` | 启动会话后只扫描一次 |
| `focused --once --session 5 --dry-run` | 单次扫描，只预演不冻结 |
| `focused --once --session 5 --keep-frozen` | 扫描后保留冻结状态（不自动恢复） |
| `focused --status` | 查询运行中的实例 |
| `focused --frozen` | 查看当前冻结记录（离线） |
| `focused --thaw-all` | 恢复所有记录（离线、幂等） |
| `focused --add-name NAME` | 添加应用名到黑名单 |
| `focused --add-url PATTERN` | 添加浏览器网址规则 |
| `focused --print-config` | 打印生效配置 |
| `focused --write-default-config PATH` | 写一份默认配置 |
| `focused --benchmark-scan 20` | 测 20 次真实 `/proc` 扫描，不冻结 |
| `focused --no-http` | 不启动 HTTP/看板 |
| `focused --version` | 版本 |

旧命令名 `focusedd` 仍可用，例如 `focusedd --status`。

---

## 它安全吗？

- **只挂起，不关闭**：使用 `SIGSTOP` 或 cgroup freezer，不会 `SIGKILL` 应用。
- **只操作当前用户**：不碰其他用户的进程。
- **默认拒绝危险目标**：自身、PID 1、shell/终端/合成器/输入法、slice、用户管理器、共享 unit 等有内置保护。
- **无法证明安全就拒绝**：未知 UID、无法确认的进程树、共享 unit 等按 fail-closed 处理。
- **崩溃恢复**：冻结记录写入 `~/.local/share/focused/frozen.json`；`--thaw-all` 和 systemd `ExecStopPost` 负责恢复。
- **网络只开回环**：HTTP API 默认绑定 `127.0.0.1`，不是公网服务。

> 冻结不是无代价的：被冻结的应用如果持有文件锁、D-Bus 名称或管道，相关程序可能暂时卡住。遇到异常先执行 `focused --thaw-all`。

---

## 升级

重新运行最新 `install.sh`：

```bash
curl -fsSLO https://github.com/CHARARA97/Focused/releases/latest/download/install.sh
less install.sh
sh install.sh
systemctl --user restart focused
```

配置文件默认保留。用 pipx 安装的则重新安装新 wheel：

```bash
pipx install --force ./focusd-<version>-py3-none-any.whl
```

---

## 卸载

先停止服务并恢复所有目标：

```bash
systemctl --user disable --now focused
focused --thaw-all
```

然后删除程序：

```bash
rm -rf ~/.local/share/focused/app
rm -f ~/.local/bin/focused ~/.local/bin/focusedd
rm -f ~/.local/bin/focused-launch ~/.local/bin/chillfocus-launch
rm -f ~/.local/bin/focused-doctor
rm -f ~/.config/systemd/user/focused.service
systemctl --user daemon-reload
```

配置和冻结记录默认保留。确认不再需要后，可另行删除：

```bash
rm -rf ~/.config/focused
rm -f ~/.local/share/focused/frozen.json
rm -f ~/.local/share/focused/plugins.json
rm -f ~/.local/share/focused/audit.jsonl
```

---

## 故障排查

### `focused: command not found`

```bash
export PATH="$HOME/.local/bin:$PATH"
focused --version
```

把这一行加入 `~/.bashrc` 或 `~/.zshrc`。

### 看板打不开

```bash
systemctl --user status focused
focused --status
journalctl --user -u focused -n 100 --no-pager
```

检查 8766 端口是否被占用：

```bash
ss -ltnp 'sport = :8766'
```

### 什么都没冻结

1. 黑名单是否为空？安装后默认是空的。
2. 是否开了 `dry_run`？看板“设置”里检查。
3. 应用是否被内置保护名单保护？
4. 用 `focused --frozen` 看记录，`journalctl` 看拒绝原因。
5. 运行 `focused-doctor` 做一次全链路自检。

### 应用一直处于挂起状态

```bash
systemctl --user stop focused
focused --thaw-all
```

`--thaw-all` 可以重复执行，不会重复解冻或报错。

### 自检

```bash
focused-doctor
```

会检查后端、配置路径、冻结记录、扫描健康、systemd/service 等。

---

## 浏览器扩展与游戏插件

Focused 本体不会关闭浏览器标签页，也不会读取游戏状态。

- **浏览器扩展**：读取 Focused 的网址名单，在专注期间关闭/保留黑名单标签页。
- **ChillFocused 游戏插件**：把游戏内专注状态交给 Focused，由 Focused 执行冻结。

两者都是可选前端；不安装也能单独使用 Focused。

---

## 文档与开发

- 使用/架构：[`docs/focused.md`](docs/focused.md)
- 冻结安全：[`docs/freeze-mode.md`](docs/freeze-mode.md)
- HTTP/插件协议：[`docs/focused-protocol.md`](docs/focused-protocol.md)
- 插件接口：[`docs/plugin-api.md`](docs/plugin-api.md)
- 发布流程：[`docs/releasing.md`](docs/releasing.md)
- 模块地图：[`AGENTS.md`](AGENTS.md)
- 问题反馈：[GitHub Issues](https://github.com/CHARARA97/Focused/issues)

开发：

```bash
python3 -m pytest -q
python3 focusedd.py --help
```

## License

MIT，见 [LICENSE](LICENSE)。
