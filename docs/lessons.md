# 经验与坑


> **状态说明（合并后）**：本文档写作时的宿主侧执行者是 `chillfocusd` 守护进程。
> 它已经**并入 Focused 应用**（`focused/`，`~/.local/bin/focusedd`，端口 8766），
> 那一层已经删除；下文的"守护进程"就是今天的 **Focused**。
> 当前架构见 [README](../README.md)，应用细节见 [focused.md](focused.md)。
> 开发过程中实测得到的结论，按"哪些是游戏的硬事实、哪些是 IMGUI 的坑、哪些是架构判断、
> 哪些是工作流习惯"分四部分记录。
>
> 已经写进 [design.md](design.md) 的机制（创作模式门控、fail-closed、PID 复用防护、
> `killpg` 禁令等）和 [ui-design.md](ui-design.md) 的界面梳理，这里不重复，只在相关处指路。

---

## 一、这个游戏的硬事实

### 运行时与工具链

| 项 | 值 |
|---|---|
| 引擎 | Unity **2022.3.62f2**，**Mono** 后端，URP |
| 加载器 | BepInEx **5.4.23.5 (win_x64)**，HarmonyX |
| 目标框架 | `net472` |
| 语言版本 | **C# 7.3** —— 没有 switch 表达式、没有 `??=`、没有 record |

### 不引用 `Assembly-CSharp` 也能做

用反射找类型/方法：`AccessTools.TypeByName("Bulbul.…")`、`AccessTools.Method`。
游戏命名空间是 `Bulbul`。这样游戏更新时不会因为程序集引用失效而整个插件加载不起来。

实测能补丁到的计时器类型：

- `Bulbul.PomodoroService`（番茄钟）
- `Bulbul.CountupTimerService`（正向计时）
- `Bulbul.TimerCoreService`（两者的公共内核，状态最稳）

### 补丁失败是常态

`Bulbul.UnlockConditionService.IsUnlocked` 一直报
`HarmonyException: IL Compile Error (unknown location)`。

**每个补丁都要单独 `try/catch` 并记日志**：一个坏补丁抛出的异常会让你误判"整个插件没加载"。

### 存档是完全可读的明文

Easy Save 3，**GZip + JSON，未加密**。宿主侧路径：

```
~/.local/share/Steam/steamapps/compatdata/3548580/pfx/drive_c/
  users/steamuser/AppData/LocalLow/nestopi/Chill With You/
    SaveData/Release/v2/<steamid>/*.es3
    Player.log
```

核心信号：`Bulbul.PomodoroData.LastUpdatedWorkDateTime.ticks`。
**计时器运行时游戏每 6–15 秒写一次存档，停止时该值冻结** —— 于是"存档是否新鲜"就等于
"计时器是否在跑"，不需要任何游戏内钩子。这是零风险的首选方案，探针（Harmony）只在
它不够用时才需要。

### Wine 与宿主的边界

- 网络：`127.0.0.1` 双向可达 ✓
- **PID 命名空间是共享的**：宿主能看到 `wineserver`、`steam.exe`、`explorer.exe`、
  `svchost.exe`、`UnityCrashHandler`、`xalia.exe`、`tabtip.exe` 等。这正是守护进程能按
  PID 干活、以及为什么保护名单里必须有这些名字的原因。
- 宿主**无法**让 Wine 执行 Linux 程序，反之亦然。

### 路径映射

插件看到的游戏路径是 `S:\steamapps\common\…`（Steam 库盘符映射），宿主上是
`~/.local/share/Steam/steamapps/common/…`。写配置/定位文件时两边要对齐，
否则插件会读到一个空文件。

---

## 二、BepInEx / Unity IMGUI 的坑

1. **在 chainloader 阶段创建的对象永远收不到 `Start`/`Update`/`OnGUI`。**
   可行做法：从后台线程用 `SynchronizationContext.Post` 延迟创建。
   ⚠️ 不要在回调里再 `Post` 自己（同帧自旋）。

2. **`GUIStyle` 有 8 个状态，只设 `normal` 会回退到内置皮肤。**
   典型症状：窗口背景在**鼠标悬停**或**拖动滚动条**时变灰，标题在悬停时变黑。
   必须把 8 个状态的 `background` **和** `textColor` 全部设上
   （见 `SetAllBackgrounds` / `SetAllTextColors`）。

3. **`GUILayout.BeginArea` 会硬裁剪到给定矩形**，而 `CalcHeight` **不含 margin**。
   手算高度/宽度必然出错。正确做法：给足空间，画完后用
   `GUILayoutUtility.GetLastRect()` 量回来，下一帧再用。

