# Arch Linux 本地包

`focused` 是 **Arch 本地包**，不是 AUR 提交：从已发布的 wheel 安装，
适合在当前机器上直接用 `makepkg` 安装。

## 构建安装

需要 `base-devel`；`python-installer` 是 makedepends，`makepkg -s` 会处理。

```bash
git clone https://github.com/CHARARA97/Focused.git
cd Focused/packaging/arch
makepkg -si
```

然后启用 user service：

```bash
systemctl --user daemon-reload
systemctl --user enable --now focused
focused --help
```

看板：<http://127.0.0.1:8766/>

## 安装内容

```text
/usr/bin/focused
/usr/bin/focusedd              # 兼容别名
/usr/bin/focused-doctor
/usr/lib/systemd/user/focused.service
/usr/share/doc/focused/README.md
/usr/share/licenses/focused/LICENSE
```

## 更新

改版本或重新发布 wheel 后：

```bash
cd Focused/packaging/arch
updpkgsums
makepkg -si
systemctl --user restart focused
```

`updpkgsums` 会重新计算 `sha256sums`。

## 卸载

先停止服务并解冻：

```bash
systemctl --user disable --now focused
focused --thaw-all
sudo pacman -Rns focused
```

配置和状态默认保留：`~/.config/focused/`、`~/.local/share/focused/`。

## 注意

- 包名与命令都是 `focused`。
- 当前未提交 AUR；日后提交时把本目录作为 AUR 包源，并补 `.SRCINFO`。
- 只安装后端；游戏插件和浏览器扩展是独立项目。
