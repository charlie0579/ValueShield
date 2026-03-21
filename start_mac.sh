#!/usr/bin/env bash
# ValueShield — Mac/Linux 一键启动脚本
# 用法：bash start_mac.sh [--port 8501]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PORT="${PORT:-8501}"

# 解析可选 --port 参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# 检查虚拟环境
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "❌ 虚拟环境不存在，请先运行：bash setup_mac.sh"
    exit 1
fi

source "$VENV_DIR/bin/activate"

echo "=== ValueShield 启动中 ==="
echo "访问地址：http://localhost:$PORT"
echo "按 Ctrl+C 停止服务"
echo ""

cd "$SCRIPT_DIR"

# 使用 python -m streamlit 规避 PATH 未配置 streamlit 命令的问题
exec python -m streamlit run app.py \
    --server.port "$PORT" \
    --server.headless true \
    --browser.gatherUsageStats false
