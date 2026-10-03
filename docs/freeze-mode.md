# 冻结模式（freeze mode）设计


> **状态说明（合并后）**：本文档写作时的宿主侧执行者是 `chillfocusd` 守护进程。
> 它已经**并入 Focused 应用**（`focused/`，`~/.local/bin/focusedd`，端口 8766），
> 那一层已经删除；下文的"守护进程"就是今天的 **Focused**。
> 当前架构见 [README](../README.md)，应用细节见 [focused.md](focused.md)。
> **已定决策**：S1 冻结代替关闭 · M-B（systemd cgroup freezer，主）/ M-A（`SIGSTOP`，兜底）·
> R2 冻整棵应用 · T1 + T4 自动解冻与启动解冻 · E1 纯冻不升级为关闭 · C1 全局开关
>
> 本文的重点是**保护性兜底**：这套机制里"冻住别人的东西"是一种伤害，任何一条兜底缺失都会让
> 用户的东西永久挂起。所以每个兜底都配"为什么"和"怎么测"。
>
> 相关背景（机制调研与实测记录）见 [lessons.md](lessons.md)；判定与执行的分工见 [design.md](design.md)。

---

## 1. 目标与非目标

| | 内容 |
|---|---|
| **目标** | 创作模式期间把黑名单应用**挂起**而非关闭；计时结束**原样恢复**；任何异常路径下都不会把用户的东西永久冻住 |
| **非目标** | 不做"冻久了自动关闭"（E1）· 不做每应用动作（C1→C2 之后再说）· 不做"不可绕过"的强制（那属于关闭模式）· 不用 `killpg` / `ptrace` / cgroup v1 |

**为什么它值得做**：关闭会丢掉浏览器的标签、编辑器未保存的草稿、下载进度；冻结是可逆的暂停，
"专注期间别打扰、结束后还给你"。代价见 §5 的风险清单。

---

## 2. 状态机（daemon 侧）

沿用现有唯一判定点 `FocusEngine.enforcing()`（两个分支：门控关 → 手动武装；门控开 → 计时器满足）。

| 转移 | 触发 | 动作 |
|---|---|---|
| 进入冻结态 | `enforcing()` 变 True 且 `freeze.enabled` | 扫描 → 对每个"匹配黑名单且未被保护"的目标 → 解析 unit → 冻结 → 记入 `_frozen` → 落盘 → 事件 `frozen` |
| 维持 | 每轮扫描 | 新出现的匹配进程照常补冻；已冻 unit 里新生成的进程由 cgroup 语义自动被冻（**这是 R2 相对 R1 的最大好处**） |
| 离开冻结态（T1） | `enforcing()` 变 False / `freeze.enabled=0` / 规则 reload 后不再匹配 / `set_enabled(False)` | **全部解冻** → 清 `_frozen` → 落盘 → 事件 `thawed` |
| 启动（T4） | daemon 启动 | 读 `frozen.json` → 逐个解冻（幂等）→ 清空 → 审计 `thawed(reason=startup-recovery)` |
| 退出 | 正常退出 / 被 SIGKILL | 正常退出自己解冻；**异常退出由 systemd 的 `ExecStopPost=` 兜底**（§4.2） |

---

## 3. 机制与降级链

| 顺序 | 手段 | 何时用 | 代价 |
|---|---|---|---|
| **M-B1** | `systemctl --user freeze <unit>` | 首选 | 依赖 D-Bus/`systemctl`；**换来 systemd 的"要对该 unit 执行 job 前自动 thaw"安全网** |
| **M-B2** | 直接写 `<cgroup>/cgroup.freeze = 1` | M-B1 不可用或超时 | 本机实测用户可写、无需 root；但**失去 B1 的自动 thaw 安全网**，所以只在 B1 失败时降级并记审计 |
| **M-A** | `kill(pid, SIGSTOP)` | 目标不在本会话子树 / unit 是共享的 / 整树冻结不安全 | 只冻一个进程；**崩溃后可能永久挂起**（靠 §4.2 兜底） |

