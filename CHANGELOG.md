# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号见
[Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-10-04

首个版本。

### 新增
- 专注会话：番茄钟（专注 / 休息，休息阶段自动恢复应用）、正向计时、可自定义时长与轮数
- 冻结而不是关闭：`SIGSTOP` / cgroup v2 整单元冻结；任何异常路径都会解冻
- 多来源规则合并：每个前端各持一份名单，取并集，互不覆盖
- 会话租约：前端停止心跳后自动结束会话并恢复全部应用
- 看板：概览 / 应用名单 / 网址名单 / 插件 / 设置 / 日志
- 插件平台：HTTP 插件注册与令牌、事件流、事件 Webhook、进程内 Python 插件（含错误隔离与自动禁用）
- 配置编辑 API、进程列表 API、事件流 API
- 崩溃兜底：`frozen.json` 启动恢复、systemd `ExecStopPost --thaw-all`、`SIGUSR1`、`--thaw-all`
- 452+ 条内置保护名单（合成器、终端、输入法、wine、游戏本体…），只增不减
- CLI：`--session` / `--status` / `--frozen` / `--thaw-all` / `--add-name` / `--add-url`
- `focused-doctor` 自检脚本：后端、游戏目录、BepInEx、winhttp、DLL、日志、两端令牌

[0.1.0]: https://github.com/CHARARA97/Focused/releases/tag/v0.1.0
