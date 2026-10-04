# 开源与分发规划

> 目标：**别人能在五分钟内装上并用起来**，每个组件走它自己最省事的渠道。
> 本文是实施前的规划 + AUR 打包的完整步骤（依据 Arch 官方文档，见文末引用）。
> **现状**：AUR 关闭新用户注册（2026 年恶意提交事件后），因此后端暂以 GitHub Release 分发；
> PKGBUILD 已通过 `makepkg` 实测，等注册恢复后直接按 §3 提交。

---

## 0. 现状盘点（实测）

| 项 | 状态 |
|---|---|
| 程序本身 | ✅ 三套测试全绿（Focused 229 / 插件 211 / 扩展 53），已在本机真机运行 |
| **LICENSE** | ❌ **缺**（开源必需；AUR 也要求仓库里有许可文件） |
| **`pyproject.toml`** | ❌ **缺**（没有它就不能 `pip install` / `pipx` / `uv tool install`，AUR 也没法构建 wheel） |
| **CHANGELOG.md** | ❌ 缺（Thunderstore 会渲染它；用户看变更） |
| **CI（GitHub Actions）** | ❌ 缺 |
| `.gitignore` | ⚠️ 原先**没有**忽略 `.backup/`、`.tmp/` —— 已在本次补上 |
| 版本号 | `focused 0.1.0`、插件 `0.1.0`、扩展 `manifest` 0.4.0（三个组件各自独立） |
| 仓库 | 还不是 git 仓库；清洗后可发布体积 **约 8 MB**（`.build/` 308 MB、`.backup/` 41 MB、`dist/`+`third_party/` 2.4 MB 都必须排除） |
| Thunderstore 清单 | ⚠️ `packaging/thunderstore/manifest.json` 还是旧信息（`name: ChillFocus`、描述里写着"守护进程会终止进程"）——需重写 |
| 图标 | ✅ `packaging/thunderstore/icon.png` 已是 256×256（Thunderstore 硬性要求） |
| PyPI 包名 | ❌ `focused` 已被占用；`focusd` / `focusctl` / `chillfocused` / `focused-app` **可用** |
| AUR 包名 | ✅ `focused` / `focused-git` / `chillfocused` 均未被占用 |
| Thunderstore 社区 | ❌ 该游戏**没有社区**（平台按游戏开版，目前只有另一个 "Cast n Chill"） |

> ⚠️ **隐私红线**：`.backup/config-*/` 里是你这台机器的真实配置（`~/.config/chillfocus/config.json`、游戏的 `com.chillfocused.*.cfg`），第一次 `git add` 前必须确认它们被忽略，否则会公开你的黑名单与保护名单。已在 `.gitignore` 里加上 `.backup/` 与 `.tmp/`。

---

## 1. 渠道策略（一张表）

| 组件 | 主渠道 | 备用 / 补充 |
|---|---|---|
| **Focused 后端**（Python，systemd user 服务） | **GitHub Release**（wheel + sdist + `install.sh`）；AUR `focused` / `focused-git` 已就绪，**待 AUR 恢复注册**后提交 | **PyPI `focusd`** → `uv tool install focusd` / `pipx install focusd`（所有发行版）；GitHub Release 的 wheel/sdist；仓库里的 `install.sh`（不依赖包管理器） |
| **ChillFocused 游戏插件**（BepInEx mod） | **GitHub Release 只放一个 `ChillFocused.dll`** + `scripts/install-bepinex.sh`（`--dll <路径\|URL>` + sha256 校验 + 清理旧位置 + 结尾自检） | 可选 AUR 助手包（只装到 `/usr/share`，由用户运行 helper 复制进游戏目录）；Nexus Mods；Thunderstore 需先申请社区（且它无法携带后端依赖） |
| **Focused 浏览器扩展**（MV3） | **Chrome Web Store**（自动更新，用户一键装） | Release 里的 zip + 「加载已解压的扩展程序」；可选 AUR 包把解压目录装到 `/usr/share/focused-extension/` |
| 文档 / 变更 | 仓库 `README.md`、`CHANGELOG.md`、`docs/` | 每个 Release 的说明 |

**为什么插件不走 AUR 安装本体**：AUR 包**不允许写 `$HOME`**，而 mod 必须落到用户的 Steam 库（`~/.local/share/Steam/...`）里。正确形态是：包只把文件装到 `/usr/share/`，装一个 helper 命令（`chillfocused-install-mod`）由用户自己跑，helper 负责探测游戏目录并复制。

---

## 2. 仓库结构：建议**单仓库（monorepo）**

