# Focused

独立的专注应用：按规则挂起干扰性应用，专注结束后恢复。自带看板、CLI、HTTP API 和插件平台。

```bash
# 安装后（推荐）
pipx install .
focused --help
# 旧名兼容：focusedd --help

# GitHub Release（推荐给普通用户）
curl -fsSLO https://github.com/CHARARA97/Focused/releases/latest/download/install.sh
less install.sh   # 先读脚本
sh install.sh
~/.local/bin/focused --help

# 源码开发
python3 -m pytest -q
python3 focusedd.py --help
```

- 架构与进度：`docs/backend-refactor-plan.md`
- 协议：`docs/focused-protocol.md`
- 插件接口：`docs/plugin-api.md`
- 问题清单：`docs/known-issues.md`