4. **`Texture2D.SetPixel(x, y)` 的 y=0 在底部**（OpenGL 约定）。
   非对称形状（勾号）会上下翻转：要么改成对称形状，要么按 `height - 1 - y` 换算。

5. **IMGUI 的圆角只能靠九宫格纹理**，且 `style.border` 必须**等于**纹理的圆角尺寸。
   边框大于纹理 → 中间区域被拉伸成灰块（"灰面板"bug 的成因）。

6. **⚠️ 不要改 `GUI.skin.verticalScrollbar` 的 `background`。**
   试过三次（圆角轨道、纯色轨道、带/不带 border），每次都让整条滚动条停止绘制或滑块
   不可见。只改 `verticalScrollbarThumb` 是安全的。这项实验已放弃，配置项默认关闭
   （`scrollbar.themed = 0`），**不要重开**。

7. **`GUI.skin.xxx = myStyle` 是全局的**，能让游戏内所有按钮变成你的样式——
   威力大，影响面也大。

8. **`BeginHorizontal` 按每个样式的 `margin.top` 对齐子控件。**
   同一行里标签和按钮对不齐，根因通常是上下内边距不对称、或 margin 不为 0。

9. **`JsonUtility` 的雷比想象的大：嵌套对象同样不绑。** 数组元素是自定义类时**静默返回 null**
   （进程列表就这样翻车过，靠自检打印 `objects=-1` 才发现）；更狠的是**嵌套对象也不绑**——
   `{"game_gate": {...}}` 经 `FromJson` 之后该字段就是 `null`，不抛异常、不打日志。
   2026-10-03 的「始终生效」就是这个：设置面板从 `status.game_gate.enabled` 读门控状态，而它
   永远是 `null` ⇒ 插件一直以为「始终生效 = 开」，每次点击都发同一个请求、弹同一句 toast，
   界面状态永远不动。同一原因让上一轮加的「守护进程重启检测」（读 `status.daemon.pid`）
   实际从未生效——策略与测试都对，输入永远是 0。
   **规则：给插件用的 DTO 只允许标量与基础类型数组。** 需要嵌套信息时，守护进程在**顶层再平铺
   一份**（`gate_enabled` / `gate_satisfied` / `daemon_pid` / `daemon_version` / `recent_names`），
   嵌套块留给其它客户端与人类看。两道自动防线：契约测试的 `test_the_status_dto_stays_flat`
   （解析 C# DTO，出现任何非基元字段即失败）与 `assertFieldsPresent`（DTO 的每个字段名都必须
   出现在真实响应里）。

10. **中文渲染**：`TextClipping.Overflow` 才是解决汉字底部被切的正确手段（加 padding 无效）；
    字体要挑有 `HasCharacter('中')` 的（游戏自带字体 → 系统字体 → 任意已加载的动态字体）。

---

## 三、架构决策

1. **拦截放在游戏外做。** 游戏内插件在 Wine 里杀进程不可靠，且游戏崩溃不该让拦截失效。
   插件只上报（规则 + 游戏状态），Linux 守护进程负责执行。规则引擎因此能用 Python 写、
   能单测、能 fail-closed。

2. **规则引擎必须 fail-closed，且有 PID 复用防护。** 保护规则优先于黑名单；用
   `/proc/<pid>/stat` 第 22 字段（进程启动时间）确认 PID 没被复用；**永远不用 `killpg`**
   （已有 AST 测试断言代码里不出现它）。详见 [design.md](design.md)。

3. **权威状态 + 超时 + 兜底。** Harmony 探针（权威，带 TTL）+ 存档轮询（兜底）。
   任何一个坏掉都不会导致误判。TTL 是必须的，否则探针挂掉后状态会永久陈旧。

4. **配置的 UX 分开处理**（收益最大的一条）：
   - 外观/布局/文案放**独立文件并热重载**，且在插件里生成**带完整中文注释的模板**。
     改字号、颜色、措辞都不用重启游戏——对一个每改代码都要重启的游戏，这是刚需。
   - **绝不重写用户的配置文件。** 插件只在文件不存在时创建；要加键时先读用户当前文件，
     把新键插进它所属的分组（见 `ConfigFile.Insert` / `Missing` / `SectionName`）。
     早期用脚本"同步模板"会抹掉用户的调整。
   - 加一个**预览模式**键：填合成数据把列表塞满，同时禁用所有写入。
   - 提供**缺失键报告**，而不是替用户改文件。