每一步都要**确认生效**再记账：B1/B2 轮询 `cgroup.events` 的 `frozen 1`（带超时），M-A 轮询
`/proc/<pid>/status` 的 `State: T`。超时算失败并记审计，**不重试风暴**。

---

## 4. 保护性兜底（本文主体）

### 4.1 硬护栏：不可配置、永远生效

| # | 规则 | 为什么 | 实现点 |
|---|---|---|---|
| H1 | **保护名单优先**：被 `protect.*` 覆盖的名字既不冻也不关 | 冻结是另一种伤害，护栏必须同样先行 | 复用 `rules.evaluate()` 的 `ACTION_PROTECT`（冻结走同一判定） |
| H2 | 永不冻 PID≤1、内核线程、僵尸、异 uid、daemon 自身/父/`hard_protect_pids` | 已有硬护栏，冻结必须继承 | 同上 |
| **H3** | **绝不整单元冻结"包含 daemon 自己的 cgroup"**，但**降级为进程树**而不是整体放弃 | 整单元冻结会冻住执行者（自锁）；可整体放弃又会让功能在同一 cgroup 的场景下**静默失效**（例如守护进程与浏览器都从同一个终端启动） | `cgroupfs.plan_for` 命中前缀即返回 `pid / self-cgroup`；引擎再把 daemon 自己的 PID 与 hard-protect PID 从进程树里**剔除**（`_freezable_pids`），若根进程被剔除则整棵放弃 |
| **H4** | **拒绝冻结 slice 与用户管理器**（`*.slice`、`user@*.service`、`init.scope`、`-.mount` 等） | 冻 slice = 冻一整片会话 | unit 类型白名单：只接受 `.scope` / `.service`，且路径不含 `user@`、`init.scope` |
| **H5** | **拒绝冻结会话/终端/合成器/输入法** | 桌面假死，且用户无法自救 | 三层：内置保护名单（已有）＋ unit 名/父链启发式（terminal、niri、kwin、fcitx…）＋ §4.3 的"专属判定" |
| **H6** | **共享 unit 只冻匹配到的 PID**（降级到 M-A），并记审计说明 | 冻共享 unit = 牵连无关进程（"从终端启动的浏览器"会连终端一起冻） | §4.3 |
| **H7** | **单轮冻结上限**（默认 20），超过只记录并告警 | 一次误配把 200 个进程冻住会很难恢复 | `freeze.max_per_scan` |

### 4.2 失控兜底：daemon 死了/卡住了怎么办

| # | 兜底 | 说明 | 依据 |
|---|---|---|---|
| **F1** | **冻结名单落盘**（`~/.local/share/chillfocus/frozen.json`：cgroup 路径 / unit / pid / starttime / since / reason） | 没有它，崩溃 = 永久挂起 | 设计 |
| **F2** | **启动时孤儿解冻**（T4） | daemon 重启后先把上一轮的账还清，再谈冻结 | 设计 |
| **F3** | **`ExecStopPost=/path/chillfocusd --thaw-all`** | **即使 daemon 被 SIGKILL 也能放人**：systemd 手册明确该指令在服务"异常退出"后仍会执行，且建议用它做"即使启动失败也要跑"的清理 | systemd.service(5)：*"…or where the service exited unexpectedly… recommended to use this setting for clean-up operations"* |
| **F4** | **`SIGUSR1` = 紧急解冻** | daemon 卡在别处时，CLI 可能也卡；信号是最短路径 | 设计 |
| **F5** | **`chillfocusd --thaw-all` 必须自足**：自己读 `frozen.json` → 写 `cgroup.freeze=0` / `SIGCONT`（校验 `(pid,starttime)`）→ 清空文件；**幂等**、**不依赖 daemon 进程**、**不依赖 systemd** | `ExecStopPost` 运行时主进程已经没了（手册也提醒"不要试图与它们通信"），所以这个命令不能走 HTTP API | 设计 + 手册 |
| **F6** | （可选）`WatchdogSec=` + `sd_notify(WATCHDOG=1)` | daemon 假死（不是死）时由 systemd 重启它 → 走 F2 | 设计，可选 |
| **F7** | **明确不做：冻结 TTL 自动过期** | 死掉的进程不能定时解冻，TTL 是**假安全网**。免得以后有人为了"更安全"实现它 | 反面记录 |

