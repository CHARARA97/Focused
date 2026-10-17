# Arch 本地包

- `PKGBUILD` — `focused` 包；从 GitHub Release wheel 安装。
- `focused.install` — 安装后提示启用 user service。
- `README.md` — 面向用户的构建/卸载说明。
- `.SRCINFO` — `makepkg --printsrcinfo` 生成；AUR 暂不提交。

构建：`makepkg -si`；版本/wheel 变化后先 `updpkgsums`。