```
chillfocused/                     ← GitHub 仓库名（建议）
├── LICENSE                       ← 新增（见 §7 许可选择）
├── README.md                     ← 面向用户：装什么、怎么用
├── CHANGELOG.md                  ← 新增：Keep a Changelog 格式
├── CONTRIBUTING.md               ← 新增（可选）
├── focused/                      ← 后端应用 + pyproject.toml（新增）
├── plugin/ChillFocused/          ← 游戏插件（BepInEx mod）
├── focused-extension/            ← 浏览器扩展（MV3）
├── packaging/
│   ├── thunderstore/             ← mod 清单与图标（重写 manifest）
│   ├── aur/focused/              ← 新增：PKGBUILD + .SRCINFO + .INSTALL
│   └── aur/focused-git/          ← 新增：VCS 变体
├── scripts/                      ← 安装/打包/测试脚本
├── docs/
└── .github/workflows/            ← 新增：ci.yml + release.yml
```

**单仓库的理由**：三者共享同一份协议（`docs/focused-protocol.md`）、同一个版本节奏、同一套测试入口；三个小仓库会立刻产生"改协议要同步三个 PR"的负担。AUR 需要的是**独立的 pkgbase 仓库**（`ssh://aur@aur.archlinux.org/focused.git`），这与 GitHub 上是单仓库并不冲突 —— `packaging/aur/*` 只是源码，发布时复制到 AUR 仓库即可。

---

## 3. AUR 打包：完整步骤

### 3.1 概念与硬规则（来自官方文档）

- AUR **只放 `PKGBUILD`**（构建脚本），不放二进制包；用户 `makepkg -si` 自己构建。
- **一个 pkgbase 一个 git 仓库**，只允许 `master` 分支；推送必须包含 `PKGBUILD` 与 `.SRCINFO`。
- 命名：**发布版本包不加后缀**（`focused`），**跟仓库走的包加 `-git`**（`focused-git`），预编译二进制加 `-bin`。
- 每条提交**必须更新 `pkgver` 或 `pkgrel`**（只有拼写修正才不更新）；`-git` 包**不要**因为上游有新提交就 bump。
- 提交信息里不能只有版本号刷屏；**提交者信息用的是你本机 git 的姓名邮箱**，AUR 上很难改。
- 只支持 `x86_64`；不得打包官方仓库里已有的软件；不得用 `replaces`（改用 `conflicts`/`provides`）。
- **包源码本身**（PKGBUILD 等）按 Arch 规定应使用 **0BSD** 并配 `LICENSE`/`REUSE.toml`；而 `license=()` 里写的是**上游软件**的许可证，必须是 SPDX 标识符。
- 官方明确警告：**自动化更新账号出问题会被直接删包**，必须人工过一遍每次上游变更。

### 3.2 我们的 `focused` PKGBUILD（草案）

前置：仓库里要有 `pyproject.toml`（PEP 517），版本号与 tag 一致；后端是**纯标准库**，因此 `arch=('any')`。

```bash
# Maintainer: Your Name <you at example dot com>
pkgname=focused
pkgver=0.2.0
pkgrel=1
pkgdesc="Suspend distracting applications while you focus (backend for ChillFocused)"
arch=('any')
url="https://github.com/YOUR-USER/chillfocused"
license=('MIT')                     # ← 与 LICENSE 文件一致（SPDX）
depends=('python')                  # 运行时只用标准库，Arch 上即 python
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
checkdepends=('python-pytest')      # 如果 check() 用 pytest；用 unittest 则不需要
provides=('focused')
source=("$pkgname-$pkgver.tar.gz::https://github.com/YOUR-USER/chillfocused/archive/refs/tags/focused-v$pkgver.tar.gz")
sha256sums=('SKIP')                 # ← 用 updpkgsums 换成真实校验和
install="$pkgname.install"

build() {
  cd "chillfocused-focused-v$pkgver/focused"
  python -m build --wheel --no-isolation
}

check() {
  cd "chillfocused-focused-v$pkgver/focused"
  python -m unittest discover -s tests -t .   # 229 项，不需要额外依赖
}

package() {
  cd "chillfocused-focused-v$pkgver/focused"
  python -m installer --destdir="$pkgdir" dist/*.whl

  # systemd 用户单元：包只负责装到 /usr/lib/systemd/user/，绝不替用户 enable
  install -Dm644 packaging/focused.service \
    "$pkgdir/usr/lib/systemd/user/focused.service"

  # 许可证
  install -Dm644 ../LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"

  # 文档
  install -Dm644 ../README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
```

对应的 `focused.install`（安装/升级后的提示，**这是引导用户的关键**）：

