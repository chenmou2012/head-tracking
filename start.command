#!/bin/bash
# HeadTrack 一键启动：在 Finder 里双击，或在终端运行 ./start.command [run.py 的参数]
# 依次检查 conda 环境、模型文件、插件安装和 Game Mode，然后启动追踪程序。

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
ENV_NAME=headtrack
XPLANE_DIR="${XPLANE_DIR:-$HOME/Library/Application Support/Steam/steamapps/common/X-Plane 12}"
GAMEPOLICY=/Applications/Xcode.app/Contents/Developer/usr/bin/gamepolicyctl

say() { printf '\033[1m[HeadTrack]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[HeadTrack] %s\033[0m\n' "$*"; }

# 1. 找 conda 和 headtrack 环境
CONDA_BIN="$(command -v conda || true)"
for c in "$HOME/miniforge3/bin/conda" "$HOME/mambaforge/bin/conda" "$HOME/miniconda3/bin/conda" "/opt/homebrew/Caskroom/miniforge/base/bin/conda"; do
  [ -n "$CONDA_BIN" ] && break
  [ -x "$c" ] && CONDA_BIN="$c"
done
if [ -z "$CONDA_BIN" ]; then
  warn "找不到 conda，请先安装 Miniforge：https://github.com/conda-forge/miniforge"
  read -r -p "按回车退出" _; exit 1
fi
CONDA_BASE="$("$CONDA_BIN" info --base)"
PY="$CONDA_BASE/envs/$ENV_NAME/bin/python"
if [ ! -x "$PY" ]; then
  say "首次运行：创建 conda 环境 $ENV_NAME（需要几分钟）"
  "$CONDA_BIN" env create -f tracker/environment.yml || { read -r -p "创建失败，按回车退出" _; exit 1; }
fi

# 2. 模型文件
if [ ! -f tracker/models/face_landmarker.task ]; then
  say "下载 Face Landmarker 模型（约 3.6 MB）"
  tracker/download_model.sh || { read -r -p "下载失败，按回车退出" _; exit 1; }
fi

# 3. 插件是否已安装（只提示，不阻止启动）
XPL="$XPLANE_DIR/Resources/plugins/HeadTrack/mac_x64/HeadTrack.xpl"
if [ ! -f "$XPL" ]; then
  warn "X-Plane 插件未安装：$XPL"
  warn "请先在 plugin/ 目录运行 make install（见 README）"
elif [ "$ROOT/plugin/build/HeadTrack/mac_x64/HeadTrack.xpl" -nt "$XPL" ]; then
  warn "plugin/build 里的插件比已安装的新，记得 make install 并重启 X-Plane"
fi

# 4. Game Mode：X-Plane 全屏时会把追踪程序压到 3~4 fps
if [ -x "$GAMEPOLICY" ]; then
  # 关闭后的输出为 "...policy is currently disabled. Game mode is forced always off."（去掉颜色码再匹配）
  if ! "$GAMEPOLICY" game-mode status 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | grep -q "forced always off"; then
    warn "Game Mode 未关闭：X-Plane 全屏时追踪程序会被降速，视角卡顿。"
    warn "关闭方法：$GAMEPOLICY game-mode set off（或让 X-Plane 以窗口模式运行）"
  fi
fi

# 5. 启动
say "启动追踪程序（预览窗口按 C 回中，Q 退出）"
cd tracker && exec "$PY" run.py "$@"