> 注意 F3 的边界：如果整个用户管理器一起退出（注销/关机），`ExecStopPost` 可能来不及跑；
> 那时 F2（下次启动解冻）就是最后一道防线。两道都要有。

### 4.3 范围自保：凭什么冻整棵树

**专属应用 unit 判定**（满足才整树冻结，否则降级 H6）：

1. cgroup 路径形如 `…/app.slice/app-<name>-<id>.scope` 或 `app-<name>@<id>.service`；
2. **cgroup 内所有进程都能被"黑名单匹配 + 其父链"解释**（不存在与目标无关的进程）；
3. 该 cgroup **不是** daemon 自己 cgroup 的祖先或自身（H3）；
4. unit 不是 slice、不是 `user@*`（H4）。

任何一条不满足 → **只冻匹配到的 PID（M-A）**，并在审计里写清"unit 是共享的，降级为单进程冻结"。

### 4.4 用户可见与自救

| # | 项 | 说明 |
|---|---|---|
| U1 | `/status` 暴露冻结清单：**并行基础类型数组**（`frozen_units[]` / `frozen_pids[]` / `frozen_since[]` / `frozen_reason[]` / `frozen_count`） | 响应 DTO 必须扁平（这是刚立并已由契约测试强制的规矩） |
| U2 | HUD/面板显示"**已冻结 N**"，toast 提示"已冻结 firefox" / "已恢复 firefox" | 用户必须能发现"我的浏览器被冻了"，否则会以为系统坏了 |
| U3 | `chillfocusd --frozen`（列出当前冻结）/ `--thaw-all` | 命令行自救入口 |
| U4 | **`freeze.enabled=0` 立即解冻** | 不是"以后不冻"，是"马上放人" |
| U5 | `reload_config` 后不再匹配的条目**立刻解冻** | 配置改了要马上生效 |
| U6 | 审计/事件新增 `frozen` / `thawed` / `thaw-failed`，含 unit/pid/starttime/原因 | 事后能回答"谁冻的、为什么、什么时候" |

### 4.5 接口纪律（趁早钉住，避免重复踩坑）

| # | 规则 |
|---|---|
| I1 | 插件侧响应 DTO **只允许标量与基础类型数组**（扩展 `test_every_response_dto_stays_flat` 覆盖新字段） |
| I2 | PID 复用防护：`_frozen` 照抄现有 `_handled: Dict[pid, (starttime, expires)]` 的形状；`SIGCONT` 前必须校验 starttime |
| I3 | **绝不 `killpg`**（已有 AST 测试断言代码里不出现它）——解冻同样只按 PID 或 cgroup |
| I4 | 解冻也要**限流/分批**，避免一次 200 个把 daemon 卡住 |
| I5 | 冻结前 dry-run 预演：`freeze.enabled=1` + `dry_run=1` → 只记录"本来会冻 unit X（含 N 个进程）" |
| I6 | 冻结/解冻都写审计（新的 stats 计数 `frozen_total` / `thawed_total`），便于"我还冻着什么" |

### 4.6 准入门槛：冻结图形客户端可能拖住合成器（**不测通不发布**）

| 风险 | 机制 | 兜底 |
|---|---|---|
| Wayland 客户端被冻 | 它不再读 socket，合成器写缓冲满之后**可能阻塞整个事件循环** | ✅ **已实测（2026-10-03，niri + kitty）**：冻住一个 Wayland 客户端约 3 秒，niri 的 IPC 延迟 13–20 ms → 17–20 ms（无变化），日志无任何条目。**结论：niri 不受影响，准入通过。** 复测脚本与时序见 §10。风险本身仍在（其他合成器/老版本 Xwayland 可能不同），所以"一键全解"仍是必备兜底 |
| X11 / Xwayland 客户端被冻 | Xwayland 阻塞会波及**所有** X11 应用 | 同上，并把 Xwayland 本身放进保护名单（已有） |
| 拖住别的进程 | 被冻应用若持有文件锁 / D-Bus 名称 / 管道，**别人会卡住**（比关掉更难排查） | 分级默认：只冻"专属应用 unit"（H6）；文档里明确"冻结不是无代价"；保留一键全解 |
| 应用自己的 watchdog | 解冻后可能已经被它自己判死、要求重新登录、页面重载 | 明确写进用户可见说明；`E2` 升级为关闭**不在本设计内**（E1） |

