#!/usr/bin/env bash
# ValueShield — 全平台一键环境初始化（Mac / Linux 通用）
# 用法：bash setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# ── OS 识别 ──────────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin) OS_LABEL="macOS" ;;
    Linux)  OS_LABEL="Linux" ;;
    *)      echo "❌ 不支持的操作系统：$OS"; exit 1 ;;
esac

echo "=== ValueShield 环境初始化 ($OS_LABEL) ==="
echo "项目目录：$SCRIPT_DIR"
echo "虚拟环境：$VENV_DIR"
echo ""

# ── 检查 Python 3.10+ ────────────────────────────────────────────────────────
PY_CMD=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PY_CMD="$cmd"
            echo "✅ 检测到 Python $ver ($cmd)"
            break
        fi
    fi
done

if [ -z "$PY_CMD" ]; then
    echo "❌ 未找到 Python 3.10+，请先安装："
    if [ "$OS_LABEL" = "macOS" ]; then
        echo "   brew install python3"
    else
        echo "   sudo apt install python3.12 python3.12-venv  # Ubuntu/Debian"
        echo "   sudo yum install python3.12               # CentOS/RHEL"
    fi
    exit 1
fi

# ── 创建 / 更新虚拟环境 ──────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 创建虚拟环境 .venv ..."
    "$PY_CMD" -m venv "$VENV_DIR"
else
    echo "♻️  虚拟环境已存在，跳过创建。"
fi

source "$VENV_DIR/bin/activate"

echo "⬆️  升级 pip ..."
python -m pip install --upgrade pip --quiet

echo "📚 安装依赖（requirements.txt）..."
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

echo ""
echo "🎉 初始化完成！"
echo ""
echo "下一步：bash start_mac.sh  （或直接：bash start.sh）启动服务"
