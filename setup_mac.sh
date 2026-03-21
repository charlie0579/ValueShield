#!/usr/bin/env bash
# ValueShield — Mac/Linux 一键环境初始化脚本
# 用法：bash setup_mac.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== ValueShield 环境初始化 ==="
echo "项目目录：$SCRIPT_DIR"
echo "虚拟环境：$VENV_DIR"
echo ""

# 检查 Python 3.10+
if ! command -v python3 &>/dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.10 或以上版本。"
    echo "   Mac: brew install python3"
    echo "   Ubuntu: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅ 检测到 Python $PY_VER"

# 创建/更新虚拟环境
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 创建虚拟环境 .venv ..."
    python3 -m venv "$VENV_DIR"
else
    echo "♻️  虚拟环境已存在，跳过创建。"
fi

# 激活并升级 pip
source "$VENV_DIR/bin/activate"
echo "⬆️  升级 pip ..."
python -m pip install --upgrade pip --quiet

# 安装依赖
echo "📚 安装依赖（requirements.txt）..."
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

echo ""
echo "🎉 初始化完成！"
echo ""
echo "下一步：运行  bash start_mac.sh  启动服务"