```bash
post_install() {
  cat <<'EOF'
Focused 已安装。首次使用：
  1) 启动服务：  systemctl --user enable --now focused
  2) 打开看板：  http://127.0.0.1:8766/
  3) 首次运行会在 ~/.config/focused/config.json 生成配置（默认不冻结任何应用）。
  Steam 启动选项建议填： focused-launch %command%
EOF
}

post_upgrade() { post_install; }
pre_remove()   { systemctl --user stop focused.service 2>/dev/null || true; }
```

> 单元文件里应当有 `ExecStopPost=/usr/bin/focusedd --config %h/.config/focused/config.json --thaw-all` ——
> 这是"崩溃也能放人"的兜底，AUR 版必须保留。

### 3.3 `focused-git` 变体

```bash
pkgname=focused-git
provides=('focused')
conflicts=('focused')
makedepends=('git' python-build python-installer python-wheel python-setuptools)
source=('focused::git+https://github.com/YOUR-USER/chillfocused.git#branch=main')
sha256sums=('SKIP')                 # VCS 源免校验和
pkgver() {
  cd focused
  ( set -o pipefail
    git describe --long --tags --abbrev=7 --match 'focused-v*' 2>/dev/null \
      | sed 's/^focused-v//;s/\([^-]*-g\)/r\1/;s/-/./g' ) \
    || printf 'r%s.%s' "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
}
```

### 3.4 提交与维护流程

```bash
# 一次性准备：AUR 账号 + 专用 SSH key（官方建议不要复用既有 key）
ssh-keygen -f ~/.ssh/aur            # 公钥贴到 AUR → My Account
cat >> ~/.ssh/config <<'EOF'
Host aur.archlinux.org
  IdentityFile ~/.ssh/aur
  User aur
EOF

# 建包（pkgbase 就是包名，先克隆空仓库）
git -c init.defaultBranch=master clone ssh://aur@aur.archlinux.org/focused.git
cd focused
cp ../chillfocused/packaging/aur/focused/{PKGBUILD,.install} .
updpkgsums                          # 用真实 sha256 覆盖 SKIP
makepkg -si                         # 本地构建并安装，验证能跑
namcap PKGBUILD && namcap focused-*.pkg.tar.zst   # 官方推荐的静态检查
makepkg --printsrcinfo > .SRCINFO   # 元数据变了就必须重新生成
git add PKGBUILD .SRCINFO focused.install
git commit -m "focused 0.2.0-1: initial packaging"
git push
```

**维护节奏**：上游发新版 → 改 `pkgver`、`pkgrel=1`、`updpkgsums`、重新 `--printsrcinfo`、本地 `makepkg -si` 验证、推送。AUR 页面上不要只发"bump 到 x.y.z"的空评论。

### 3.5 可选：`focused-extension` 与 mod 的 AUR 包

- `focused-extension`：把扩展解压目录装到 `/usr/share/focused-extension/`，`.install` 里提示"在 `chrome://extensions` 打开开发者模式 → 加载已解压的扩展程序 → 选该目录"。**不能**替用户装进浏览器。
- `chillfocused-bepinex`：把 `ChillFocused.dll` 与安装脚本装到 `/usr/share/chillfocused/`，提供 `/usr/bin/chillfocused-install-mod`（探测 Steam 库、判断 Proton 前缀、复制 DLL、打印后续步骤）。**不能**直接写 `$HOME`。

---

## 4. 非 Arch 用户的安装路径

1. **PyPI**（推荐，最省事）：包名用 **`focusd`**（`focused` 已被占用）。

   ```bash
   uv tool install focusd      # 或 pipx install focusd
   focusedd --write-default-config ~/.config/focused/config.json
   focusedd                     # 前台跑；或自己写 user unit
   ```

   PyPI 需要：`pyproject.toml` + 一次 `uv build && uv publish`（或 `python -m build` + `twine upload`），并支持 **Trusted Publishing**（GitHub Actions OIDC，无需存 API token）。

2. **一行安装脚本**（无包管理器、无 Python 环境也能用）：

   ```bash
   curl -fsSL https://raw.githubusercontent.com/YOUR-USER/chillfocused/main/install.sh | sh
   ```

   脚本要做的事：检测 `python3`（≥3.9；实测代码只用标准库）→ 下载对应 Release 的 wheel/sdist → 建 venv 到 `~/.local/share/focused/venv` → 写 `~/.local/bin/focusedd` 包装 → 装 `~/.config/systemd/user/focused.service`（含 `ExecStopPost` 兜底）→ 提示 `systemctl --user enable --now focused`。
   *现有 `scripts/install-focused.sh` 已经做了大半，需要把"从当前 checkout 复制"改成"从 Release 下载"，并注意源里的 `focusedd.py` 路径在打包后会变成 `focused.cli`（见 §6 改动清单）。*