5. **诊断要主动。** 启动时一次性打印**采样值**：生成纹理的角落与中心 alpha、解析出的
   门控状态、字体来源。最难查的两个 bug（灰面板、"始终生效"误显示）都是靠自检行定位的。

6. **跨语言契约测试。** 守护进程（Python）解析插件（C#）的 DTO 字段名并断言与 JSON 键
   一致；协议漂移会立刻被测出来。

7. **部署要核对哈希。** `md5sum` 前后对比 —— 已因此抓到两次"部署了陈旧 DLL"
   （构建失败但没注意，复制了旧产物）。

8. **"只存在内存里"的状态要配一条"谁来重建"的路径。** 守护进程把插件推来的规则只放在内存
   （这是对的：它绝不该继续执行没人重发的规则），于是**守护进程重启后必须有人重发**。
   这件事由两条路径分担，而它们在这一点上原本并不对称：
   - 后台同步线程（`HeadlessRunner`）一直在跑，并且**每 30 秒无条件重发一次**——重启后的兜底
     是它做的，代价是最长 30 秒的执行空窗；
   - 覆盖层路径（`FocusRunner`）只在"规则变化"和"推送失败"时置脏标志，**对重启一无所知**，
     因此对这个空窗没有任何贡献。
   补上的信号是**守护进程自己的 pid**（每个 `/status` 都带）：pid 变了就是新进程，覆盖层于是
   立刻重发。这比"等一次请求失败"可靠——重启快到发生在两次轮询之间时，根本不会有失败请求。
   回归测试：`test_restart.py`（守护进程侧）+ `RuleSyncTests`（覆盖层的策略）。

9. **"我在 A 处没找到这个机制"不等于"系统没有这个机制"。** 上一条的判断先看到覆盖层缺重启信号，
   就写下了"重启后黑名单不再生效、直到玩家改设置"的结论；直到核对 `HeadlessRunner` 才看到那段
   30 秒兜底，真实的空窗是 ≤30 秒而不是无限。**结论的粒度要和证据的覆盖面匹配**：只读了一条
   路径，就只能说"这条路径没有"，不能说"系统没有"。分享结论时把"我读了哪些文件"一并说清楚，
   对方才能判断可信度。

10. **先确认"哪个组件真的在跑"，再叠加推理。** 排查「始终生效」时，我从"开关不更新"推断
   "状态解析拿到 null"，中途还错判成"覆盖层轮询已经停了"——推翻它的实验很简单：把守护进程
   **停够 10 秒**，覆盖层立刻打 `executor unreachable` 并弹重连 toast，证明它是活的（此前两次
   只停 1–2 秒，被 1 秒的轮询周期错过了）。教训：**"没看到日志"只说明"这段代码没走到"**，
   必须区分"代码没跑 / 条件没进 / 日志被截断"，然后设计一个能让它开口的实验（停服务、改一个值、
   去掉一个键），而不是在推理链上再叠一层推理。

---

## 四、工作流程

1. **把无 Unity 依赖的逻辑单独放文件，编进测试工程。** IMGUI 代码没法单测，但它依赖的
   纯逻辑可以：文本截断、配置解析/插入、行标记编解码、状态文案组装、过滤谓词、几何换算。
   测量函数用**委托注入**：

   ```csharp
   TextFit.Fit(text, width, s => style.CalcSize(new GUIContent(s)).x);  // 游戏里
   TextFit.Fit(text, 45f, s => s.Length * 10f);                          // 测试里
   ```

2. **拆大文件用"删定义 + 改调用点"，让编译器兜底。** 不要用大块文本切片 —— 曾算错范围
   把两个方法整个删掉（57 个编译错误）。每次改完必须看到 `0 错误`；构建失败绝不部署。

3. **派生样式要断言基底存在。** `new GUIStyle(null)` 是合法代码却产生空样式
   （字体/padding/clipping 全丢），编译器查不出来。用 `Derive(basis, name)`，基底为 null
   时告警——让这类错误可见。

4. **别把"没看到失败"当成"通过"。** 曾连续 5 轮因为没打印测试汇总行，误以为测试通过，
   实际是测试工程编译失败。**每轮都要让 `已通过! - 失败: 0` 出现在输出里**，
   不要用 `| tail -2` 这类会截掉错误行的截断。

5. **看不到的 UI 不要盲改。** 鼠标注入在 niri 下不可用（XTEST 指针移动被忽略），
   键盘注入可用。能截图就截图；不能截图就用**配置键强制进入要观察的状态**
   （`panel.advanced = 1` 让高级视图自动展开），这比猜强得多。

