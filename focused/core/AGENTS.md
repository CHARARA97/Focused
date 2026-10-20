# core — OS 适配与判定

| 模块 | 职责 |
|---|---|
| `killer.py` | 唯一发 `SIGKILL` 的地方；发信号前复检 starttime |
| `proctree.py` | 执行前重读子树（`/proc/<pid>/task/*/children`） |
| `procfs.py` | `/proc` 读取：快照、starttime、子进程 |
| `decision.py` | `evaluate`：硬护栏 → 保护 → 名字 → cmdline → PID guard |
| `rules.py` | `RuleSet`：规则值、归一化、catch-all / 短子串 / shadowed 自检 |
| `guards.py` | PID 复用的纯判定（无效 PID / 已消失 / 被复用） |
| `matching.py` | 精确名/通配符/comm 截断的预编译匹配器 |
| `protect.py` | 内置保护名单 |
| `urlfilter.py` | URL pattern 清洗 |
| `audit.py` | 0600 审计日志 |

规则：**不要在 `killer.py` 之外发信号**；不要让 `core` import `app/api/server`。
这里改动必须配失败路径测试；依赖方向由 `tests/test_architecture.py` 强制。
