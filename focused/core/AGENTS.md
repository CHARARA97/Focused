# core — OS 与危险适配

| 模块 | 职责 |
|---|---|
| `freezer.py` | 唯一发 `SIGSTOP/SIGCONT/systemctl` 的地方；记录、guard、确认 |
| `procfs.py` | `/proc` 读取、cgroup 状态 |
| `cgroupfs.py` | cgroup 规划、unit 判定、成员 |
| `rules.py` | 规则值、evaluate、catch-all/PID guard |
| `matching.py` | 精确名/通配符/comm 截断的匹配器 |
| `protect.py` | 内置保护名单 |
| `urlfilter.py` | URL pattern 清洗 |
| `audit.py` | 0600 审计日志 |

规则：**不要在 `freezer.py` 之外发信号**；不要让 `core` import `app/api/server`。这里改动必须配失败路径测试。