6. **命名要与配置键镜像。** `pad.state.top` ←→ `PadStateTop`。曾因命名不一致连续猜错锚点。

7. **记录你验证不了的东西。** 交付说明里明确写"哪些改动未经验证"，用户才知道该重点看哪里。

8. **写"该杀 / 不该杀"的测试前，先确认测试进程自己没撞上护栏。** 保护名单匹配的是
   `comm` + `/proc/<pid>/exe` 的名字 + `argv[0]` 三者（`ProcessInfo.name_candidates()`），
   而内置名单里有 `python3`、`bash`、各桌面组件——所以**一个 python 写的假进程天生就是
   受保护进程**，用它做"应当被拦"的对照组只会得到"没有事件"的假通过。
   可行做法：把一个无害二进制（`sleep`）复制成以标记命名的文件再执行，
   这样三个候选名都是标记，既不撞内置名单，也不撞环境里的任何东西。
   相关坑：`comm` 上限 15 字节，标记名超长会被内核截断，测试里的比较也要按 15 字节截断。

---

## 五、最小可用插件骨架

```csharp
[BepInPlugin("com.you.mod", "Your Mod", "0.1.0")]
public class Plugin : BaseUnityPlugin
{
    private void Awake()
    {
        foreach (var name in new[] { "Bulbul.PomodoroService", "Bulbul.CountupTimerService" })
        {
            try
            {
                var type = AccessTools.TypeByName(name);
                if (type == null) { Logger.LogWarning("no " + name); continue; }
                Harmony.CreateAndPatchAll(type, Info.Metadata.GUID);
                Logger.LogInfo("patched " + name);
            }
            catch (Exception ex) { Logger.LogWarning("patch failed: " + ex.Message); }
        }
    }
}
```

**只读状态优先**：只需要知道"计时器在不在跑"时，**先试存档轮询**（零风险、不改游戏内存），
不够用再上 Harmony 补丁。这个游戏的存档新鲜度信号足够可靠。

**配置热重载**：用文件 mtime 变化触发重读。注意 BepInEx 的 `Config.Reload()` 会重写文件、
改变 mtime，**重读后要重新记录时间戳**，否则会陷入循环。

---

## 六、本仓库里的对应实现与验证手法

纯逻辑单元（每个都编进 `ChillFocused.Tests`）：`TextFit`、`ConfigFile`、`OverlayText`、
`Toasts`、`HudState`、`OverlayLayout`、`Hotkeys`、`PanelStrings`、`AppFilter`。

**大文件拆分后的"搬移保真性"核对**（第 4、6 步用过，值得保留）：

1. 动手前把待拆文件快照到 `.build/<step>-baseline/`；
2. 搬完后用脚本按签名花括号配对取出被搬方法的原文，做归一化（改名的字段/前缀）后
   逐行在目标文件里查找，**未命中的行必须逐条给出解释**；
3. 单独比对**字符串字面量与文案键的集合**（搬移不允许改动任何一句 UI 文案）；
4. 再比对"方法名集合"，确认没有方法被静默删掉（编译器抓不到被删的死方法）。

这套核对能在"看不到界面"的前提下，把"纯搬移"这件事验证到与截图接近的可信度。

### 已知待办 / 已知问题

- `SettingsPanel` 的窗口框（`DrawWindow` 的视图切换与 Back/Advanced/Close 按钮）仍留在原文件。
  它持有 `_rect`、`_advanced`、`Open` 和拖动状态，单独拆出只会多一层回调转发，收益有限。
- （已修）高级页已拆成 `Core/AdvancedPane.cs`：它通过 `IAdvancedHooks` 按需读取面板的回调
  （runner 是在面板构造**之后**才接线这些委托的，所以不能在建页时捕获），页面状态只有
  半途输入的启动参数文本框。
- （已修）`UiTheme.Release()` 无人调用导致的九宫格纹理泄漏：现在由 `HudRenderer.Destroy()`
  在 runner 销毁时调用。
- （已修）语言开关：`ChineseHud` 已移除，界面只有中文。`PanelStrings.S` / `HudState.Resolve`
  现在只接受「键 + 内置文案」，英文参数从 163 处调用点消失。**用户配置文件不重写**，所以
  `com.chillfocused.plugin.cfg` 里的 `ChineseHud` 与 `com.chillfocused.text.cfg` 里的
  `panel.chinese_interface` 会成为孤立键（前者被 BepInEx 忽略，后者会让文本表报一条"未知键"）。
