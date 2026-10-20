# domain — 纯值与纯转移

没有 I/O、没有可变状态、没有单例。全部 `frozen=True`。

| 模块 | 回答的问题 |
|---|---|
| `intent.py` | 现在该不该动手（`Intent.decide`，16 种组合可穷举） |
| `identity.py` | 自己是谁：`Identity(pid, ppid, uid)` 与硬护栏集合 |
| `tick.py` | 一轮扫描的输入：序号、单调时间、意图、策略 |
| `policy.py` | 这轮的模式、上限、规则版本 |
| `targets.py` | 进程树的纯算法、`Selection`、starttime 完整性 |
| `preflight.py` | 这个目标允许动吗（唯一的"不准杀"判定） |
| `session_timer.py` | 计时状态机：`SessionState` + 纯转移 `start/extend/advance/...` |
| `reasons.py` | 全部稳定字符串：拒绝原因、门控、结果、事件名 |

规则是 fail-closed：不能证明安全就拒绝。

可变累加器不在这里——`Health`、`ClaimBook`、`EpisodeBook` 在 `app/`，因为它们有生命周期。

纪律由 `tests/test_architecture.py` 强制：只 import domain、dataclass 必须 frozen、不出现 IO 模块或 `open()`。