---

## 5. 配置与接口

**daemon `config.json`**（新增，全部有默认值）

```jsonc
"freeze": {
  "enabled": true,           // 出厂就开：冻结是 ChillFocus 唯一的动作（见 §12d）
  "max_per_scan": 20,        // H7
  "confirm_timeout_ms": 1500 // 冻结后确认 frozen 1 的超时
}
```

界面上**没有**"改用关闭"这个开关：改 `enabled: false` 只能手改 JSON，含义是"既不冻结也不关闭"
（调试用），启动时会记一条警告。

**插件 `com.chillfocused.plugin.cfg`**：`FreezeInsteadOfClose`（bool，默认 `false`）→ 通过 `POST /api/v1/rules`
的**扁平**字段 `freeze` 推给 daemon（与现有 payload 一样只有基础类型）。

**HTTP**：`/status` 新增 U1 的扁平字段；新增 `POST /api/v1/thaw`（全量解冻，等价于 U3/U4）。
**不改** `/rules` 的既有字段语义（保护仍是只增不减）。

---

## 6. UI 文案（全部进 `PanelText`，用户可改）

| 键 | 中文 |
|---|---|
| `panel.freeze_instead` | 专注期间冻结而不是关闭 |
| `overlay.frozen_n` | 已冻结 {0} |
| `toast.frozen` | 已冻结 {0} |
| `toast.thawed` | 已恢复 {0} |
| `panel.freeze_hint` | 冻结只是暂停：计时结束后应用会原样恢复。 |

---

## 7. 测试清单（每条兜底都要有对应测试）

| 兜底 | 怎么测（沿用现有真进程 + 真 systemd 的 harness：`test_restart.py` / `test_protect_file.py`） |
|---|---|
| 冻结真的生效 | 真进程 + 对比 `/proc/<pid>/stat` 的 utime/stime 在冻结期间**不增长** |
| 解冻真的恢复 | 同上，解冻后继续增长 |
| H1 保护优先 | 保护名单里的名字 → 既不冻也不关（扩展现有保护测试） |
| H3 拒绝自锁 | 构造"目标 cgroup 是 daemon 自身祖先"的用例 → 必须拒绝并告警 |
| H6 共享 unit 降级 | 目标 unit 里放两个无关进程 → 只冻匹配的那个 PID，另一个不动 |
| H7 上限 | 一次匹配 30 个、上限 20 → 只冻 20 并告警 |
| **F3 ExecStopPost** | 启动真 daemon（临时 unit，带 `ExecStopPost=--thaw-all`）→ `kill -9` 它 → 断言目标已被解冻 |
| **F2 启动孤儿解冻** | 预置一个 `frozen.json` → 启动 daemon → 断言解冻且文件被清空 |
| F5 `--thaw-all` 自足 | daemon 未运行、systemd 不参与时执行它 → 仍能解冻（幂等，跑两遍无副作用） |
| U4 立即解冻 | `freeze.enabled=0` → 断言立刻解冻而不是下一轮 |
| I1 扁平契约 | 扩展 `test_every_response_dto_stays_flat` 覆盖新字段 |
| I2 PID 复用 | 伪造一个已死 PID 的冻结条目 → 解冻时不误发 `SIGCONT` |
| 门控回归 | 类似 `test_the_master_switch_cannot_bypass_a_stopped_timer`：未满足门控时**不得**冻任何东西 |

---

## 8. 分阶段

