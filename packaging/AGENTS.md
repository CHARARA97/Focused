# focused packaging

- `focused.service` — systemd user unit，启动 `focused`。
- [`arch/`](arch/AGENTS.md) — 本地 Arch `focused` 包；`makepkg -si`，当前不提交 AUR。

安装/路径变更要同步 `../docs/distribution.md` 和安装脚本；这里不放业务逻辑。
