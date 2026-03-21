#!/usr/bin/env bash
# ValueShield — 本地数据自动外发脚本
# 功能：将 magic_formula_cache.json 通过 scp 传输到腾讯云服务器
# 用法：bash sync_data.sh
#
# ──────────────────────────────────────────────────────────────────────────────
# 配置区（首次使用请修改以下变量）
# ──────────────────────────────────────────────────────────────────────────────
REMOTE_USER="ubuntu"                          # 腾讯云 SSH 用户名
REMOTE_HOST="your.server.ip"                  # 腾讯云服务器公网 IP 或域名
REMOTE_PATH="/home/ubuntu/ValueShield/"       # 服务器上 ValueShield 项目路径
SSH_KEY="$HOME/.ssh/id_rsa"                   # SSH 私钥路径（留空则使用默认密钥）
REMOTE_PORT=22                                # SSH 端口（默认 22）
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_FILE="$SCRIPT_DIR/magic_formula_cache.json"

echo "=== ValueShield 数据同步（本地 → 腾讯云）==="
echo "本地文件：$CACHE_FILE"
echo "远端目标：$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH"
echo ""

# 检查缓存文件是否存在
if [ ! -f "$CACHE_FILE" ]; then
    echo "❌ 缓存文件不存在：$CACHE_FILE"
    echo "   请先在本地执行神奇公式扫描，生成缓存后再同步。"
    exit 1
fi

# 检查服务器地址是否已配置
if [ "$REMOTE_HOST" = "your.server.ip" ]; then
    echo "❌ 请先编辑 sync_data.sh，填写腾讯云服务器的 REMOTE_HOST 等配置。"
    exit 1
fi

# 构建 SSH / SCP 选项
SCP_OPTS=(-P "$REMOTE_PORT" -o StrictHostKeyChecking=accept-new)
if [ -n "$SSH_KEY" ] && [ -f "$SSH_KEY" ]; then
    SCP_OPTS+=(-i "$SSH_KEY")
fi

# 执行传输
echo "📤 正在传输..."
scp "${SCP_OPTS[@]}" "$CACHE_FILE" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH"

echo ""
echo "✅ 同步完成！服务器 Streamlit 将在下次刷新时自动加载最新扫描结果。"
echo ""
echo "提示：若服务器 Streamlit 需要手动触发刷新，可执行："
echo "  ssh $REMOTE_USER@$REMOTE_HOST 'touch $REMOTE_PATH/app.py'"
