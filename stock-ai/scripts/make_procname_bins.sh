#!/bin/bash
# 生成有辨识度的进程名可执行文件（macOS Activity Monitor / top 显示用）
# 原理：复制系统 Python 二进制 -> 重写动态库路径 -> ad-hoc 重签名，
#       使 launchd 启动的进程显示为 shenwansan-api / shenwansan-bot。
set -euo pipefail

API_DIR="$(cd "$(dirname "$0")/../api" && pwd)"
BIN_DIR="$API_DIR/bin"
SYS_PY="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
FW_PY="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Python3"

if [[ ! -x "$SYS_PY" ]]; then
  echo "未找到系统 Python: $SYS_PY" >&2
  exit 1
fi

mkdir -p "$BIN_DIR"
for name in shenwansan-api shenwansan-bot shenwansan-research; do
  cp "$SYS_PY" "$BIN_DIR/$name"
  install_name_tool -change "@executable_path/../../../../Python3" "$FW_PY" "$BIN_DIR/$name" 2>/dev/null || true
  codesign --remove-signature "$BIN_DIR/$name" 2>/dev/null || true
  codesign -f -s - "$BIN_DIR/$name"
  echo "已生成 $BIN_DIR/$name"
done
