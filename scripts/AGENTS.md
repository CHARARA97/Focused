# focused 脚本

- `doctor-focused.sh` — 全链路自检；读取 `/api/v1/status` 的 health。
- `install-focused.sh`、`launch-focused.sh` — 安装/启动。
- `run-tests.sh`、`test-runner.py` — 测试入口。
- `simulate-plugin.py` — HTTP 插件模拟。

脚本不复制后端业务逻辑；字段变更时同步 doctor/测试。
