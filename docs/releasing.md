# 发布到 GitHub

发布以 **tag** 为唯一触发点；日常提交不影响 release。

## 1. 版本

同时改：

- `focused/__init__.py` 的 `__version__`
- `pyproject.toml` 的 `version`
- `CHANGELOG.md`

版本必须与 tag 一致：tag `v0.2.0` ↔ `0.2.0`。

## 2. 本地验收

```bash
python3 -m pytest -q
python3 -m build
python3 -m venv /tmp/focused-venv
/tmp/focused-venv/bin/pip install dist/*.whl
/tmp/focused-venv/bin/focused --version
/tmp/focused-venv/bin/focused --help
```

## 3. 推送并打 tag

```bash
git add -A
git commit -m "release: v0.2.0"
git push origin main
git tag v0.2.0
git push origin v0.2.0
```

## 4. 自动发布

`.github/workflows/release.yml` 在 `v*` tag 上执行：

1. 跑完整后端测试；
2. 校验 tag 与包版本一致；
3. 构建 wheel + sdist；
4. 在独立 venv 里安装 wheel 并运行 `focused --version/--help`；
5. 生成 `SHA256SUMS`；
6. 把 `install.sh`、`doctor-focused.sh`、`launch-focused.sh`、wheel、sdist 上传为 Release 资产；
7. 创建 GitHub Release。

## 5. 发布后验证

```bash
gh release view v0.2.0
curl -fsSLO https://github.com/CHARARA97/Focused/releases/latest/download/install.sh
less install.sh          # 先读，再执行
sh install.sh
~/.local/bin/focused --help
```

`install.sh` 会自动取 latest release 的 wheel 和两个 helper 脚本。

## Arch Linux 本地包

`packaging/arch/` 是本地 `makepkg` 包，不是 AUR 提交。

```bash
cd packaging/arch
updpkgsums          # 版本/wheel 变化后
makepkg -si
systemctl --user enable --now focused
```

`namcap focused-*.pkg.tar.zst` 应只剩 Python entry point 的提示。