| 阶段 | 内容 | 风险 | 验收 |
|---|---|---|---|
| **P0 ✅ 已完成** | **只读解析与预览，不动任何进程**：PID → `/proc/<pid>/cgroup` → unit 解析、专属判定、`/status` 平铺字段报告"若启用会冻谁（含进程树大小）"；并实测 niri 对冻结客户端的反应 | 零 | ✅ 见 §10：`cgroupfs` 38 项 + 预览 14 项测试；真机判定正确（app scope → `unit`，终端启动 → `pid`）；niri 准入通过 |
| **P1 ✅ 已完成** | M-A（单 PID + 子树 `SIGSTOP`）+ `freeze.enabled`（默认关）+ F1/F2/F5 + 审计/事件/限流 + `/freeze`、`/thaw` 端点 + `--thaw-all`、`--frozen`、`SIGUSR1` | 低 | ✅ 24 项 freezer 单测 + 11 项真进程测试；真机端到端见 §11 |
| **P2 ✅ 已完成** | M-B1/M-B2（`systemctl --user freeze` + `cgroup.freeze` 回退 + `frozen 1` 确认 + 失败回滚）+ 专属判定 + 降级 M-A + F3 `ExecStopPost` 写进 systemd 单元 | 中 | ✅ 单元模式单测 + 真机 `systemctl --user freeze` 实测（§11） |
| **P3 ✅ 已完成** | 插件侧 `FreezeInsteadOfClose` 开关 + 面板复选框 + "立即恢复全部"按钮 + HUD「已冻结」行 + toast + 文案键 | 中 | ✅ 插件 203 项测试 + 契约测试断言扁平字段 |

**P0 是准入门槛**：§4.6 的合成器问题若确认有风险，产品的默认目标集要缩到"非图形应用"。

---

## 10. P0 实测记录（2026-10-03，本机 Arch + systemd 262 + cgroup2fs + niri）

**已实现（只读，一个进程都没冻）**

| 交付 | 位置 |
|---|---|
| 冻结目标判定（纯逻辑，38 项测试） | `focused/focused/core/cgroupfs.py` + `focused/tests/test_cgroupfs.py` |
| `/proc` 读 cgroup、读 `cgroup.events` | `ProcFS.cgroup()` / `ProcFS.cgroup_frozen()` |
| 扫描时的只读预览 + `/status` 平铺字段（14 项测试） | `FocusEngine._preview_freeze()` / `_publish_freeze_preview()` + `focused/tests/test_focused_engine.py` |

`/status` 新增（全部是标量或基础类型数组）：

```
freeze_unified_cgroup2  freeze_self_cgroup  freeze_preview_count
freeze_preview_names[]  freeze_preview_units[]  freeze_preview_plans[]
freeze_preview_reasons[]  freeze_preview_trees[]
```

**真机判定结果**（黑名单里只放探针名字，`dry_run` 打开，无任何信号发出）

| 目标的 cgroup | 判定 | 说明 |
|---|---|---|
| `…/app.slice/app-freeze-probe-1.service` | `plan=unit`，reason `dedicated-app-unit` | 整单元冻结可行（systemd app scope 约定） |
| `…/app.slice/dsh-subprocess-*.scope` | `plan=pid`，reason `shared-unit` | 共享 scope → 只冻进程树（H6 生效） |
| daemon 自身/其祖先 | `skip`，reason `self-ancestor` | H3 生效（连用户管理器也先被这条拦下） |

**合成器准入实验**（§4.6 的门槛，全程用我自己起的窗口，trap 保证立即恢复）

| 测量 | 冻结前 | 冻结期间 | 解冻后 |
|---|---|---|---|
| niri IPC `niri msg --json outputs` | 13–20 ms | **17–20 ms** | 21 ms |
| 目标 CPU tick 增量（1.2 s） | 121 | **0** | 120 |
| `cgroup.events` | `frozen 0` | `frozen 1` | `frozen 0` |
| niri 日志 | — | 无条目 | — |

**实测新增的四条事实**（写进 `lessons.md`）

