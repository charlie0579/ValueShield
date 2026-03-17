"""
app.py - ValueShield Web 看板
深色金融风格 Streamlit 界面，支持移动端访问。
功能：热力网格刻度尺、持仓管理、Base/Step 重置、确认成交、影子对账。
"""

import json
import os
from datetime import datetime

import streamlit as st

# ── 必须是第一个 Streamlit 调用 ──────────────────────────────────────────────
st.set_page_config(
    page_title="ValueShield H股看板",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from engine import GridEngine, compute_total_risk_capital, check_cash_warning
from crawler import fetch_realtime_price, fetch_dividend_ttm, compute_dividend_yield
from monitor import load_config, load_state, save_state, build_engines

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")

# ─────────────────────────────────────────────────────────────────────────────
# 全局 CSS（深色金融风格 + 移动端优化）
# ─────────────────────────────────────────────────────────────────────────────
DARK_CSS = """
<style>
/* 基础深色背景 */
html, body, [data-testid="stAppViewContainer"] {
    background-color: #0d1117 !important;
    color: #e6edf3 !important;
    font-family: 'SF Pro Display', 'PingFang SC', 'Helvetica Neue', Arial, sans-serif;
}
[data-testid="stSidebar"] { background-color: #161b22 !important; }

/* 卡片 */
.vs-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 14px;
}
.vs-card-title {
    font-size: 0.75rem;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 4px;
}
.vs-card-value {
    font-size: 1.6rem;
    font-weight: 700;
    color: #f0f6fc;
}
.vs-card-value.up   { color: #3fb950; }
.vs-card-value.down { color: #f85149; }
.vs-card-value.warn { color: #d29922; }

/* 风险预警横幅 */
.risk-banner {
    background: linear-gradient(135deg, #3d1a1a, #5c1a1a);
    border: 2px solid #f85149;
    border-radius: 10px;
    padding: 14px 20px;
    margin-bottom: 16px;
    color: #ff7b72;
    font-weight: 700;
    font-size: 1rem;
    text-align: center;
}

/* 热力网格 */
.grid-container {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 10px 0;
}
.grid-cell {
    width: calc(5% - 4px);
    min-width: 40px;
    height: 52px;
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-size: 0.62rem;
    font-weight: 600;
    border: 1px solid #30363d;
    transition: transform 0.1s;
    cursor: default;
}
.grid-cell.empty   { background: #1c2128; color: #8b949e; }
.grid-cell.occupied { background: linear-gradient(135deg, #1a4a2e, #2ea043); color: #aff5b4; border-color: #3fb950; }
.grid-cell.current  { background: linear-gradient(135deg, #1a3a5c, #1f6feb); color: #cae8ff; border-color: #388bfd; box-shadow: 0 0 8px #388bfd80; }
.grid-cell.trigger  { background: linear-gradient(135deg, #3d2b00, #9e6a03); color: #ffd800; border-color: #d29922; }
.grid-pointer { font-size: 0.7rem; }
.grid-legend { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 6px; font-size: 0.72rem; color: #8b949e; }
.legend-dot { width:10px; height:10px; border-radius:3px; display:inline-block; margin-right:4px; vertical-align: middle; }

/* 持仓表格 */
.holding-row {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
}
.holding-id { color: #8b949e; font-size: 0.7rem; }
.holding-pnl.pos { color: #3fb950; }
.holding-pnl.neg { color: #f85149; }

/* 按钮覆盖 */
.stButton > button {
    background: #21262d !important;
    border: 1px solid #30363d !important;
    color: #e6edf3 !important;
    border-radius: 8px !important;
    padding: 6px 16px !important;
    font-size: 0.85rem !important;
    transition: all 0.15s !important;
}
.stButton > button:hover {
    background: #30363d !important;
    border-color: #8b949e !important;
}
.stButton > button.primary {
    background: #1f6feb !important;
    border-color: #388bfd !important;
}

/* 股票头部标签 */
.stock-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
}
.stock-badge {
    background: #21262d;
    border: 1px solid #388bfd;
    color: #79c0ff;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 0.75rem;
    font-weight: 600;
}
.stock-name {
    font-size: 1.2rem;
    font-weight: 700;
    color: #f0f6fc;
}
.last-update { color: #8b949e; font-size: 0.72rem; }

/* 分隔线 */
hr.vs-divider { border-color: #21262d; margin: 18px 0; }

/* 移动端：格子变小 */
@media (max-width: 480px) {
    .grid-cell { min-width: 30px; height: 44px; font-size: 0.54rem; }
    .vs-card-value { font-size: 1.2rem; }
}

/* 隐藏 Streamlit 默认装饰 */
#MainMenu, footer, header { visibility: hidden; }
</style>
"""

st.markdown(DARK_CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=2)


def render_grid_heatmap(engine: GridEngine, current_price: float) -> str:
    """生成热力网格 HTML。"""
    prices = engine.grid_prices()
    cells_html = ""

    for i, price in enumerate(prices):
        level_key = str(i)
        is_occupied = level_key in engine.grid_occupied
        is_current = (current_price is not None) and abs(current_price - price) <= engine.step * 0.5

        if is_occupied and is_current:
            css = "occupied current"
            icon = "📍"
        elif is_occupied:
            css = "occupied"
            icon = "●"
        elif is_current:
            css = "current"
            icon = "▼"
        else:
            css = "empty"
            icon = ""

        cells_html += f"""
        <div class="grid-cell {css}" title="第{i+1}格 触发价:{price:.3f}">
            <span>{i+1}</span>
            <span style="font-size:0.68rem">{price:.2f}</span>
            <span class="grid-pointer">{icon}</span>
        </div>
        """

    legend = """
    <div class="grid-legend">
      <span><span class="legend-dot" style="background:#2ea043"></span>已持仓</span>
      <span><span class="legend-dot" style="background:#1f6feb"></span>当前价位</span>
      <span><span class="legend-dot" style="background:#1c2128;border:1px solid #30363d"></span>空格</span>
    </div>
    """
    return f'<div class="grid-container">{cells_html}</div>{legend}'


def color_price_change(current: float, prev: float) -> str:
    if prev is None or prev == 0:
        return "neutral"
    return "up" if current >= prev else "down"


# ─────────────────────────────────────────────────────────────────────────────
# 主界面
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    config = load_config()
    state = load_state()
    settings = config["settings"]
    engines = build_engines(config, state)

    # ── URL 参数处理（Bark 回调）─────────────────────────────────────────────
    params = st.query_params
    action = params.get("action", "")
    cb_code = params.get("code", "")
    cb_level = params.get("level", "")
    cb_holding_id = params.get("holding_id", "")

    # ── 顶部标题栏 ────────────────────────────────────────────────────────────
    col_title, col_refresh = st.columns([5, 1])
    with col_title:
        st.markdown(
            '<div style="font-size:1.5rem;font-weight:800;color:#f0f6fc;">🛡️ ValueShield <span style="color:#8b949e;font-size:1rem;font-weight:400;">H股价值投资助手</span></div>',
            unsafe_allow_html=True,
        )
    with col_refresh:
        if st.button("🔄 刷新"):
            st.rerun()

    last_updated = state.get("last_updated", "—")
    st.markdown(f'<div class="last-update">最后更新：{last_updated}</div>', unsafe_allow_html=True)
    st.markdown('<hr class="vs-divider">', unsafe_allow_html=True)

    # ── 全局风险预警 ──────────────────────────────────────────────────────────
    total_risk = compute_total_risk_capital(engines)
    cash_reserve = settings["cash_reserve"]
    if check_cash_warning(total_risk, cash_reserve):
        st.markdown(
            f'<div class="risk-banner">⚠️ 风险预警：总风险资金需求 {total_risk:,.0f} HKD 已超过现金预留 {cash_reserve:,.0f} HKD，'
            f'超出 {total_risk - cash_reserve:,.0f} HKD！</div>',
            unsafe_allow_html=True,
        )

    # ── 汇总指标行 ─────────────────────────────────────────────────────────────
    summary_cols = st.columns(3)
    with summary_cols[0]:
        st.markdown(
            f'<div class="vs-card"><div class="vs-card-title">总风险资金需求</div>'
            f'<div class="vs-card-value {"warn" if check_cash_warning(total_risk, cash_reserve) else ""}">'
            f'{total_risk:,.0f} <span style="font-size:1rem;color:#8b949e">HKD</span></div></div>',
            unsafe_allow_html=True,
        )
    with summary_cols[1]:
        total_holdings = sum(len(e.active_holdings()) for e in engines.values())
        st.markdown(
            f'<div class="vs-card"><div class="vs-card-title">当前持仓笔数</div>'
            f'<div class="vs-card-value">{total_holdings} <span style="font-size:1rem;color:#8b949e">笔</span></div></div>',
            unsafe_allow_html=True,
        )
    with summary_cols[2]:
        n_stocks = len([s for s in config["stocks"] if s.get("enabled", True)])
        st.markdown(
            f'<div class="vs-card"><div class="vs-card-title">监控标的数</div>'
            f'<div class="vs-card-value">{n_stocks} <span style="font-size:1rem;color:#8b949e">只</span></div></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<hr class="vs-divider">', unsafe_allow_html=True)

    # ── Bark 回调弹窗 ─────────────────────────────────────────────────────────
    if action == "confirm_buy" and cb_code and cb_level:
        st.info(f"📱 收到买入确认回调：{cb_code} 第 {int(cb_level) + 1} 格，请在下方持仓面板点击【确认成交】")

    if action == "confirm_sell" and cb_code and cb_holding_id:
        st.info(f"📱 收到卖出确认回调：{cb_code} 持仓 {cb_holding_id}，请在下方持仓列表点击【确认卖出】")

    # ── 各股票详情面板 ─────────────────────────────────────────────────────────
    for stock_cfg in config["stocks"]:
        if not stock_cfg.get("enabled", True):
            continue

        code = stock_cfg["code"]
        name = stock_cfg["name"]
        engine = engines[code]
        current_price = state.get("latest_prices", {}).get(code)
        annual_div = state.get("latest_dividend_ttm", {}).get(code, stock_cfg.get("annual_dividend_hkd", 0.0))
        div_yield = compute_dividend_yield(annual_div, current_price) if current_price else 0.0

        with st.container():
            # 股票头部
            st.markdown(
                f'<div class="stock-header">'
                f'<span class="stock-badge">{code}.HK</span>'
                f'<span class="stock-name">{name}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # 指标行
            m1, m2, m3, m4, m5 = st.columns(5)
            price_str = f"{current_price:.3f}" if current_price else "—"
            with m1:
                st.markdown(
                    f'<div class="vs-card"><div class="vs-card-title">现价 HKD</div>'
                    f'<div class="vs-card-value">{price_str}</div></div>',
                    unsafe_allow_html=True,
                )
            with m2:
                st.markdown(
                    f'<div class="vs-card"><div class="vs-card-title">股息率(TTM)</div>'
                    f'<div class="vs-card-value up">{div_yield * 100:.2f}%</div></div>',
                    unsafe_allow_html=True,
                )
            with m3:
                st.markdown(
                    f'<div class="vs-card"><div class="vs-card-title">Step 大小</div>'
                    f'<div class="vs-card-value">{engine.step:.4f}</div></div>',
                    unsafe_allow_html=True,
                )
            with m4:
                risk = engine.compute_risk_capital()
                st.markdown(
                    f'<div class="vs-card"><div class="vs-card-title">剩余风险资金</div>'
                    f'<div class="vs-card-value warn">{risk:,.0f}</div></div>',
                    unsafe_allow_html=True,
                )
            with m5:
                occupied = len(engine.active_holdings())
                st.markdown(
                    f'<div class="vs-card"><div class="vs-card-title">占用格数</div>'
                    f'<div class="vs-card-value">{occupied}/{engine.grid_levels}</div></div>',
                    unsafe_allow_html=True,
                )

            # 热力网格
            st.markdown("**📊 网格热力图**")
            if current_price:
                heatmap_html = render_grid_heatmap(engine, current_price)
            else:
                heatmap_html = render_grid_heatmap(engine, 0)
            st.markdown(heatmap_html, unsafe_allow_html=True)

            # 操作面板
            with st.expander("⚙️ 网格参数设置", expanded=False):
                op_col1, op_col2, op_col3 = st.columns(3)
                with op_col1:
                    new_base = st.number_input(
                        f"Base_Price ({code})",
                        value=float(engine.base_price),
                        min_value=0.01,
                        step=0.01,
                        key=f"base_{code}",
                    )
                with op_col2:
                    new_step = st.number_input(
                        f"手动 Step ({code}) (0=自动)",
                        value=float(engine._step or 0),
                        min_value=0.0,
                        step=0.001,
                        format="%.4f",
                        key=f"step_{code}",
                    )
                with op_col3:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button(f"✅ 应用参数 [{code}]", key=f"apply_{code}"):
                        engine.set_base_price(new_base)
                        if new_step > 0:
                            engine.set_step(new_step)
                        # 同步回 config.json
                        for s in config["stocks"]:
                            if s["code"] == code:
                                s["base_price"] = new_base
                                s["step"] = new_step if new_step > 0 else None
                        save_config(config)
                        state["positions"][code] = engine.to_state_dict()
                        save_state(state)
                        st.success(f"[{code}] 参数已更新！Base={new_base:.4f} Step={engine.step:.4f}")
                        st.rerun()

            # 手动买入确认
            with st.expander("🟢 手动确认买入（影子网格补录）", expanded=(action == "confirm_buy" and cb_code == code)):
                grid_prices = engine.grid_prices()
                buy_options = {
                    f"第 {i+1} 格  触发价 {p:.3f} HKD": i
                    for i, p in enumerate(grid_prices)
                    if str(i) not in engine.grid_occupied
                }
                if buy_options:
                    selected_buy = st.selectbox(
                        "选择格子",
                        list(buy_options.keys()),
                        key=f"buy_sel_{code}",
                    )
                    if st.button(f"✅ 确认买入 [{code}]", key=f"confirm_buy_{code}"):
                        level_idx = buy_options[selected_buy]
                        engine.confirm_buy(level_idx)
                        state["positions"][code] = engine.to_state_dict()
                        save_state(state)
                        st.success(f"✅ 已确认买入 {name} 第 {level_idx + 1} 格，价格 {grid_prices[level_idx]:.3f} HKD")
                        st.rerun()
                else:
                    st.info("所有格子均已占用。")

            # 持仓列表 + 卖出确认
            active = engine.active_holdings()
            if active:
                st.markdown(f"**📋 当前持仓 ({len(active)} 笔)**")
                for holding in active:
                    cp = current_price or holding.buy_price
                    pnl_pct = holding.profit_pct_if_sold_at(cp) * 100
                    pnl_class = "pos" if pnl_pct >= 0 else "neg"
                    pnl_val = holding.profit_if_sold_at(cp)
                    tp_price = holding.take_profit_price

                    h_col1, h_col2, h_col3, h_col4 = st.columns([2, 2, 2, 2])
                    with h_col1:
                        st.markdown(
                            f'<div class="holding-id">ID: {holding.holding_id}</div>'
                            f'<div>第 <b>{holding.grid_level + 1}</b> 格</div>'
                            f'<div style="color:#8b949e;font-size:0.8rem">买入: {holding.buy_price:.3f}</div>',
                            unsafe_allow_html=True,
                        )
                    with h_col2:
                        st.markdown(
                            f'<div class="holding-pnl {pnl_class}">{pnl_pct:+.2f}%</div>'
                            f'<div style="color:#8b949e;font-size:0.8rem">{pnl_val:+.1f} HKD</div>',
                            unsafe_allow_html=True,
                        )
                    with h_col3:
                        new_tp = st.number_input(
                            "止盈%",
                            value=holding.effective_take_profit_pct * 100,
                            min_value=1.0,
                            max_value=100.0,
                            step=0.5,
                            key=f"tp_{holding.holding_id}",
                            label_visibility="collapsed",
                        )
                        if st.button("更新止盈", key=f"uptp_{holding.holding_id}"):
                            engine.set_custom_take_profit(holding.holding_id, new_tp / 100)
                            state["positions"][code] = engine.to_state_dict()
                            save_state(state)
                            st.rerun()
                        st.markdown(
                            f'<div style="color:#8b949e;font-size:0.72rem">止盈价: {tp_price:.3f}</div>',
                            unsafe_allow_html=True,
                        )
                    with h_col4:
                        sell_at = st.number_input(
                            "卖出价",
                            value=float(cp),
                            min_value=0.01,
                            step=0.001,
                            format="%.3f",
                            key=f"sellprice_{holding.holding_id}",
                            label_visibility="collapsed",
                        )
                        if st.button(
                            "✅ 确认卖出",
                            key=f"sell_{holding.holding_id}",
                            type="primary" if (action == "confirm_sell" and cb_holding_id == holding.holding_id) else "secondary",
                        ):
                            engine.confirm_sell(holding.holding_id, sell_at)
                            state["positions"][code] = engine.to_state_dict()
                            save_state(state)
                            st.success(
                                f"✅ 已确认卖出 {name} 持仓 {holding.holding_id}，"
                                f"卖价 {sell_at:.3f} HKD，盈利 {holding.profit_pct_if_sold_at(sell_at)*100:.2f}%"
                            )
                            st.rerun()

            st.markdown('<hr class="vs-divider">', unsafe_allow_html=True)

    # ── 底部：全局设置 ─────────────────────────────────────────────────────────
    with st.expander("🔧 全局设置", expanded=False):
        g_col1, g_col2 = st.columns(2)
        with g_col1:
            new_cash_reserve = st.number_input(
                "现金预留 (HKD)",
                value=float(settings["cash_reserve"]),
                min_value=0.0,
                step=10000.0,
                key="global_cash_reserve",
            )
            new_bark_token = st.text_input(
                "Bark Token",
                value=settings.get("bark_token", ""),
                type="password",
                key="global_bark_token",
            )
        with g_col2:
            new_poll_interval = st.number_input(
                "轮询间隔 (秒)",
                value=int(settings["poll_interval_seconds"]),
                min_value=5,
                max_value=300,
                step=5,
                key="global_poll_interval",
            )
            new_web_url = st.text_input(
                "Web 服务地址",
                value=settings.get("web_server_url", "http://localhost:8501"),
                key="global_web_url",
            )
        if st.button("💾 保存全局设置"):
            config["settings"]["cash_reserve"] = new_cash_reserve
            config["settings"]["bark_token"] = new_bark_token
            config["settings"]["poll_interval_seconds"] = new_poll_interval
            config["settings"]["web_server_url"] = new_web_url
            save_config(config)
            st.success("全局设置已保存！")
            st.rerun()

    # ── 底部版权信息 ──────────────────────────────────────────────────────────
    st.markdown(
        '<div style="text-align:center;color:#484f58;font-size:0.7rem;padding-top:20px;">'
        'ValueShield v1.0 · 算法为辅，主观为主 · 数据本地化 · 移动端优先'
        '</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
