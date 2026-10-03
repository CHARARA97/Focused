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

## 4. AUR

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