3. **手动**：Release 里下载 wheel，`python3 -m pip install --user ./focusd-*.whl`。

---

## 5. 游戏插件与浏览器扩展的分发

### 5.1 插件（BepInEx mod）

- **Release 资产**：只有 `ChillFocused.dll` 与 `ChillFocused.dll.sha256`。一个文件，没有 zip
  结构可以解压错一层。config 目录里的 `com.chillfocused.plugin.cfg` / `.panel.cfg` / `.text.cfg`
  **不是发布物**：首次运行自动生成，而且面板布局与文案本来就是可选覆盖。
- **为什么单 DLL 就够**（已核实）：默认布局与全部文案都在 DLL 里（`PanelSpec.Defaults()`、`PanelText`
  三语言表），插件不读自身目录、不读字体文件（借游戏已加载的 CJK 字体）、贴图程序化生成。
  运行期需要的只有 BepInEx 自己 + 这一个文件。
- **打包**：`scripts/package-mod.sh` —— `dotnet build -c Release -p:SourceRevisionId=<sha>`，
  产出 `dist/mod/ChillFocused.dll` + `.sha256`。版本来自 `ChillFocused.csproj` 的 `<Version>`，
  由 `VersionConsistencyTests` 锁住它与源码常量一致；提交哈希进 `AssemblyInformationalVersion`，
  插件启动日志会打印，于是"装的是哪个构建"可以从 DLL 本身回答。
- **安装**：`scripts/install-bepinex.sh [--dll PATH|URL] [--sha256 HEX] [--dry-run]`：
  多 Steam 库探测（读 `libraryfolders.vdf`，含 flatpak Steam 路径）、BepInEx 5.4.23.5 固定 sha256 校验、
  **清理旧位置**（`plugins/ChillFocus/`、`plugins/ChillFocused/`、`plugins/ChillFocus.dll`）、
  装到 `plugins/ChillFocused.dll`、结尾自动跑自检。
- **重复 DLL 是真实故障**：BepInEx 加载 `plugins/` 下**所有** DLL（含子目录），两份 = 同一套功能跑两遍。
  除安装脚本清理外，插件启动时也扫描并在日志里警告（`PluginInstallCheck`，纯逻辑 + 9 项测试）。
- **自检**：`scripts/doctor-focused.sh`（安装为 `focused-doctor`）逐项检查后端、游戏目录、BepInEx、
  winhttp override、DLL 份数、日志里的加载记录、两端地址与令牌是否一致；每条 ✗ 都给出确切命令。
- **README**：`plugin/ChillFocused/README.md`（中）与 `README.en.md`（英）按这个生态的惯例写
  （徽章、效果演示、它解决什么问题、特性表、安装含目录结构图、与其他 Mod 的关系、许可、致谢）。
- **Thunderstore**：该游戏**没有社区**（实测），要么先申请开版，要么走 Nexus Mods / GitHub。`packaging/thunderstore/manifest.json` 需要重写（`name: ChillFocused`、新描述、`website_url`、`dependencies` 需指向对应游戏的 BepInExPack —— 没有社区就没有 BepInExPack，这也是"先申请社区"的原因）。
- **mod manager（r2modman/Gale）**依赖 Thunderstore 清单，社区没开之前用不了。

### 5.2 浏览器扩展

- **Chrome Web Store**（主）：一次性 5 美元开发者注册费 + 审核；优点是自动更新、一键安装。
- **Release zip**（备）：解压后在 `vivaldi://extensions` / `chrome://extensions` 打开开发者模式 → 加载已解压的扩展程序。
- 注意 manifest `version` 每次发版必须递增（商店要求），当前 0.4.0。

---

## 6. 开源前必须补齐的改动（清单）