1. `systemctl --user freeze <app-*.scope>` 对真实应用单元有效，`cgroup.events` 给出 `frozen 1`；解冻后 CPU 立刻恢复 → 机制可用。
2. **cgroup 冻结的进程在 `/proc/<pid>/status` 里仍显示 `S`**，不是 `T`；判断只能读 `cgroup.events`（与 SIGSTOP 相对）。
3. **scope 单元不报 `MainPID`**（`systemctl show -p MainPID --value` 为空），要拿单元里的进程得读 `/sys/fs/cgroup<path>/cgroup.procs`。
4. **合成器 fork 出来的应用通常落在合成器自己的 cgroup 里**（这台机器是 `app.slice/niri-autostart.service`）→ 对 niri 用户，"整单元冻结"只适用于真正拥有自己 `app-*.scope` 的应用，其余走 `plan=pid`（进程树）。这也说明 R2 在 M-A 路线下**必须包含子树**，否则浏览器只被冻住主进程。

---

## 11. 实现与验收记录（2026-10-03）

**代码**

| 文件 | 作用 |
|---|---|
| `focused/focused/core/cgroupfs.py` | 目标解析与"能不能冻/怎么冻"的纯判定（H3/H4/H6、cgroup v1 拒绝） |
| `focused/focused/core/freezer.py` | 冻结器：两种模式、落盘、PID 复用防护（含**子进程**）、幂等解冻、`--thaw-all` 自足实现 |
| `focused/chillfocus/config.py` | `freeze` 段（enabled/max_per_scan/confirm_timeout_ms/use_units/state_path）+ 校验 |
| `focused/chillfocus/engine.py` | 扫描分支、三条解冻规则、`thaw_stale`、`/status` 平铺字段、`recover_frozen` |
| `focused/chillfocusd.py` | `--thaw-all`、`--frozen`、`SIGUSR1`、启动孤儿解冻、退出解冻 |
| `focused/chillfocus/server.py` | `POST /freeze`、`POST /thaw` |
| `scripts/install-daemon.sh` | `ExecStopPost=… --thaw-all`（崩溃兜底） |
| 插件 6 个文件 | 开关、面板、HUD、toast、契约字段 |

**测试**：守护进程 **326** 项（其中 9 项破坏性默认跳过；冻结相关新增：cgroupfs 39 + 预览与自保护 19 + freezer 24 + 真进程 11 = 93），插件 **205** 项，扩展 **36** 项。

**真机证据**

| 场景 | 结果 |
|---|---|
| `systemctl --user freeze <app-*.scope>`（M-B1） | `cgroup.events` → `frozen 1`；目标 CPU tick 1.2s 内增量 **0** |
| 解冻后恢复调度 | CPU tick 增量 **120**（冻结前 121） |
| 冻结 Wayland 客户端时 niri 的响应 | IPC 延迟 13–20ms → **17–20ms**（无影响） |
| 端到端：推规则 → 冻结 → `kill -9` 守护进程 → `--thaw-all` | 目标 `T` → 守护进程死 → `{"thawed": 1}` → 目标恢复 `R`，状态文件清空 |

**过程中被测试抓出来的两个真 bug**（都写进了 `lessons.md`）：
1. 解冻只校验了根 PID 的 starttime，**子进程被复用时仍会收到 `SIGCONT`** → 改为逐 PID 记录并校验 `guards`。
2. `_housekeeping` 只看内存里的冻结表，**磁盘上的遗留记录不会被清** → 加 `ensure_loaded()`，开场前先读盘。

---

## 9. 我方补充的三条（你没勾但建议必须有的）

1. **T2 自救入口**（`--thaw-all` / `SIGUSR1` / `freeze.enabled=0` 立即解冻）。你只选了 T1 与 T4；
   没有 T2，daemon 卡住而没死时你只能注销会话。
2. **F3 `ExecStopPost` 兜底**：这是唯一能在 **daemon 被 SIGKILL** 后仍然放人的机制，成本是 unit 里一行。
3. **§4.6 合成器准入门槛**：冻结图形客户端在这台机器上可能导致 **niri/Xwayland 卡住**（机制上完全可能），
   必须先测；否则"冻结"可能比"关闭"更伤。
