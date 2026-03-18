"""
app.py - ValueShield v1.6
底仓逻辑 · 资产总账 · 成交价覆盖 · 资金进度条 · 系统参数追加
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
# Apple/SaaS 亮色主题 CSS
# ─────────────────────────────────────────────────────────────────────────────
LIGHT_CSS = """
<style>
html, body, [data-testid="stAppViewContainer"] {
    background-color: #F9FAFB !important;
    color: #111827 !important;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", Arial, sans-serif;
}
[data-testid="stSidebar"] {
    background-color: #FFFFFF !important;
    border-right: 1px solid #E5E7EB !important;
}
.lv-card {
    background: #FFFFFF;
    border-radius: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    padding: 16px 20px;
    margin-bottom: 10px;
    border: 1px solid #F3F4F6;
}
.lv-label {
    font-size: 0.67rem; color: #9CA3AF; text-transform: uppercase;
    letter-spacing: 0.09em; margin-bottom: 3px; font-weight: 500;
}
.lv-value       { font-size: 1.55rem; font-weight: 700; color: #111827; }
.lv-value.green { color: #16A34A; }
.lv-value.red   { color: #DC2626; }
.lv-value.amber { color: #D97706; }
.lv-notify {
    background: #EFF6FF; border: 1px solid #BFDBFE;
    border-left: 4px solid #2563EB; border-radius: 10px;
    padding: 12px 18px; margin-bottom: 12px;
}
.lv-notify.sell-notify {
    background: #FFF7ED; border-color: #FED7AA; border-left-color: #F59E0B;
}
.lv-alert {
    background: #FEF2F2; border: 1px solid #FECACA;
    border-left: 4px solid #DC2626; border-radius: 10px;
    padding: 10px 18px; color: #B91C1C; font-weight: 600;
    margin-bottom: 12px; font-size: 0.88rem;
}
.lv-badge {
    background: #EFF6FF; border: 1px solid #BFDBFE; color: #1D4ED8;
    border-radius: 6px; padding: 2px 8px; font-size: 0.72rem;
    font-weight: 700; display: inline-block; margin-bottom: 4px;
}
.lv-price    { font-size: 2.8rem; font-weight: 800; color: #111827; letter-spacing: -0.04em; line-height: 1.1; }
.lv-divyield { font-size: 1.4rem; font-weight: 700; }
.lv-pnl-pos  { color: #16A34A; font-weight: 700; font-size: 1rem; }
.lv-pnl-neg  { color: #DC2626; font-weight: 700; font-size: 1rem; }
.big-confirm-wrap .stButton > button {
    background: #2563EB !important; border: none !important;
    color: #FFFFFF !important; font-size: 0.93rem !important;
    font-weight: 700 !important; padding: 14px 20px !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 8px rgba(37,99,235,0.22) !important;
}
.big-confirm-wrap .stButton > button:hover { background: #1D4ED8 !important; }
.big-confirm-wrap.sell-btn .stButton > button {
    background: #F59E0B !important;
    box-shadow: 0 2px 8px rgba(245,158,11,0.22) !important;
}
[data-testid="stSidebar"] .stButton > button {
    text-align: left !important; background: transparent !important;
    border: none !important; border-bottom: 1px solid #F3F4F6 !important;
    border-radius: 0 !important; color: #6B7280 !important;
    padding: 10px 14px !important; width: 100% !important;
    font-size: 0.82rem !important; white-space: pre-wrap !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #F9FAFB !important; color: #111827 !important;
}
.stButton > button {
    background: #FFFFFF !important; border: 1px solid #E5E7EB !important;
    color: #374151 !important; border-radius: 8px !important; font-weight: 500 !important;
}
.stButton > button:hover { background: #F9FAFB !important; }
.lv-hr { border: none; border-top: 1px solid #F3F4F6; margin: 14px 0; }
#MainMenu, footer, header { visibility: hidden; }
</style>
"""
st.markdown(LIGHT_CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────────────

def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=2)


@st.cache_data(ttl=5)
def _fetch_price_cached(akshare_code: str) -> float | None:
    """带 5 s 缓存的行情拉取，防止高频点击阻塞 UI 渲染。"""
    return fetch_realtime_price(akshare_code)


def _hr() -> None:
    st.markdown('<hr class="lv-hr">', unsafe_allow_html=True)


def compute_portfolio_stats(engines: dict, state: dict) -> dict:
    """计算全局资产总账：总市値 / 底仓规模 / 累计收割。"""
    total_mv = 0.0
    core_mv = 0.0
    realized = 0.0
    for code, engine in engines.items():
        price = state.get("latest_prices", {}).get(code, 0.0) or 0.0
        total_mv += engine.total_market_value(price)
        core_mv += engine.core_position_value(price)
        realized += engine.realized_profit()
    return {
        "total_market_value": round(total_mv, 2),
        "core_value": round(core_mv, 2),
        "realized_profit": round(realized, 2),
    }
# ─────────────────────────────────────────────────────────────────────────────

def render_sidebar(config: dict, state: dict) -> str:
    stocks = [s for s in config["stocks"] if s.get("enabled", True)]
    if not stocks:
        return ""
    pending_all = state.get("pending_confirmations", [])

    with st.sidebar:
        st.markdown(
            '<div style="padding:16px 12px 8px;">'
            '<div style="font-size:1.1rem;font-weight:800;color:#111827;">🛡️ ValueShield</div>'
            '<div style="font-size:0.65rem;color:#9CA3AF;margin-top:2px;">H股网格价值投资</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<hr class="lv-hr">', unsafe_allow_html=True)

        search = st.text_input(
            "", placeholder="🔍 搜索名称/代码",
            label_visibility="collapsed", key="sidebar_search",
        )
        filtered = [
            s for s in stocks
            if not search or search.lower() in s["name"].lower() or search in s["code"]
        ]

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
            if st.button(f"{arrow}{name}{badge}\n        {code}.HK", key=f"nav_{code}"):
                st.session_state["selected_code"] = code
                st.rerun()

        st.markdown('<hr class="lv-hr">', unsafe_allow_html=True)
        last_upd = state.get("last_updated", "—")[:16]
        st.markdown(
            f'<div style="padding:6px 12px;font-size:0.65rem;color:#9CA3AF;">更新: {last_upd}</div>',
            unsafe_allow_html=True,
        )

    return st.session_state["selected_code"]


# ─────────────────────────────────────────────────────────────────────────────
# 亮色热力图：空格浅灰虚线框，持仓薄荷绿，当前价蓝色
# ─────────────────────────────────────────────────────────────────────────────

def render_grid_heatmap(engine: GridEngine, current_price: float) -> None:
    prices = engine.grid_prices()
    core_levels = {h.grid_level for h in engine.active_holdings() if h.is_core}

    for row_start in range(0, len(prices), 10):
        cols = st.columns(10)
        for j, col in enumerate(cols):
            i = row_start + j
            if i >= len(prices):
                break
            price = prices[i]
            is_occupied = str(i) in engine.grid_occupied
            is_core_cell = i in core_levels
            is_current = current_price > 0 and abs(current_price - price) <= engine.step * 0.6

            if is_core_cell and is_current:
                bg = "#00897B"; bdr = "2px solid #004D40"; text = "#FFFFFF"; icon = "🏠"
            elif is_core_cell:
                bg = "#26A69A"; bdr = "1px solid #00897B"; text = "#FFFFFF"; icon = "🏠"
            elif is_occupied and is_current:
                bg = "#D1FAE5"; bdr = "2px solid #059669"; text = "#065F46"; icon = "📍"
            elif is_occupied:
                bg = "#DCFCE7"; bdr = "1px solid #86EFAC"; text = "#15803D"; icon = "●"
            elif is_current:
                bg = "#EFF6FF"; bdr = "2px solid #3B82F6"; text = "#1D4ED8"; icon = "▼"
            else:
                bg = "#FAFAFA"; bdr = "1px dashed #D1D5DB"; text = "#9CA3AF"; icon = ""

            with col:
                st.markdown(
                    f'<div style="background:{bg};border:{bdr};color:{text};'
                    f'border-radius:8px;padding:5px 2px;text-align:center;line-height:1.5;margin:2px;">'
                    f'<div style="font-size:0.52rem;opacity:0.55">{i + 1}</div>'
                    f'<div style="font-size:0.65rem;font-weight:700">{price:.2f}</div>'
                    f'<div style="font-size:0.68rem">{icon}</div></div>',
                    unsafe_allow_html=True,
                )

    st.markdown(
        '<div style="display:flex;gap:14px;margin-top:6px;font-size:0.67rem;color:#9CA3AF;">'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:#26A69A;border:1px solid #00897B;margin-right:3px;vertical-align:middle"></span>🏠底仓</span>'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:#DCFCE7;border:1px solid #86EFAC;margin-right:3px;vertical-align:middle"></span>已持仓</span>'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:#EFF6FF;border:1px solid #3B82F6;margin-right:3px;vertical-align:middle"></span>当前价</span>'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:#FAFAFA;border:1px dashed #D1D5DB;margin-right:3px;vertical-align:middle"></span>空格</span></div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 待确认通知条（仅有待操作时显示，平时界面保持干净）
# ─────────────────────────────────────────────────────────────────────────────

def render_pending_section(state: dict, engines: dict, filter_code: str = "") -> bool:
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

        type_emoji = "🔔" if is_sell else "📥"
        type_text = "止盈档位到达" if is_sell else "买入档位已触发"
        price_color = "#F59E0B" if is_sell else "#2563EB"
        extra = f" · 预期盈利 +{profit_pct * 100:.1f}%" if is_sell else ""
        notify_cls = "lv-notify sell-notify" if is_sell else "lv-notify"

        st.markdown(
            f'<div class="{notify_cls}">'
            f'<div style="font-size:0.7rem;color:#9CA3AF;margin-bottom:4px">{ts} · {type_emoji} {type_text}</div>'
            f'<div style="font-size:1.05rem;font-weight:700;color:#111827;">'
            f'<span class="lv-badge">{code}.HK</span> {name} '
            f'<span style="color:{price_color}">第 {grid_level + 1} 格 · @{grid_price:.3f} HKD{extra}</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        confirm_key = f"confirm_open_{code}_{grid_level}_{item_type}"
        wrap_cls = "big-confirm-wrap sell-btn" if is_sell else "big-confirm-wrap"

        if st.session_state.get(confirm_key):
            # Stage 2: 实际成交价输入层
            st.markdown(
                '<div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:10px;'
                'padding:12px 16px;margin-bottom:10px;">'
                '<div style="font-size:0.75rem;color:#15803D;font-weight:600;margin-bottom:8px;">'
                '📝 请确认实际成交价（默认为网格触发价，可修改为实际价格）</div>',
                unsafe_allow_html=True,
            )
            price_c, confirm_c, cancel_c = st.columns([4, 1, 1])
            with price_c:
                actual_price = st.number_input(
                    "实际成交价 (HKD)",
                    value=float(grid_price),
                    min_value=0.001,
                    step=0.001,
                    format="%.3f",
                    key=f"actual_price_{code}_{grid_level}_{item_type}",
                    label_visibility="collapsed",
                )
            with confirm_c:
                if st.button("✅ 写入", key=f"final_{code}_{grid_level}_{item_type}",
                             use_container_width=True):
                    engine = engines.get(code)
                    if engine:
                        if item_type == "buy":
                            if str(grid_level) not in engine.grid_occupied:
                                engine.confirm_buy(grid_level, actual_price)
                            state["positions"][code] = engine.to_state_dict()
                            st.toast(f"✅ {name} 第{grid_level + 1}格 买入 @{actual_price:.3f} 已记录")
                        else:
                            engine.confirm_sell(item.get("holding_id", ""), actual_price)
                            state["positions"][code] = engine.to_state_dict()
                            st.toast(f"✅ {name} 第{grid_level + 1}格 卖出 @{actual_price:.3f} 已记录")
                    st.session_state[confirm_key] = False
                    to_remove.append((code, grid_level, item_type))
                    did_action = True
            with cancel_c:
                if st.button("✖ 取消", key=f"cancel_{code}_{grid_level}_{item_type}",
                             use_container_width=True):
                    st.session_state[confirm_key] = False
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            btn_col, dismiss_col = st.columns([5, 1])
            with btn_col:
                st.markdown(f'<div class="{wrap_cls}">', unsafe_allow_html=True)
                btn_text = (
                    f"✅ 确认成交 · {name} · 第{grid_level + 1}格"
                    f" · @{grid_price:.3f} HKD（可改价）"
                )
                if st.button(btn_text, key=f"bigpend_{code}_{grid_level}_{item_type}",
                             use_container_width=True):
                    st.session_state[confirm_key] = True
                    st.rerun()
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

def render_auto_align(engine: GridEngine, code: str, current_price: float, state: dict) -> bool:
    if not current_price or current_price <= 0:
        st.warning("当前无行情数据，请先在监控页刷新行情。")
        return False

    prices = engine.grid_prices()
    to_fill = [i for i, p in enumerate(prices) if p >= current_price and str(i) not in engine.grid_occupied]

    if not to_fill:
        st.success("✅ 所有触发格子已对齐，无需校准。")
        return False

    st.warning(f"检测到 {len(to_fill)} 个格子触发价高于现价 {current_price:.3f} HKD，尚未记录持仓。")
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

    selected_code = render_sidebar(config, state)

    total_risk = compute_total_risk_capital(engines)
    cash_reserve = settings["cash_reserve"]
    n_pending_all = len(state.get("pending_confirmations", []))
    total_holdings = sum(len(e.active_holdings()) for e in engines.values())

    # ── 顶部标题行（单行，极简）
    title_col, refresh_col = st.columns([9, 1])
    with title_col:
        last_upd = state.get("last_updated", "—")[:16]
        pend_color = "#D97706" if n_pending_all > 0 else "#9CA3AF"
        risk_color = "#DC2626" if check_cash_warning(total_risk, cash_reserve) else "#9CA3AF"
        st.markdown(
            '<span style="font-size:1.2rem;font-weight:800;color:#111827;">🛡️ ValueShield</span>'
            '<span style="font-size:0.72rem;color:#9CA3AF;margin-left:12px;">'
            f'风险资金 <span style="color:{risk_color};font-weight:600">{total_risk:,.0f}</span> HKD'
            f' · 持仓 <b style="color:#111827">{total_holdings}</b> 笔'
            f' · 待确认 <span style="color:{pend_color};font-weight:600">{n_pending_all}</span> 条'
            f' · {last_upd}</span>',
            unsafe_allow_html=True,
        )
    with refresh_col:
        if st.button("🔄", help="刷新页面", key="global_refresh"):
            st.rerun()

    # ── 全局风险预警横幅
    if check_cash_warning(total_risk, cash_reserve):
        st.markdown(
            f'<div class="lv-alert">⚠️ 风险资金需求 {total_risk:,.0f} HKD 超过现金预留 '
            f'{cash_reserve:,.0f} HKD，超出 {total_risk - cash_reserve:,.0f} HKD</div>',
            unsafe_allow_html=True,
        )

    # ── 待确认通知条：仅有待确认时显示，平时保持干净
    pending_for_stock = [p for p in state.get("pending_confirmations", []) if p.get("code") == selected_code]
    if pending_for_stock:
        if render_pending_section(state, engines, filter_code=selected_code):
            st.rerun()

    _hr()

    stock_cfg = next((s for s in config["stocks"] if s["code"] == selected_code), None)
    if not stock_cfg:
        st.warning("未找到选中标的配置。")
        return

    code = stock_cfg["code"]
    name = stock_cfg["name"]
    engine = engines[code]
    current_price = state.get("latest_prices", {}).get(code)
    annual_div = state.get("latest_dividend_ttm", {}).get(code, stock_cfg.get("annual_dividend_hkd", 0.0))
    div_yield = compute_dividend_yield(annual_div, current_price) if current_price else 0.0

    # ── 标签页
    tab_monitor, tab_config, tab_settings = st.tabs(["📊 实时监控", "⚙️ 网格配置", "🛠️ 系统设置"])

    # ════════════════════════════════════════════════════════════════
    # 📊 监控页
    # ════════════════════════════════════════════════════════════════
    with tab_monitor:
        # ── 资产总账看板
        stats = compute_portfolio_stats(engines, state)
        p1, p2, p3 = st.columns(3)
        real_cls = "green" if stats["realized_profit"] >= 0 else "red"
        with p1:
            st.markdown(
                f'<div class="lv-card"><div class="lv-label">资产占用（持仓总市値）</div>'
                f'<div class="lv-value">{stats["total_market_value"]:,.0f}'
                f'<span style="font-size:0.85rem;color:#9CA3AF"> HKD</span></div></div>',
                unsafe_allow_html=True,
            )
        with p2:
            st.markdown(
                f'<div class="lv-card"><div class="lv-label">🏠 底仓规模</div>'
                f'<div class="lv-value" style="color:#26A69A">{stats["core_value"]:,.0f}'
                f'<span style="font-size:0.85rem;color:#9CA3AF"> HKD</span></div></div>',
                unsafe_allow_html=True,
            )
        with p3:
            st.markdown(
                f'<div class="lv-card"><div class="lv-label">收割成果（累计已实现）</div>'
                f'<div class="lv-value {real_cls}">{stats["realized_profit"]:+,.0f}'
                f'<span style="font-size:0.85rem;color:#9CA3AF"> HKD</span></div></div>',
                unsafe_allow_html=True,
            )

        # ── 资金占用进度条
        max_cap = settings.get("max_capital_usage", 0)
        min_hold = settings.get("min_holding_limit", 0)
        if max_cap > 0:
            usage_pct = min(stats["total_market_value"] / max_cap, 1.0)
            st.progress(
                usage_pct,
                text=(
                    f"资金占用 {stats['total_market_value']:,.0f} / {max_cap:,.0f} HKD"
                    f" ({usage_pct * 100:.1f}%)"
                ),
            )
        if min_hold > 0 and total_holdings < min_hold:
            st.markdown(
                f'<div class="lv-alert" style="border-left-color:#D97706;background:#FFFBEB;'  # amber
                f'border-color:#FDE68A;color:#92400E;">'  
                f'⚠️ 当前持仓 {total_holdings} 笔，低于底仓保护阈値 {min_hold} 笔</div>',
                unsafe_allow_html=True,
            )

        _hr()

        g1, g2, g3, g4 = st.columns(4)
        occupied_cnt = len(engine.active_holdings())
        risk_cls = "red" if check_cash_warning(total_risk, cash_reserve) else ""
        pend_cls = "amber" if n_pending_all > 0 else ""

        with g1:
            st.markdown(
                f'<div class="lv-card"><div class="lv-label">总风险资金需求</div>'
                f'<div class="lv-value {risk_cls}">{total_risk:,.0f}'
                f'<span style="font-size:0.85rem;color:#9CA3AF"> HKD</span></div></div>',
                unsafe_allow_html=True,
            )
        with g2:
            st.markdown(
                f'<div class="lv-card"><div class="lv-label">当前持仓</div>'
                f'<div class="lv-value">{total_holdings}'
                f'<span style="font-size:0.85rem;color:#9CA3AF"> 笔</span></div></div>',
                unsafe_allow_html=True,
            )
        with g3:
            st.markdown(
                f'<div class="lv-card"><div class="lv-label">待确认操作</div>'
                f'<div class="lv-value {pend_cls}">{n_pending_all}'
                f'<span style="font-size:0.85rem;color:#9CA3AF"> 条</span></div></div>',
                unsafe_allow_html=True,
            )
        with g4:
            st.markdown(
                f'<div class="lv-card"><div class="lv-label">本标的占用格数</div>'
                f'<div class="lv-value">{occupied_cnt}'
                f'<span style="font-size:0.85rem;color:#9CA3AF">/{engine.grid_levels}</span></div></div>',
                unsafe_allow_html=True,
            )

        _hr()

        # 大字行情头部
        hdr1, hdr2, hdr3, hdr4, hdr5 = st.columns([3, 2, 2, 2, 1])
        with hdr1:
            st.markdown(
                f'<span class="lv-badge">{code}.HK</span>'
                f'<div style="font-size:1.55rem;font-weight:800;color:#111827;margin-top:2px;">{name}</div>',
                unsafe_allow_html=True,
            )
        with hdr2:
            price_str = f"{current_price:.3f}" if current_price else "—"
            st.markdown(
                f'<div class="lv-label">现价 HKD</div><div class="lv-price">{price_str}</div>',
                unsafe_allow_html=True,
            )
        with hdr3:
            yield_color = "#16A34A" if div_yield >= 0.05 else ("#D97706" if div_yield > 0 else "#9CA3AF")
            st.markdown(
                f'<div class="lv-label">股息率 TTM</div>'
                f'<div class="lv-divyield" style="color:{yield_color}">{div_yield * 100:.2f}%</div>',
                unsafe_allow_html=True,
            )
        with hdr4:
            risk = engine.compute_risk_capital()
            st.markdown(
                f'<div class="lv-label">剩余风险资金</div>'
                f'<div style="font-size:1.3rem;font-weight:700;color:#D97706">{risk:,.0f} HKD</div>',
                unsafe_allow_html=True,
            )
        with hdr5:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("📡 刷新", key=f"fetch_{code}"):
                _fetch_price_cached.clear()
                with st.spinner("数据同步中..."):
                    new_price = _fetch_price_cached(stock_cfg["akshare_code"])
                if new_price:
                    state.setdefault("latest_prices", {})[code] = new_price
                    save_state(state)
                    st.success(f"{new_price:.3f}")
                    st.rerun()
                else:
                    st.error("获取失败")

        _hr()

        st.markdown('<div style="font-size:0.74rem;font-weight:600;color:#374151;margin-bottom:8px;">📊 网格热力图</div>', unsafe_allow_html=True)
        render_grid_heatmap(engine, current_price or 0)

        _hr()

        active = engine.active_holdings()
        if active:
            st.markdown(
                f'<div style="font-size:0.74rem;font-weight:600;color:#374151;margin-bottom:10px;">📋 当前持仓 ({len(active)} 笔)</div>',
                unsafe_allow_html=True,
            )
            for holding in active:
                cp = current_price or holding.buy_price
                pnl_pct = holding.profit_pct_if_sold_at(cp) * 100
                pnl_val = holding.profit_if_sold_at(cp)
                pnl_cls = "lv-pnl-pos" if pnl_pct >= 0 else "lv-pnl-neg"
                tp_price = holding.take_profit_price

                h1, h2, h3, h4, h5 = st.columns([2, 2, 2, 1, 1])
                with h1:
                    st.markdown(
                        f'<div style="color:#9CA3AF;font-size:0.65rem">ID: {holding.holding_id}</div>'
                        f'<div style="color:#111827;font-weight:600">第 {holding.grid_level + 1} 格</div>'
                        f'<div style="color:#9CA3AF;font-size:0.78rem">买入 {holding.buy_price:.3f}</div>',
                        unsafe_allow_html=True,
                    )
                with h2:
                    st.markdown(
                        f'<div class="{pnl_cls}">{pnl_pct:+.2f}%</div>'
                        f'<div style="color:#9CA3AF;font-size:0.78rem">{pnl_val:+.1f} HKD</div>'
                        f'<div style="color:#9CA3AF;font-size:0.7rem">止盈价 {tp_price:.3f}</div>',
                        unsafe_allow_html=True,
                    )
                with h3:
                    new_tp = st.number_input(
                        "止盈%", value=holding.effective_take_profit_pct * 100,
                        min_value=1.0, max_value=100.0, step=0.5,
                        key=f"tp_{holding.holding_id}", label_visibility="collapsed",
                    )
                    if st.button("更新止盈%", key=f"uptp_{holding.holding_id}"):
                        engine.set_custom_take_profit(holding.holding_id, new_tp / 100)
                        state["positions"][code] = engine.to_state_dict()
                        save_state(state)
                        st.rerun()
                with h4:
                    core_label = "🏠 底仓" if holding.is_core else "⬜ 底仓"
                    core_color = "color:#26A69A;font-weight:700" if holding.is_core else "color:#9CA3AF"
                    st.markdown(f'<div style="font-size:0.7rem;margin-bottom:4px;{core_color}">{core_label}</div>',
                                unsafe_allow_html=True)
                    if st.button("切换", key=f"core_{holding.holding_id}",
                                 help="标记/取消底仓：底仓不发止盈提醒"):
                        engine.toggle_core(holding.holding_id)
                        state["positions"][code] = engine.to_state_dict()
                        save_state(state)
                        st.rerun()
                with h5:
                    if not holding.is_core:
                        if st.button(f"✅ 卖出 @{tp_price:.3f}", key=f"sell_{holding.holding_id}"):
                            engine.confirm_sell(holding.holding_id, tp_price)
                            state["positions"][code] = engine.to_state_dict()
                            save_state(state)
                            st.toast(f"✅ 已卖出，盈利 {pnl_pct:.2f}%")
                            st.rerun()
                    else:
                        st.markdown(
                            '<div style="font-size:0.65rem;color:#9CA3AF;padding-top:8px;">🏠底仓<br>禁止止盈</div>',
                            unsafe_allow_html=True,
                        )
                _hr()
        else:
            st.markdown(
                '<div style="color:#9CA3AF;font-size:0.85rem;text-align:center;padding:20px 0;">暂无持仓，等待触发信号...</div>',
                unsafe_allow_html=True,
            )

    # ════════════════════════════════════════════════════════════════
    # ⚙️ 配置页
    # ════════════════════════════════════════════════════════════════
    with tab_config:
        # ── 标题行：显示 Base/Step + 实时市价参考
        mkt_ref = (
            f"市场现价 <b style='color:#2563EB'>{current_price:.3f}</b> HKD · "
            if current_price else ""
        )
        st.markdown(
            f'<div style="font-size:0.85rem;color:#374151;margin-bottom:16px;">'
            f'<b style="color:#111827">{name}</b> ({code}.HK) · '
            f'{mkt_ref}Base: <b>{engine.base_price:.4f}</b> · Step: <b>{engine.step:.4f}</b></div>',
            unsafe_allow_html=True,
        )

        st.markdown("**📐 网格参数**")

        # ── 同步现价为 Base 快捷按钮
        if current_price:
            if st.button(
                f"📌 同步现价 {current_price:.3f} → Base",
                key=f"sync_base_{code}",
                help="将当前市场价填入 Base_Price 输入框，方便快速重置 20 个格子",
            ):
                st.session_state[f"base_{code}"] = float(current_price)
                st.rerun()

        op1, op2, op3 = st.columns([2, 2, 1])
        with op1:
            new_base = st.number_input(
                "Base_Price（锚定价格）", value=float(engine.base_price),
                min_value=0.01, step=0.01, key=f"base_{code}",
            )
        with op2:
            new_step = st.number_input(
                "手动 Step（0 = 自动计算）", value=float(engine._step or 0),
                min_value=0.0, step=0.001, format="%.4f", key=f"step_{code}",
            )
        with op3:
            st.markdown("<br><br>", unsafe_allow_html=True)
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
                st.toast(f"✅ Base={new_base:.4f}  Step={engine.step:.4f} 已应用")
                st.rerun()

        _hr()
        st.markdown("**🎯 自动校准**")
        if render_auto_align(engine, code, current_price or 0, state):
            st.rerun()

        _hr()
        st.markdown("**🟢 手动补录买入**")
        grid_prices_list = engine.grid_prices()
        buy_options = {
            f"第 {i + 1} 格  @{p:.3f} HKD": i
            for i, p in enumerate(grid_prices_list)
            if str(i) not in engine.grid_occupied
        }
        if buy_options:
            selected_buy = st.selectbox("选择格子", list(buy_options.keys()), key=f"buy_sel_{code}")
            if st.button("✅ 确认补录", key=f"confirm_buy_{code}"):
                level_idx = buy_options[selected_buy]
                engine.confirm_buy(level_idx)
                state["positions"][code] = engine.to_state_dict()
                save_state(state)
                st.success(f"✅ 已补录第{level_idx + 1}格 @{grid_prices_list[level_idx]:.3f} HKD")
                st.rerun()
        else:
            st.info("所有格子均已占用。")

    # ════════════════════════════════════════════════════════════════
    # 🛠️ 设置页
    # ════════════════════════════════════════════════════════════════
    with tab_settings:
        st.markdown("**💰 资金预警**")
        s1, s2 = st.columns(2)
        with s1:
            new_cash = st.number_input(
                "现金预留警戒线 (HKD)", value=float(settings["cash_reserve"]),
                min_value=0.0, step=10000.0, key="g_cash",
                help="总风险资金需求超过此值时触发红色预警",
            )
        with s2:
            new_poll = st.number_input(
                "监控轮询间隔 (秒)", value=int(settings["poll_interval_seconds"]),
                min_value=5, max_value=600, step=5, key="g_poll",
            )
        _hr()
        st.markdown("**📊 持仓与资金阈値**")
        s5, s6 = st.columns(2)
        with s5:
            new_min_hold = st.number_input(
                "最少持仓数（底仓保护）",
                value=int(settings.get("min_holding_limit", 0)),
                min_value=0, max_value=50, step=1, key="g_minhold",
                help="活跃持仓数低于此値时，主页显示警告；0 = 不启用",
            )
        with s6:
            new_max_cap = st.number_input(
                "最大资金占用 (HKD)",
                value=float(settings.get("max_capital_usage", 0)),
                min_value=0.0, step=50000.0, key="g_maxcap",
                help="主页资金占用进度条的满格値；0 = 不显示进度条",
            )
        _hr()
        st.markdown("**🔔 通知推送**")
        s3, s4 = st.columns(2)
        with s3:
            new_bark = st.text_input(
                "Bark Token", value=settings.get("bark_token", ""),
                type="password", key="g_bark", help="留空则不推送 Bark 通知",
            )
        with s4:
            new_url = st.text_input(
                "Web 服务公网地址",
                value=settings.get("web_server_url", "http://localhost:8501"),
                key="g_url", help="Bark 回调链接使用此地址",
            )

        _hr()
        if st.button("💾 保存所有设置", key="save_settings"):
            config["settings"].update({
                "cash_reserve": new_cash, "bark_token": new_bark,
                "poll_interval_seconds": new_poll, "web_server_url": new_url,
                "min_holding_limit": int(new_min_hold),
                "max_capital_usage": float(new_max_cap),
            })
            save_config(config)
            st.toast("✅ 设置已保存，重启生效")
            st.rerun()

    st.markdown(
        '<div style="text-align:center;color:#E5E7EB;font-size:0.6rem;padding:20px 0 6px;">'
        'ValueShield v1.6 · 算法为辅，主观为主</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