| # | 改动 | 为什么 |
|---|---|---|
| 1 | 加 `LICENSE` | 开源与 AUR 都要求；见 §7 |
| 2 | 加 `focused/pyproject.toml`（PEP 517；`[project.scripts] focusedd = "focused.cli:main"`） | pip/PyPI/AUR/uv 全部依赖它 |
| 3 | 把 `focusedd.py` 收进包内（`focused/focused/cli.py`），根目录留一个 shim | 打包安装后才有一个可执行入口；现有脚本仍可用 shim |
| 4 | 版本单一来源：`focused/__init__.py:__version__`，`pyproject.toml` 用动态版本读取 | 避免两处版本漂移 |
| 5 | `CHANGELOG.md`（Keep a Changelog） | 用户与商店/平台都要看 |
| 6 | 重写 `packaging/thunderstore/manifest.json` | 现内容是旧产品名与"终止进程"的过时描述 |
| 7 | `.gitignore` 补 `.backup/`、`.tmp/` | ✅ 本次已做（隐私红线） |
| 8 | 清理仓库里的开发残留（`.tmp/`、`tools/netprobe/obj`、`scripts/__pycache__`） | 首个提交要干净 |
| 9 | `README.md` 增加三行"安装"入口（AUR / PyPI / Release） | 新用户第一眼要看到 |
| 10 | `.github/workflows/ci.yml` + `release.yml` | 让别人相信"测试是真的在跑" |
| 11 | `docs/focused.md` 里的 `scripts/install-focused.sh` 说明改成两种路径（checkout / Release） | 与 §4 一致 |
| 12 | 插件 Release 打包脚本 `scripts/package-mod.sh` | 手工打 zip 会错结构（必须 DLL 在 `BepInEx/plugins/...`） |

---

## 7. 许可证选择（需要你定）

| 选项 | 适合 | 代价 |
|---|---|---|
| **MIT**（推荐） | 想让人随便用、方便将来被发行版收录 | 别人可以闭源再发布 |
| **Apache-2.0** | 同 MIT，但含专利授权与 NOTICE 条款 | 文件更长，需带 NOTICE |
| **GPL-3.0-or-later** | 想强制衍生作品也开源 | 与部分生态（如某些 mod 平台）兼容性差 |

- 注意：**AUR 上的 `PKGBUILD` 等包源码**按 Arch 规范用 **0BSD**（与你的上游许可无关），所以 `packaging/aur/*` 里会放一份 0BSD 的 `LICENSE`。
- 建议三组件统一一个许可（简化心智与合规检查）；若插件想用不同许可（mod 生态常见 MIT），在 `plugin/` 下另放一份并说明即可。

---

## 8. CI 骨架

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]
jobs:
  focused:
    runs-on: ubuntu-latest
    strategy: { matrix: { python: ['3.9', '3.13'] } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '${{ matrix.python }}' }
      - run: cd focused && PYTHONPATH=. python -m unittest discover -s tests -t .
  extension:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd focused-extension && node --test test/
  plugin:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-dotnet@v4
        with: { dotnet-version: '8.0.x' }
      - run: dotnet test plugin/ChillFocused.Tests/ChillFocused.Tests.csproj
```

`release.yml`：tag 触发（`focused-v*` / `plugin-v*` / `ext-v*`）→ 构建 wheel+sdist、mod zip、extension zip → 作为 Release 资产上传；PyPI 用 Trusted Publishing；AUR 更新**不要**做成无人工的全自动（官方警告）；可以用 `KSXGitHub/github-actions-deploy-aur` 之类工具，但每次仍应本地 `makepkg -si` 验证。

---

## 9. 用户看到的最短路径（目标形态）

```bash
# Arch（后端）
yay -S focused && systemctl --user enable --now focused
xdg-open http://127.0.0.1:8766/

# 其他发行版（后端）
uv tool install focusd && systemctl --user enable --now focused

# 游戏插件（Linux/Proton）
curl -fsSL https://github.com/YOUR-USER/chillfocused/releases/latest/download/install-mod.sh | sh

# 浏览器扩展
# Chrome Web Store 安装链接（或用 Release zip 加载已解压目录）
```

---

## 10. 参考

- [AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines) —— 提交流程、命名、`.SRCINFO`、SSH、维护规则
- [Arch package guidelines](https://manual.archlinux.page/package-guidelines/) —— 目录规范、许可、etiquette、`namcap`
- [Python package guidelines](https://manual.archlinux.page/package-guidelines/python/) —— PEP 517 构建（`python-build` + `python-installer`）、`check()`
- [VCS package guidelines](https://manual.archlinux.page/package-guidelines/vcs/) —— `-git` 命名与 `pkgver()`
- [systemd/User（ArchWiki）](https://wiki.archlinux.org/title/Systemd/User) —— 包提供的用户单元放 `/usr/lib/systemd/user/`，由用户 `systemctl --user enable`
- [Thunderstore：创建一个包](https://wiki.thunderstore.io/mods/creating-a-package) —— `icon.png` 256×256、`README.md`、`manifest.json` 必须在 zip 根
- [Thunderstore 社区 API](https://thunderstore.io/api/experimental/community/) —— 本游戏暂无社区（实测）
