#!/usr/bin/env bash
# IndexTTS2 CLI 启动器（Linux / macOS / Git Bash）。
#
# 职责只有一个：找到一个**当前系统真能执行**的 Python 来跑 CLI 外壳。
# 真正的环境选择由 Python 侧（conf.resolve_python_exe）决定，两边顺序保持一致。
#
# 候选顺序刻意让 POSIX 路径排在 .exe 前面：把 Windows 的 venv 整体拷到 Linux 时，
# venv/python.exe 确实存在，但先命中它只会得到 "Exec format error"。

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# ---- 项目目录：环境变量 > config.json > skill 自身 ----
PROJECT_DIR="${INDEXTTS2_HOME:-}"
if [ -z "$PROJECT_DIR" ] && [ -f "$SKILL_DIR/config.json" ]; then
  PROJECT_DIR="$(sed -n 's/.*"project_dir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SKILL_DIR/config.json" | head -1)"
  PROJECT_DIR="${PROJECT_DIR//\\\\/\\}"   # JSON 的 \\ -> \
fi
PROJECT_DIR="${PROJECT_DIR:-$SKILL_DIR}"

# ---- 解释器：INDEXTTS2_PYTHON > config 的 python > venv 各形态 > 系统 python ----
PY="${INDEXTTS2_PYTHON:-}"
if [ -z "$PY" ] && [ -f "$SKILL_DIR/config.json" ]; then
  PY="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SKILL_DIR/config.json" | head -1)"
  PY="${PY//\\\\/\\}"
fi

if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  PY=""
  UNAME_S="$(uname -s 2>/dev/null || echo unknown)"
  if [ "$UNAME_S" = "Linux" ] || [ "$UNAME_S" = "Darwin" ]; then
    # 绝不能选 .exe：WSL 的 /mnt/* 会把所有文件显示成可执行，而且 interop
    # 能直接运行 Windows 的 python.exe —— 选错之后它会把 POSIX 路径交给
    # Windows 解释器，整条链就废了。
    CANDS="$PROJECT_DIR/venv/bin/python3
$PROJECT_DIR/venv/bin/python
$PROJECT_DIR/venv/python3
$PROJECT_DIR/venv/python"
  else
    CANDS="$PROJECT_DIR/venv/python.exe
$PROJECT_DIR/venv/Scripts/python.exe
$PROJECT_DIR/venv/python"
  fi
  OLD_IFS=$IFS
  IFS='
'
  for candidate in $CANDS; do
    if [ -x "$candidate" ]; then
      PY="$candidate"
      break
    fi
  done
  IFS=$OLD_IFS
fi

if [ -z "$PY" ]; then
  # 还没有环境：用系统 Python 跑起 CLI，`setup` 会据此自举
  PY="$(command -v python3 || command -v python || true)"
fi

if [ -z "$PY" ]; then
  echo "indextts2: 找不到可用的 Python 解释器（$PROJECT_DIR/venv 与 PATH 上都没有）" >&2
  echo "  先安装 Python 3.10/3.11，或运行 scripts/indextts2.sh setup 前先装好 python3。" >&2
  exit 3
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PATH="$PROJECT_DIR/venv/bin:$PROJECT_DIR/venv:$PATH"

exec "$PY" -u "$SKILL_DIR/scripts/indextts2.py" "$@"
