"""
app.py - ValueShield v1.2
TradingView Dark 主题 · 侧边栏多标的导航 · 原生 st.columns 热力图(发光边框) · 超大一键确认
"""

import json
import os

import streamlit as st

st.set_page_config(
    page_title="ValueShield",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from engine import GridEngine, compute_total_risk_capital, check_cash_warning
from crawler import fetch_realtime_price, fetch_dividend_ttm, compute_dividend_yield
from monitor import load_config, load_state, save_state, build_engines

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ─────────────────────────────────────────────────────────────────────────────
# TradingView 深色主题 CSS
# ─────────────────────────────────────────────────────────────────────────────
TV_CSS = """
<style>
html, body, [data-testid="stAppViewContainer"] {
    background-color: #1E222D !important;
    color: #D1D4DC !important;
    font-family: 'Trebuchet MS', 'PingFang SC', Arial, sans-serif;
}
[data-testid="stSidebar"] {
    background-color: #161A25 !important;
    border-right: 1px solid #2A2E39 !important;
}
/* 侧边栏内所有按钮：列表导航样式 */
[data-testid="stSidebar"] .stButton > button {
    text-align: left !important;
    background: transparent !important;
    border: none !important;
    border-bottom: 1px solid #2A2E39 !important;
    border-radius: 0 !important;
    color: #9598A1 !important;
    padding: 10px 14px !important;
    width: 100% !important;
    font-size: 0.82rem !important;
    white-space: pre-wrap !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #252A37 !important;
    color: #D1D4DC !important;
}
/* 数据卡片 */
.tv-card {
    background: #252A37;
    border: 1px solid #363B47;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 10px;
}
.tv-label {
    font-size: 0.68rem;
    color: #787B86;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 2px;
}
.tv-value       { font-size: 1.8rem; font-weight: 700; color: #D1D4DC; }
.tv-value.green { color: #26A69A; }
.tv-value.red   { color: #EF5350; }
.tv-value.amber { color: #FF9800; }
/* 风险横幅 */
.tv-alert {
    background: #2A1A1A;
    border: 1px solid #EF5350;
    border-radius: 8px;
    padding: 10px 16px;
    color: #EF5350;
    font-weight: 700;
    margin-bottom: 12px;
}
/* 待确认卡片 */
.tv-pending {
    background: #192030;
    border: 1px solid #2962FF;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 10px;
}
.tv-pending.sell-pend {
    background: #2A1E10;
    border-color: #FF9800;
}
/* 股票头部大字 */
.tv-stock-name { font-size: 1.5rem; font-weight: 800; color: #D1D4DC; }
.tv-price      { font-size: 2.6rem; font-weight: 800; color: #D1D4DC; letter-spacing: -0.04em; }
.tv-divyield   { font-size: 1.3rem; font-weight: 700; color: #26A69A; }
/* 代码标签 */
.tv-badge {
    background: #252A37;
    border: 1px solid #2962FF;
    color: #82AAFF;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.72rem;
    font-weight: 700;
}
/* 持仓盈亏 */
.tv-pnl-pos { color: #26A69A; font-size: 1.05rem; font-weight: 700; }
.tv-pnl-neg { color: #EF5350; font-size: 1.05rem; font-weight: 700; }
/* 超大确认按钮覆盖 */
.big-confirm-wrap .stButton > button {
    background: linear-gradient(135deg, #1565C0, #1976D2) !important;
    border: 1px solid #2962FF !important;
    color: #FFFFFF !important;
    font-size: 1rem !important;
    font-weight: 700 !important;
    padding: 14px 20px !important;
    border-radius: 8px !important;
    letter-spacing: 0.02em !important;
}
.big-confirm-wrap .stButton > button:hover {
    background: linear-gradient(135deg, #1976D2, #1E88E5) !important;
    box-shadow: 0 0 14px #2962FF66 !important;
}
.big-confirm-wrap.sell-btn .stButton > button {
    background: linear-gradient(135deg, #E65100, #EF6C00) !important;
    border-color: #FF9800 !important;
}
/* 主区域普通按钮 */
.main-area .stButton > button {
    background: #252A37 !important;
    border: 1px solid #363B47 !important;
    color: #D1D4DC !important;
    border-radius: 6px !important;
}
.main-area .stButton > button:hover { background: #363B47 !important; }
/* 通用分割线 */
.tv-hr { border: none; border-top: 1px solid #2A2E39; margin: 12px 0; }
/* 隐藏 Streamlit 默认装饰 */
#MainMenu, footer, header { visibility: hidden; }
</style>
"""
st.markdown(TV_CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────────────

def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=2)


def _hr() -> None:
    st.markdown('<hr class="tv-hr">', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# 热力图：原生 st.columns + 内联 CSS（发光边框）
# ─────────────────────────────────────────────────────────────────────────────

def render_grid_heatmap(engine: GridEngine, current_price: float) -> None:
    """
    用原生 st.columns 渲染 20 格热力图。
    每格使用完整内联 CSS，无需外部样式表，发光边框通过 box-shadow 实现。
    """
    prices = engine.grid_prices()
    n_cols = 10

    for row_start in range(0, len(prices), n_cols):
        cols = st.columns(n_cols)
        for j, col in enumerate(cols):
            i = row_start + j
            if i >= len(prices):
                break
            price = prices[i]
            is_occupied = str(i) in engine.grid_occupied
            is_current = (
                current_price > 0
                and abs(current_price - price) <= engine.step * 0.6
            )

            if is_occupied and is_current:
                bg = "#1a4a2e"; bdr = "#26A69A"
                glow = "0 0 10px #26A69A99"; text = "#80CBC4"; icon = "📍"
            elif is_occupied:
                bg = "#162A20"; bdr = "#26A69A"
                glow = "none"; text = "#80CBC4"; icon = "●"
            elif is_current:
                bg = "#0D2248"; bdr = "#2962FF"
                glow = "0 0 12px #2962FF99"; text = "#82AAFF"; icon = "▼"
            else:
                bg = "#252A37"; bdr = "#363B47"
                glow = "none"; text = "#6B737D"; icon = ""

            with col:
                st.markdown(
                    f'<div style="background:{bg};border:1px solid {bdr};'
                    f'box-shadow:{glow};color:{text};border-radius:6px;'
                    f'padding:5px 2px;text-align:center;line-height:1.55;margin:1px;">'
                    f'<div style="font-size:0.56rem;opacity:0.55">{i + 1}</div>'
                    f'<div style="font-size:0.68rem;font-weight:700">{price:.2f}</div>'
                    f'<div style="font-size:0.72rem">{icon}</div></div>',
                    unsafe_allow_html=True,
                )

    # 图例
    st.markdown(
        '<div style="display:flex;gap:14px;margin-top:5px;font-size:0.68rem;color:#787B86;">'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:#26A69A;margin-right:4px;vertical-align:middle"></span>已持仓</span>'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:#2962FF;margin-right:4px;vertical-align:middle"></span>当前价位</span>'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:#252A37;border:1px solid #363B47;margin-right:4px;vertical-align:middle">'
        '</span>空格</span></div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 侧边栏：标的导航
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar(config: dict, state: dict) -> str:
    """渲染侧边栏股票导航列表，返回当前选中股票代码。"""
    stocks = [s for s in config["stocks"] if s.get("enabled", True)]
    if not stocks:
        return ""
    pending_all = state.get("pending_confirmations", [])

    with st.sidebar:
        st.markdown(
            '<div style="padding:14px 10px 6px;">'
            '<div style="font-size:1.1rem;font-weight:800;color:#D1D4DC;">🛡️ ValueShield</div>'
            '<div style="font-size:0.65rem;color:#787B86;margin-top:2px;">H股网格价值投资</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<hr class="tv-hr">', unsafe_allow_html=True)

        search = st.text_input(
            "搜索", "", placeholder="名称 / 代码",
            label_visibility="collapsed", key="sidebar_search",
        )
        filtered = [
            s for s in stocks
            if not search
            or search.lower() in s["name"].lower()
            or search in s["code"]
        ]

        # 初始化选中状态
        all_codes = {s["code"] for s in stocks}
        if "selected_code" not in st.session_state or st.session_state["selected_code"] not in all_codes:
            st.session_state["selected_code"] = stocks[0]["code"]

        for s in filtered:
            code = s["code"]
            name = s["name"]
            n_pend = sum(1 for p in pending_all if p.get("code") == code)
            badge = f"  🔔{n_pend}" if n_pend else ""
            is_sel = st.session_state["selected_code"] == code
            arrow = "▶ " if is_sel else "   "
            btn_label = f"{arrow}{name}{badge}\n        {code}.HK"
            if st.button(btn_label, key=f"nav_{code}"):
                st.session_state["selected_code"] = code
                st.rerun()

        st.markdown('<hr class="tv-hr">', unsafe_allow_html=True)
        last_upd = state.get("last_updated", "—")[:16]
        st.markdown(
            f'<div style="padding:6px 10px;font-size:0.65rem;color:#4B5060;">更新: {last_upd}</div>',
            unsafe_allow_html=True,
        )

    return st.session_state["selected_code"]


# ─────────────────────────────────────────────────────────────────────────────
# 待确认区：超大一键确认，禁止弹价格输入框
# ─────────────────────────────────────────────────────────────────────────────

def render_pending_section(state: dict, engines: dict, filter_code: str = "") -> bool:
    """
    渲染待确认操作。filter_code 为空时显示全部，否则只显示该股票的。
    返回 True 表示有操作执行（调用方应 st.rerun()）。
    """
    all_pending = state.get("pending_confirmations", [])
    pending = [p for p in all_pending if not filter_code or p.get("code") == filter_code]
    if not pending:
        return False

    to_remove = []
    did_action = False

    for item in pending:
        code = item.get("code", "")
        name = item.get("name", code)
        item_type = item.get("type", "buy")
        grid_level = item.get("grid_level", 0)
        grid_price = item.get("grid_price", 0.0)
        profit_pct = item.get("profit_pct", 0.0)
        ts = item.get("timestamp", "")[:16].replace("T", " ")
        is_sell = item_type == "sell"

        type_text = "止盈档位到达" if is_sell else "买入档位到达"
        type_emoji = "🔵" if is_sell else "🟢"
        price_color = "#FF9800" if is_sell else "#82AAFF"
        extra = f"  预期盈利 +{profit_pct * 100:.1f}%" if is_sell else ""
        pend_cls = "tv-pending sell-pend" if is_sell else "tv-pending"

        st.markdown(
            f'<div class="{pend_cls}">'
            f'<div style="font-size:0.75rem;color:#787B86;margin-bottom:6px">'
            f'{ts} &nbsp; {type_emoji} {type_text}</div>'
            f'<div style="font-size:1.2rem;font-weight:700;color:#D1D4DC;">'
            f'<span class="tv-badge">{code}.HK</span>&nbsp; {name}'
            f'<span style="color:{price_color}"> &nbsp;第 {grid_level + 1} 格 &nbsp;'
            f'触发价 {grid_price:.3f} HKD{extra}</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        btn_col, dismiss_col = st.columns([5, 1])
        wrap_cls = "big-confirm-wrap sell-btn" if is_sell else "big-confirm-wrap"
        with btn_col:
            st.markdown(f'<div class="{wrap_cls}">', unsafe_allow_html=True)
            btn_text = f"✅ 已按计划成交  |  {code}  第{grid_level + 1}格  @{grid_price:.3f} HKD"
            if st.button(btn_text, key=f"bigpend_{code}_{grid_level}_{item_type}",
                         use_container_width=True):
                engine = engines.get(code)
                if engine:
                    if item_type == "buy":
                        if str(grid_level) not in engine.grid_occupied:
                            engine.confirm_buy(grid_level)
                        state["positions"][code] = engine.to_state_dict()
                        st.toast(f"✅ {name} 第{grid_level + 1}格 买入已记录")
                    else:
                        engine.confirm_sell(item.get("holding_id", ""), grid_price)
                        state["positions"][code] = engine.to_state_dict()
                        st.toast(f"✅ {name} 第{grid_level + 1}格 卖出已记录")
                to_remove.append((code, grid_level, item_type))
                did_action = True
            st.markdown('</div>', unsafe_allow_html=True)

        with dismiss_col:
            if st.button("✖ 忽略", key=f"dismiss_{code}_{grid_level}_{item_type}"):
                to_remove.append((code, grid_level, item_type))
                did_action = True

    if to_remove:
        state["pending_confirmations"] = [
            p for p in all_pending
            if (p.get("code"), p.get("grid_level"), p.get("type")) not in to_remove
        ]
        save_state(state)

    return did_action


# ─────────────────────────────────────────────────────────────────────────────
# 自动校准
# ─────────────────────────────────────────────────────────────────────────────

def render_auto_align(
    engine: GridEngine, code: str, current_price: float, state: dict
) -> bool:
    if not current_price or current_price <= 0:
        st.warning("当前无行情数据，请先刷新行情。")
        return False

    prices = engine.grid_prices()
    to_fill = [
        i for i, p in enumerate(prices)
        if p >= current_price and str(i) not in engine.grid_occupied
    ]

    if not to_fill:
        st.info("✅ 所有触发格子已对齐，无需校准。")
        return False

    st.warning(
        f"⚠️ 检测到 {len(to_fill)} 个格子触发价高于现价 {current_price:.3f} HKD，尚未记录持仓。"
    )
    st.caption("  ".join(f"第{i + 1}格 {prices[i]:.3f}" for i in to_fill))

    if st.button(f"🎯 一键对齐 {len(to_fill)} 格", key=f"align_{code}"):
        for i in to_fill:
            engine.confirm_buy(i)
        state["positions"][code] = engine.to_state_dict()
        save_state(state)
        st.success(f"✅ 已对齐 {len(to_fill)} 格！")
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 主界面
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:  # noqa: PLR0912, PLR0915
    config = load_config()
    state = load_state()
    settings = config["settings"]
    engines = build_engines(config, state)

    # ── 侧边栏导航 ────────────────────────────────────────────────────────────
    selected_code = render_sidebar(config, state)

    # ── 全局指标（置顶元信息行）────────────────────────────────────────────────
    total_risk = compute_total_risk_capital(engines)
    cash_reserve = settings["cash_reserve"]
    n_pending = len(state.get("pending_confirmations", []))
    total_holdings = sum(len(e.active_holdings()) for e in engines.values())

    title_col, meta_col, refresh_col = st.columns([4, 4, 1])
    with title_col:
        st.markdown(
            '<div style="font-size:1.25rem;font-weight:800;color:#D1D4DC;padding-top:6px;">'
            '🛡️ ValueShield'
            '<span style="color:#787B86;font-size:0.8rem;font-weight:400;margin-left:8px;">v1.2</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    with meta_col:
        risk_color = "#EF5350" if check_cash_warning(total_risk, cash_reserve) else "#787B86"
        pend_color = "#FF9800" if n_pending > 0 else "#787B86"
        last_upd = state.get("last_updated", "—")[:16]
        st.markdown(
            f'<div style="font-size:0.75rem;color:#787B86;padding-top:12px;">'
            f'风险资金 <span style="color:{risk_color};font-weight:700">{total_risk:,.0f}</span> HKD'
            f' &nbsp;|&nbsp; 持仓 <b>{total_holdings}</b> 笔'
            f' &nbsp;|&nbsp; 待确认'
            f' <span style="color:{pend_color};font-weight:700">{n_pending}</span> 条'
            f' &nbsp;|&nbsp; {last_upd}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with refresh_col:
        if st.button("🔄", help="刷新页面"):
            st.rerun()

    # ── 全局风险预警横幅 ──────────────────────────────────────────────────────
    if check_cash_warning(total_risk, cash_reserve):
        st.markdown(
            f'<div class="tv-alert">⚠️ 风险预警：总风险资金需求 {total_risk:,.0f} HKD '
            f'超过现金预留 {cash_reserve:,.0f} HKD，超出 {total_risk - cash_reserve:,.0f} HKD</div>',
            unsafe_allow_html=True,
        )

    _hr()

    # ── 待确认区（只显示当前选中股票，置顶显眼位置）──────────────────────────
    if n_pending > 0:
        pending_for_stock = [
            p for p in state.get("pending_confirmations", [])
            if p.get("code") == selected_code
        ]
        if pending_for_stock:
            if render_pending_section(state, engines, filter_code=selected_code):
                st.rerun()
            _hr()

    # ── 当前选中股票详情 ──────────────────────────────────────────────────────
    stock_cfg = next((s for s in config["stocks"] if s["code"] == selected_code), None)
    if not stock_cfg:
        st.warning("未找到选中标的配置。")
        return

    code = stock_cfg["code"]
    name = stock_cfg["name"]
    engine = engines[code]
    current_price = state.get("latest_prices", {}).get(code)
    annual_div = state.get("latest_dividend_ttm", {}).get(
        code, stock_cfg.get("annual_dividend_hkd", 0.0)
    )
    div_yield = compute_dividend_yield(annual_div, current_price) if current_price else 0.0

    # ── 股票头部：大字现价 + 大字股息率 ────────────────────────────────────────
    hdr1, hdr2, hdr3, hdr4, hdr5 = st.columns([3, 2, 2, 2, 2])
    with hdr1:
        occupied_cnt = len(engine.active_holdings())
        st.markdown(
            f'<div><span class="tv-badge">{code}.HK</span></div>'
            f'<div class="tv-stock-name">{name}</div>'
            f'<div style="color:#787B86;font-size:0.75rem;margin-top:4px;">'
            f'占用 {occupied_cnt}/{engine.grid_levels} 格'
            f'</div>',
            unsafe_allow_html=True,
        )
    with hdr2:
        price_str = f"{current_price:.3f}" if current_price else "—"
        st.markdown(
            f'<div class="tv-label">现价 HKD</div>'
            f'<div class="tv-price">{price_str}</div>',
            unsafe_allow_html=True,
        )
    with hdr3:
        yield_color = "#26A69A" if div_yield >= 0.05 else "#D1D4DC"
        st.markdown(
            f'<div class="tv-label">股息率 TTM</div>'
            f'<div class="tv-divyield" style="color:{yield_color}">'
            f'{div_yield * 100:.2f}%</div>',
            unsafe_allow_html=True,
        )
    with hdr4:
        risk = engine.compute_risk_capital()
        st.markdown(
            f'<div class="tv-label">剩余风险资金</div>'
            f'<div style="font-size:1.3rem;font-weight:700;color:#FF9800">'
            f'{risk:,.0f} HKD</div>',
            unsafe_allow_html=True,
        )
    with hdr5:
        st.markdown('<div style="padding-top:4px;">', unsafe_allow_html=True)
        if st.button(f"📡 刷新行情", key=f"fetch_{code}"):
            with st.spinner("获取行情中（将尝试双通道）..."):
                new_price = fetch_realtime_price(stock_cfg["akshare_code"])
            if new_price:
                state.setdefault("latest_prices", {})[code] = new_price
                save_state(state)
                st.success(f"✅ {new_price:.3f} HKD")
                st.rerun()
            else:
                st.error("❌ 双通道均失败，请查看后台日志")
        st.markdown('</div>', unsafe_allow_html=True)

    _hr()

    # ── 热力网格（原生 st.columns，发光边框）────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.78rem;font-weight:600;color:#787B86;margin-bottom:6px;">'
        '📊 网格热力图</div>',
        unsafe_allow_html=True,
    )
    render_grid_heatmap(engine, current_price or 0)

    _hr()

    # ── 持仓列表 ───────────────────────────────────────────────────────────────
    active = engine.active_holdings()
    if active:
        st.markdown(
            f'<div style="font-size:0.78rem;font-weight:600;color:#787B86;margin-bottom:8px;">'
            f'📋 当前持仓 ({len(active)} 笔)</div>',
            unsafe_allow_html=True,
        )
        for holding in active:
            cp = current_price or holding.buy_price
            pnl_pct = holding.profit_pct_if_sold_at(cp) * 100
            pnl_val = holding.profit_if_sold_at(cp)
            pnl_cls = "tv-pnl-pos" if pnl_pct >= 0 else "tv-pnl-neg"
            tp_price = holding.take_profit_price

            h1, h2, h3, h4 = st.columns([2, 2, 2, 2])
            with h1:
                st.markdown(
                    f'<div style="color:#787B86;font-size:0.66rem">ID: {holding.holding_id}</div>'
                    f'<div style="color:#D1D4DC">第 <b>{holding.grid_level + 1}</b> 格</div>'
                    f'<div style="color:#787B86;font-size:0.78rem">买入 {holding.buy_price:.3f}</div>',
                    unsafe_allow_html=True,
                )
            with h2:
                st.markdown(
                    f'<div class="{pnl_cls}">{pnl_pct:+.2f}%</div>'
                    f'<div style="color:#787B86;font-size:0.78rem">{pnl_val:+.1f} HKD</div>'
                    f'<div style="color:#787B86;font-size:0.7rem">止盈价 {tp_price:.3f}</div>',
                    unsafe_allow_html=True,
                )
            with h3:
                new_tp = st.number_input(
                    "止盈%",
                    value=holding.effective_take_profit_pct * 100,
                    min_value=1.0, max_value=100.0, step=0.5,
                    key=f"tp_{holding.holding_id}",
                    label_visibility="collapsed",
                )
                if st.button("更新止盈%", key=f"uptp_{holding.holding_id}"):
                    engine.set_custom_take_profit(holding.holding_id, new_tp / 100)
                    state["positions"][code] = engine.to_state_dict()
                    save_state(state)
                    st.rerun()
            with h4:
                if st.button(
                    f"✅ 卖出 @{tp_price:.3f}",
                    key=f"sell_{holding.holding_id}",
                ):
                    engine.confirm_sell(holding.holding_id, tp_price)
                    state["positions"][code] = engine.to_state_dict()
                    save_state(state)
                    st.toast(f"✅ 已卖出，盈利 {pnl_pct:.2f}%")
                    st.rerun()

        _hr()

    # ── 折叠面板 ───────────────────────────────────────────────────────────────
    with st.expander("⚙️ 网格参数", expanded=False):
        op1, op2, op3 = st.columns(3)
        with op1:
            new_base = st.number_input(
                "Base_Price", value=float(engine.base_price),
                min_value=0.01, step=0.01, key=f"base_{code}",
            )
        with op2:
            new_step = st.number_input(
                "手动 Step (0=自动)", value=float(engine._step or 0),
                min_value=0.0, step=0.001, format="%.4f", key=f"step_{code}",
            )
        with op3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("✅ 应用", key=f"apply_{code}"):
                engine.set_base_price(new_base)
                if new_step > 0:
                    engine.set_step(new_step)
                for s in config["stocks"]:
                    if s["code"] == code:
                        s["base_price"] = new_base
                        s["step"] = new_step if new_step > 0 else None
                save_config(config)
                state["positions"][code] = engine.to_state_dict()
                save_state(state)
                st.success(f"已更新 Base={new_base:.4f} Step={engine.step:.4f}")
                st.rerun()

    with st.expander("🎯 自动校准 — 一键对齐格子", expanded=False):
        if render_auto_align(engine, code, current_price or 0, state):
            st.rerun()

    with st.expander("🟢 手动补录买入", expanded=False):
        grid_prices_list = engine.grid_prices()
        buy_options = {
            f"第 {i + 1} 格  @{p:.3f} HKD": i
            for i, p in enumerate(grid_prices_list)
            if str(i) not in engine.grid_occupied
        }
        if buy_options:
            selected_buy = st.selectbox(
                "选择格子", list(buy_options.keys()), key=f"buy_sel_{code}",
            )
            if st.button("✅ 确认补录", key=f"confirm_buy_{code}"):
                level_idx = buy_options[selected_buy]
                engine.confirm_buy(level_idx)
                state["positions"][code] = engine.to_state_dict()
                save_state(state)
                st.success(
                    f"✅ 已补录第{level_idx + 1}格 @{grid_prices_list[level_idx]:.3f} HKD"
                )
                st.rerun()
        else:
            st.info("所有格子均已占用。")

    with st.expander("🔧 全局设置", expanded=False):
        g1, g2 = st.columns(2)
        with g1:
            new_cash = st.number_input(
                "现金预留 HKD", value=float(settings["cash_reserve"]),
                min_value=0.0, step=10000.0, key="g_cash",
            )
            new_bark = st.text_input(
                "Bark Token", value=settings.get("bark_token", ""),
                type="password", key="g_bark",
            )
        with g2:
            new_poll = st.number_input(
                "轮询间隔 秒", value=int(settings["poll_interval_seconds"]),
                min_value=5, max_value=300, step=5, key="g_poll",
            )
            new_url = st.text_input(
                "Web 地址",
                value=settings.get("web_server_url", "http://localhost:8501"),
                key="g_url",
            )
        if st.button("💾 保存全局设置"):
            config["settings"].update({
                "cash_reserve": new_cash,
                "bark_token": new_bark,
                "poll_interval_seconds": new_poll,
                "web_server_url": new_url,
            })
            save_config(config)
            st.success("已保存！")
            st.rerun()

    st.markdown(
        '<div style="text-align:center;color:#363B47;font-size:0.62rem;padding:20px 0 8px;">'
        'ValueShield v1.2 · TradingView Dark · 算法为辅，主观为主</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
