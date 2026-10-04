# 发布后端

版本号的唯一来源是 `pyproject.toml` 的 `version`；`focused/__init__.py` 与
`packaging/aur/focused/PKGBUILD` 的 `pkgver` 由 `tests/test_version.py` 校验一致。

## 1. 提交前

```bash
scripts/run-tests.sh              # 全部测试
scripts/run-tests.sh --offline    # 无网络时使用本地包缓存
```

## 2. 构建产物

```bash
python -m build --no-isolation
```

`--no-isolation` 使用已安装的 `setuptools` 与 `wheel`，不在构建时联网下载构建依赖。
产物为 `dist/focusd-<版本>-py3-none-any.whl` 与 `dist/focusd-<版本>.tar.gz`。

构建后确认安装路径仍然可用（不需要 pip、不需要虚拟环境）：

```bash
TMP=$(mktemp -d)
HOME="$TMP" scripts/install.sh --no-service \
    --wheel "file://$PWD/dist/focusd-<版本>-py3-none-any.whl" --scripts-dir "$PWD/scripts"
HOME="$TMP" "$TMP/.local/bin/focusedd" --version
rm -rf "$TMP"
```

## 3. 打标签并发布

发布资产使用**稳定文件名**，这样 `releases/latest/download/<名字>` 永远有效
（安装脚本按这些名字下载自身所需的辅助脚本）：

```
install.sh             ← scripts/install.sh
focused-launch.sh      ← scripts/launch-focused.sh
focused-doctor.sh      ← scripts/doctor-focused.sh
focusd-<版本>-py3-none-any.whl
focusd-<版本>.tar.gz
```

```bash
git tag -a v0.1.0 -m "Focused 0.1.0"
git push origin v0.1.0

mkdir -p dist/release
cp scripts/install.sh         dist/release/install.sh
cp scripts/launch-focused.sh  dist/release/focused-launch.sh
cp scripts/doctor-focused.sh  dist/release/focused-doctor.sh

gh release create v0.1.0 dist/release/* dist/*.whl dist/*.tar.gz \
  --title "Focused 0.1.0" --generate-notes
```

发布后验证文档里的两条命令：

```bash
curl -fsSL https://github.com/CHARARA97/Focused/releases/latest/download/install.sh | sh
```

## 4. AUR（待注册开放）

AUR **目前关闭新用户注册**（2026 年恶意提交事件后 Arch 侧收紧），因此这一节暂时无法执行。
PKGBUILD 已就绪并通过 `makepkg` 实测；等注册开放后，注册账号、把 SSH 公钥填进 AUR 账号设置，再按下面执行。

AUR 需要**网络**（`makepkg` 要下载 tag 归档）且必须在 GitHub 标签存在之后进行：

```bash
cd packaging/aur/focused
updpkgsums                      # 把 PKGBUILD 里的 sha256sums=('SKIP') 换成真实校验和
makepkg --printsrcinfo > .SRCINFO
namcap PKGBUILD                 # 可选的规范检查
makepkg -si                     # 本地安装验证

# 推送：AUR 的每个包是独立 git 仓库
git clone ssh://aur@aur.archlinux.org/focused.git /tmp/aur-focused
cp PKGBUILD focused.install .SRCINFO /tmp/aur-focused/
cd /tmp/aur-focused && git add -A && git commit -m "0.1.0-1" && git push
```

`focused-git` 同理，包名换成 `focused-git`，不需要 `.install`。

### PKGBUILD 为什么用 `noextract` + `prepare()`

GitHub 的归档解出来的顶层目录是 `<仓库名>-<tag 去掉 v>`，即 `Focused-0.1.0`，而包名是
`focused`。与其在 `build()/check()/package()` 里猜目录名，PKGBUILD 自己用
`bsdtar --strip-components=1` 解到固定的 `$srcdir/src`，因此仓库改名或 tag 格式变化都不会
再让它失败。`makepkg` 生成的 `src/`、`pkg/` 是构建残留，可以随时删除。