- （已修）`_defaultsCaptured` 与四个 `_togglePad*` 从未被读写 —— 拆 `PanelStyles` 时一并删除，
  插件侧编译告警从 5 条降到 0 条。
- （已修）`SettingsPanel` 的样式/Checkbox/样式助手已拆成 `Core/PanelStyles.cs`；
  列表/模式/过滤此前已拆成 `Core/AppPicker.cs`。
- （已修）**面板状态冻结**：`StatusResponse` 曾把门控与守护进程信息放进嵌套块，而 `JsonUtility`
  不绑嵌套对象 ⇒ 门控状态恒为 `null`、「始终生效」勾选框永远显示"开"且点不动。现在 `/status`
  在顶层平铺这些值，插件 DTO 只留标量与基础类型数组，并有契约测试强制这一形状。
- **冻结（cgroup freezer）的实测事实**（2026-10-03，本机 Arch + systemd 262 + cgroup2fs + niri）：
  `systemctl --user freeze <app-*.scope>` 有效且 `cgroup.events` 给 `frozen 1`；解冻后 CPU 立刻恢复
  （实测 tick 增量 121 → 0 → 120）；**被 cgroup 冻结的进程 `/proc/<pid>/status` 仍是 `S` 而不是 `T`**
  （所以只能读 `cgroup.events` 判断）；**scope 单元不报 `MainPID`**，要读 `cgroup.procs`；
  **合成器 fork 出的应用通常落在合成器自己的 cgroup**（本机 `app.slice/niri-autostart.service`），
  对它整单元冻结会连合成器一起冻；实测把 Wayland 客户端冻住 ~3 秒**没有拖慢 niri**（IPC 13–20 ms 不变）。
  完整设计与兜底见 `docs/freeze-mode.md`。
- **改变产品语义时，用户的"文字表"也是要迁移的配置。** `com.chillfocused.text.cfg` 里存的是
  用户自己改过的界面措辞（他写过「屏蔽名单」「拦截行为」），插件只会在缺键时用内置默认值，
  **所以改了内置文案不会自动改到他屏幕上**。做「只冻结、不关闭」这次改动时，必须同时：
  ①按语义定向改写用户文件里的措辞（保留他自己的简写风格，只把"关闭/拦截/屏蔽"换成"冻结/恢复"）；
  ②把新键**追加**进他的文件（否则新控件的新文案对他不可见）；③改前备份。这三步一步都不能少，
  否则"界面上不再提关闭"这个需求只在新装机器上成立。
- **拒绝的粒度要选对：过度拒绝会让功能静默失效。** 第一版把"目标 cgroup 包含 daemon 自己"整条路径
  都判成 `skip`（理由：整单元冻结会自锁）。这在真机上直接让冻结**什么都不做**——只要用户从同一个
  终端启动守护进程和被屏蔽的应用，两者就在同一个 cgroup。正确做法是把拒绝**只加在真正危险的那一步**：
  不许整单元冻结（降级为进程树），同时把 daemon 自己的 PID 从要冻结的进程树里剔除（`_freezable_pids`），
  根进程被剔除才整棵放弃。教训：安全规则要精确到"哪一个动作不安全"，否则它会把整个功能一起关掉，
  而且不报错。
- **对接一个还不存在的程序时，"契约先行"是唯一能落地的做法。** 本机没有 Focused 的源码，于是先写
  `docs/focused-protocol.md`（三个端点 + 语义 + 交接规则 + 给对方的实现清单），在**自己这侧实现同一套
  接口**当参考实现（既是浏览器扩展要的桥，也是 Focused 的模板），再用桩服务器做真 HTTP 集成测试、
  把**真实守护进程的响应**抓下来当扩展的 fixture。这样对方只要照契约实现就能接上，
  而双方的测试都不依赖对方存在。
- （已修）插件侧**所有响应 DTO** 都清成了标量+基础类型数组：删掉 `ProcessListResponse.processes`
  与它永不生效的兜底分支、`RulesResponse.applied`、`ScanResponse.outcomes`、`EventListResponse.events`，
  以及随之无用的 `RuleApplied` / `OutcomeItem` / `EventItem` / `ProcessItem` 的 `[Serializable]`。
  守护进程照旧发这些嵌套块与对象数组——**它们不是"给别的客户端"，而是守护进程自己测试的参照物**
  （`test_server.py` 拿对象数组当参考形状去交叉验证并行数组），所以只能删插件侧的声明，
  不能删守护进程侧的字段。**契约测试现在对每个响应 DTO 都强制扁平形状**，"声明了却永远绑不上"
  的字段不会再出现。
