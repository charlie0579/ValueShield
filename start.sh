#!/usr/bin/env bash
# ValueShield — 全平台一键启动（Mac / Linux 通用）
# 用法：bash start.sh [--port 8501]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PORT=8501

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "❌ 虚拟环境不存在，请先运行：bash setup.sh"
    exit 1
fi

source "$VENV_DIR/bin/activate"

echo "=== ValueShield 启动中 ==="
echo "访问地址：http://localhost:$PORT"
echo "按 Ctrl+C 停止服务"
echo ""

cd "$SCRIPT_DIR"
exec python -m streamlit run app.py \
    --server.port "$PORT" \
    --server.headless true \
    --browser.gatherUsageStats false
