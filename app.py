"""
app.py - ValueShield v2.4
价値投资管家：总仓位管理 + 历史估值分位 + 观察者模式 + 同花顺风格 UI
"""

import json
import logging
import os

import streamlit as st

st.set_page_config(
    page_title="ValueShield",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from engine import (
    GridEngine,
    PositionSummary,
    WatcherTarget,
    compute_total_risk_capital,
    compute_total_risk_capital_v2,
    check_cash_warning,
)
from crawler import (
    fetch_realtime_price,
    fetch_dividend_ttm,
    compute_dividend_yield,
    compute_percentile,
    fetch_div_yield_history,
    fetch_pb_history,
    get_valuation_label,
)
from monitor import load_config, load_state, save_state, build_engines, build_watchers
from magic_formula import (
    StockScore,
    is_cache_fresh,
    load_cache as load_mf_cache,
    save_cache as save_mf_cache,
    scan_magic_formula,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

logger = logging.getLogger(__name__)

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
/* v2.4 观察者卡片 */
.lv-watcher-card {
    background: linear-gradient(135deg, #F0FDF4 0%, #ECFDF5 100%);
    border-radius: 14px;
    border: 1px solid #BBF7D0;
    padding: 18px 22px;
    margin-bottom: 12px;
}
/* v2.4 估值分位标签 */
.lv-val-badge {
    display: inline-block; padding: 3px 10px;
    border-radius: 8px; font-size: 0.76rem; font-weight: 600;
    margin: 2px 4px 2px 0;
}
.lv-val-badge.underval  { background: #ECFDF5; color: #065F46; border: 1px solid #6EE7B7; }
.lv-val-badge.overval   { background: #FEF2F2; color: #991B1B; border: 1px solid #FCA5A5; }
.lv-val-badge.neutral   { background: #F9FAFB; color: #374151; border: 1px solid #E5E7EB; }
/* v2.4 仓位占比条 */
.lv-band-info {
    font-size: 0.78rem; color: #6B7280;
    background: #F9FAFB; border-radius: 6px;
    padding: 5px 10px; margin-top: 4px;
    display: inline-block;
}
/* 侧边栏分组标题 */
.sb-group-title {
    font-size: 0.65rem; font-weight: 700; color: #6B7280;
    text-transform: uppercase; letter-spacing: 0.1em;
    padding: 8px 14px 4px; margin-top: 6px;
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
    """
    v2.4 双分组侧边栏：
    📊 当前持仓 (N) — 已有仓位的标的
    🔍 观察名单 (M) — 零持仓监控标的
    """
    stocks = [s for s in config["stocks"] if s.get("enabled", True)]
    watchers = config.get("watchers", [])
    pending_all = state.get("pending_confirmations", [])

    # 初始化 selected_code
    valid_codes = {s["code"] for s in stocks} | {f"watch_{w['code']}" for w in watchers}
    if "selected_code" not in st.session_state or st.session_state["selected_code"] not in valid_codes:
        st.session_state["selected_code"] = stocks[0]["code"] if stocks else ""

    with st.sidebar:
        st.markdown(
            '<div style="padding:16px 12px 8px;">'
            '<div style="font-size:1.1rem;font-weight:800;color:#111827;">🛡️ ValueShield</div>'
            '<div style="font-size:0.65rem;color:#9CA3AF;margin-top:2px;">v2.5 · 价值投资管家</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<hr class="lv-hr">', unsafe_allow_html=True)

        # ── 顶级模式切换器
        app_mode = st.radio(
            "导航模式",
            ["📈 仓位管理", "✨ 市场发现"],
            key="app_mode",
            label_visibility="collapsed",
            horizontal=True,
        )
        st.markdown('<hr class="lv-hr">', unsafe_allow_html=True)

        if app_mode == "✨ 市场发现":
            logger.debug("sidebar: 用户切换到「市场发现」模式 → 返回 __discovery__")
            st.caption("神奇公式全市场扫描")
            return "__discovery__"

        logger.debug("sidebar: 用户停留在「仓位管理」模式")

        # ── 📊 当前持仓
        st.markdown(
            f'<div class="sb-group-title">📊 当前持仓 ({len(stocks)})</div>',
            unsafe_allow_html=True,
        )
        for s in stocks:
            code = s["code"]
            name = s["name"]
            price = state.get("latest_prices", {}).get(code)
            n_pend = sum(1 for p in pending_all if p.get("code") == code)
            badge = f"  🔔{n_pend}" if n_pend else ""
            # 浮盈色
            ps_data = state.get("positions", {}).get(code, {}).get("position_summary")
            if ps_data and price:
                ps = PositionSummary.from_dict(ps_data)
                pnl_pct = ps.unrealized_pnl_pct(price) * 100
                pnl_str = f" {pnl_pct:+.1f}%"
            else:
                pnl_str = f" {price:.2f}" if price else ""
            is_sel = st.session_state["selected_code"] == code
            arrow = "▶ " if is_sel else "   "
            if st.button(
                f"{arrow}{name}{badge}\n        {code}.HK{pnl_str}",
                key=f"nav_{code}",
            ):
                st.session_state["selected_code"] = code
                st.rerun()

        st.markdown('<hr class="lv-hr">', unsafe_allow_html=True)

        # ── 🔍 观察名单
        st.markdown(
            f'<div class="sb-group-title">🔍 观察名单 ({len(watchers)})</div>',
            unsafe_allow_html=True,
        )
        for w in watchers:
            code = w["code"]
            name = w["name"]
            base = w.get("base_price", 0.0)
            w_price = state.get("watcher_prices", {}).get(code)
            if w_price and base:
                dist_pct = (w_price - base) / base * 100
                dist_str = f" {dist_pct:+.1f}%"
                status = "🟢 已达" if w_price <= base else f"⏳ 距{dist_pct:+.1f}%"
            else:
                dist_str = ""
                status = "⏳ 等待"
            sel_key = f"watch_{code}"
            is_sel = st.session_state["selected_code"] == sel_key
            arrow = "▶ " if is_sel else "   "
            if st.button(
                f"{arrow}👁 {name}\n        {code}.HK · {status}",
                key=f"nav_w_{code}",
            ):
                st.session_state["selected_code"] = sel_key
                st.rerun()

        st.markdown('<hr class="lv-hr">', unsafe_allow_html=True)
        last_upd = state.get("last_updated", "—")[:16]
        st.markdown(
            f'<div style="padding:6px 12px;font-size:0.65rem;color:#9CA3AF;">更新: {last_upd}</div>',
            unsafe_allow_html=True,
        )

    return st.session_state["selected_code"]


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

        wrap_cls = "big-confirm-wrap sell-btn" if is_sell else "big-confirm-wrap"
        btn_col, dismiss_col = st.columns([5, 1])
        with btn_col:
            st.markdown(f'<div class="{wrap_cls}">', unsafe_allow_html=True)
            btn_text = f"✅ 确认成交 · {name} · 第{grid_level + 1}格 · @{grid_price:.3f} HKD"
            if st.button(btn_text, key=f"bigpend_{code}_{grid_level}_{item_type}",
                         use_container_width=True):
                engine = engines.get(code)
                if engine:
                    if item_type == "buy":
                        if str(grid_level) not in engine.grid_occupied:
                            engine.confirm_buy(grid_level)
                        state["positions"][code] = engine.to_state_dict()
                        st.toast(f"✅ {name} 第{grid_level + 1}格 买入 @{grid_price:.3f} 已记录")
                    else:
                        engine.confirm_sell(item.get("holding_id", ""), grid_price)
                        state["positions"][code] = engine.to_state_dict()
                        st.toast(f"✅ {name} 第{grid_level + 1}格 止盈 @{grid_price:.3f} 已记录")
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

def render_auto_align(engine: GridEngine, code: str, current_price: float, state: dict) -> bool:
    if not current_price or current_price <= 0:
        st.warning("当前无行情数据，请先在监控页刷新行情。")
        return False

    prices = engine.grid_prices()
    to_fill = [i for i, p in enumerate(prices) if p >= current_price and str(i) not in engine.grid_occupied]

    if not to_fill:
        st.success("✅ 所有触发格子已对齐，无需校准。")
        return False

    st.info(f"📐 现价 {current_price:.3f} HKD 偏离基准价较大，共 {len(to_fill)} 格待对齐，建议一键对齐。")
    if st.button("🎯 一键对齐", key=f"align_{code}"):
        for i in to_fill:
            engine.confirm_buy(i)
        # 同时清除该标的所有历史待确认提醒
        state["pending_confirmations"] = [
            p for p in state.get("pending_confirmations", [])
            if p.get("code") != code
        ]
        state["positions"][code] = engine.to_state_dict()
        save_state(state)
        st.toast(f"✅ 已对齐 {len(to_fill)} 格，历史待确认提醒已清除。")
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
    watchers = build_watchers(config)

    selected_code = render_sidebar(config, state)

    # ── 顶级模式路由：市场发现模式全屏展示神奇公式
    if selected_code == "__discovery__":
        logger.debug("main: 进入市场发现模式，调用 _render_magic_formula_tab")
        _render_magic_formula_tab(config, state)
        return

    logger.debug("main: 仓位管理模式，selected_code=%s", selected_code)

    # v2.4 风险计算：优先使用预算公式，退化用网格压力测试
    current_prices = state.get("latest_prices", {})
    total_risk = compute_total_risk_capital_v2(engines, current_prices)
    if total_risk == 0.0:
        total_risk = compute_total_risk_capital(engines)
    cash_reserve = settings["cash_reserve"]
    n_pending_all = len(state.get("pending_confirmations", []))
    total_holdings = sum(len(e.active_holdings()) for e in engines.values())

    # ── 顶部标题行（单行，极简）
    title_col, refresh_col = st.columns([9, 1])
    with title_col:
        last_upd = state.get("last_updated", "")[:16].replace("T", " ") or "未同步"
        pend_color = "#D97706" if n_pending_all > 0 else "#9CA3AF"
        risk_color = "#DC2626" if check_cash_warning(total_risk, cash_reserve) else "#9CA3AF"
        st.markdown(
            '<span style="font-size:1.2rem;font-weight:800;color:#111827;">🛡️ ValueShield</span>'
            '<span style="font-size:0.72rem;color:#9CA3AF;margin-left:12px;">'
            f'风险资金 <span style="color:{risk_color};font-weight:600">{total_risk:,.0f}</span> HKD'
            f' · 持仓 <b style="color:#111827">{total_holdings}</b> 笔'
            f' · 待确认 <span style="color:{pend_color};font-weight:600">{n_pending_all}</span> 条'
            f' · 🕐 {last_upd}</span>',
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

    # ── v2.4：观察者卡片路由
    if selected_code.startswith("watch_"):
        watch_code = selected_code[6:]
        watcher_cfg = next((w for w in config.get("watchers", []) if w["code"] == watch_code), None)
        if watcher_cfg:
            _render_watcher_card(watcher_cfg, state, config, engines)
        return

    stock_cfg = next((s for s in config["stocks"] if s["code"] == selected_code), None)
    if not stock_cfg:
        st.warning("未找到选中标的配置。")
        return

    code = stock_cfg["code"]
    name = stock_cfg["name"]
    engine = engines.get(code)
    if engine is None:
        st.warning(
            f"⚠️ 标的 **{name}**（{code}）尚未初始化引擎。\n\n"
            "通常原因：首次部署或 `state.json` 被重置，`monitor.py` 还未运行一次。\n"
            "请先启动后台监控服务：`python3.12 monitor.py`，等待第一次轮询完成后刷新页面。"
        )
        logger.warning("main: engines 中不存在 %s，跳过渲染（首次部署？）", code)
        return
    current_price = state.get("latest_prices", {}).get(code)
    annual_div = state.get("latest_dividend_ttm", {}).get(code, stock_cfg.get("annual_dividend_hkd", 0.0))
    div_yield = compute_dividend_yield(annual_div, current_price) if current_price else 0.0

    # v2.4 估值分位（从缓存读取，不在 UI 层实时抓取）
    val_hist = state.get("valuation_history", {}).get(code, {})
    dy_hist = val_hist.get("div_yield", [])
    pb_hist = val_hist.get("pb", [])
    dy_pct = compute_percentile(div_yield, dy_hist, higher_is_better=True)
    pb_pct = compute_percentile(0.0, pb_hist, higher_is_better=False)  # PB 值由监控层填充
    dy_label = get_valuation_label(dy_pct, "股息率", div_yield * 100, unit="%")
    pb_label = get_valuation_label(pb_pct, "PB", 0.0, unit="x") if pb_hist else ""

    # ── 标签页
    tab_monitor, tab_config, tab_settings = st.tabs(
        ["📊 实时监控", "⚙️ 网格配置", "🛠️ 系统设置"]
    )

    # ════════════════════════════════════════════════════════════════
    # 📊 监控页
    # ════════════════════════════════════════════════════════════════
    with tab_monitor:
        # ── 资产总账看板
        stats = compute_portfolio_stats(engines, state)
        p1, p2, p3 = st.columns(3)
        pnl_color = "#10B981" if stats["realized_profit"] >= 0 else "#EF4444"
        with p1:
            with st.container(border=True):
                st.caption("资产占用（持仓总市值）")
                st.markdown(
                    f'<div style="font-size:1.55rem;font-weight:700;color:#111827;line-height:1.2;">'
                    f'{stats["total_market_value"]:,.0f}'
                    f'<span style="font-size:0.82rem;color:#9CA3AF;font-weight:400;"> HKD</span></div>',
                    unsafe_allow_html=True,
                )
        with p2:
            with st.container(border=True):
                st.caption("🏠 底仓规模")
                st.markdown(
                    f'<div style="font-size:1.55rem;font-weight:700;color:#26A69A;line-height:1.2;">'
                    f'{stats["core_value"]:,.0f}'
                    f'<span style="font-size:0.82rem;color:#9CA3AF;font-weight:400;"> HKD</span></div>',
                    unsafe_allow_html=True,
                )
        with p3:
            with st.container(border=True):
                st.caption("收割成果（累计已实现）")
                st.markdown(
                    f'<div style="font-size:1.55rem;font-weight:700;color:{pnl_color};line-height:1.2;">'
                    f'{stats["realized_profit"]:+,.0f}'
                    f'<span style="font-size:0.82rem;color:#9CA3AF;font-weight:400;"> HKD</span></div>',
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

        # ── v2.4 总仓位摘要卡片（同花顺风格）
        ps = engine.position_summary
        with st.container():
            st.markdown(
                f'<span class="lv-badge">{code}.HK</span>'
                f' <span style="font-size:1.15rem;font-weight:800;color:#111827;">{name}</span>'
                f'<span style="font-size:0.72rem;color:#9CA3AF;margin-left:10px;">'
                f'Step {engine.step:.3f} · {engine.grid_levels} 格</span>',
                unsafe_allow_html=True,
            )

        # 估值分位标签（若有历史数据）
        if dy_label:
            val_cls = "underval" if dy_pct >= 75 else ("overval" if dy_pct >= 0 and dy_pct < 25 else "neutral")
            st.markdown(
                f'<span class="lv-val-badge {val_cls}">{dy_label}</span>'
                + (f'<span class="lv-val-badge neutral">{pb_label}</span>' if pb_label else ""),
                unsafe_allow_html=True,
            )

        # PositionSummary 摘要行
        if ps is not None and ps.total_shares > 0:
            pnl_val = ps.unrealized_pnl(current_price) if current_price else 0.0
            pnl_pct = ps.unrealized_pnl_pct(current_price) * 100 if current_price else 0.0
            pnl_color = "#16A34A" if pnl_val >= 0 else "#DC2626"
            ps_c1, ps_c2, ps_c3, ps_c4, ps_c5 = st.columns(5)
            with ps_c1:
                with st.container(border=True):
                    st.caption("总持股")
                    st.markdown(f'<div style="font-size:1.3rem;font-weight:700;">{ps.total_shares:,}</div><div style="font-size:0.7rem;color:#9CA3AF;">底仓 {ps.core_shares:,} | 波段 {ps.band_shares:,}</div>', unsafe_allow_html=True)
            with ps_c2:
                with st.container(border=True):
                    st.caption("均价")
                    st.markdown(f'<div style="font-size:1.3rem;font-weight:700;">{ps.avg_cost:.3f}</div><div style="font-size:0.7rem;color:#9CA3AF;">HKD</div>', unsafe_allow_html=True)
            with ps_c3:
                with st.container(border=True):
                    st.caption("现价")
                    price_str = f"{current_price:.3f}" if current_price else "--"
                    st.markdown(f'<div style="font-size:1.3rem;font-weight:700;">{price_str}</div><div style="font-size:0.7rem;color:#9CA3AF;">HKD</div>', unsafe_allow_html=True)
            with ps_c4:
                with st.container(border=True):
                    st.caption("浮盈")
                    st.markdown(
                        f'<div style="font-size:1.3rem;font-weight:700;color:{pnl_color};">{pnl_val:+,.0f}</div>'
                        f'<div style="font-size:0.7rem;color:{pnl_color};">{pnl_pct:+.2f}%</div>',
                        unsafe_allow_html=True,
                    )
            with ps_c5:
                with st.container(border=True):
                    st.caption("市值")
                    mv = ps.market_value(current_price) if current_price else ps.cost_value
                    st.markdown(f'<div style="font-size:1.3rem;font-weight:700;">{mv:,.0f}</div><div style="font-size:0.7rem;color:#9CA3AF;">HKD</div>', unsafe_allow_html=True)

            # 预算进度条
            if ps.total_budget > 0:
                usage = ps.budget_usage_pct(current_price or ps.avg_cost)
                risk_need = ps.risk_capital_needed(current_price or 0)
                st.progress(
                    usage,
                    text=(
                        f"📊 预算进度：已投 {ps.cost_value:,.0f} / 总预算 {ps.total_budget:,.0f} HKD"
                        f"  ({usage * 100:.1f}%)  · 剩余风险资金需求 {risk_need:,.0f} HKD"
                    ),
                )

            # 波段仓位为 0 时显示提示
            if ps.band_shares == 0:
                st.info("🏠 波段仓位已清空，系统已屏蔽全部卖出提醒（底仓保护模式）。")

        _hr()

        # ── 同花顺风格持仓看板
        stock_hdr_col, refresh_btn_col = st.columns([8, 1])
        with stock_hdr_col:
            pass  # 标题已在上方显示
        with refresh_btn_col:
            if st.button("📡 刷新", key=f"fetch_{code}"):
                _fetch_price_cached.clear()
                with st.spinner("数据同步中..."):
                    new_price = _fetch_price_cached(stock_cfg["akshare_code"])
                if new_price:
                    state.setdefault("latest_prices", {})[code] = new_price
                    save_state(state)
                    st.toast(f"✅ {new_price:.3f} HKD")
                    st.rerun()
                else:
                    st.error("获取失败")

        # ── 紧急校准：数据源彻底失效时手动输入现价
        with st.expander("🚨 紧急校准（数据源失效时使用）", expanded=False):
            st.caption("仅在行情接口全部失效、显示价格明显错误时使用。输入后点击【覆盖现价】立即生效。")
            manual_col, btn_col = st.columns([3, 1])
            with manual_col:
                manual_price_key = f"manual_price_{code}"
                if manual_price_key not in st.session_state:
                    st.session_state[manual_price_key] = float(current_price or 0.0)
                manual_price = st.number_input(
                    "手动输入现价（HKD）",
                    min_value=0.001, step=0.01, format="%.3f",
                    key=manual_price_key,
                )
            with btn_col:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("✏️ 覆盖现价", key=f"override_price_{code}"):
                    if manual_price and manual_price > 0:
                        state.setdefault("latest_prices", {})[code] = float(manual_price)
                        save_state(state)
                        st.toast(f"✅ 现价已手动覆盖为 {manual_price:.3f} HKD")
                        st.rerun()
                    else:
                        st.error("请输入有效价格")

        active = engine.active_holdings()
        total_shares = sum(h.lot_size for h in active)
        total_cost_val = sum(h.buy_price * h.lot_size for h in active)
        avg_cost = total_cost_val / total_shares if total_shares > 0 else 0.0
        cp = current_price or avg_cost
        total_pnl_val = (total_shares * cp) - total_cost_val
        total_pnl_pct = (total_pnl_val / total_cost_val * 100) if total_cost_val > 0 else 0.0
        core_shares = sum(h.lot_size for h in active if h.is_core)
        min_hold_shares = int(settings.get("min_holding_limit", 0))
        bottom_protected = min_hold_shares > 0 and total_shares <= min_hold_shares

        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("持仓股数", f"{total_shares:,}")
        with m2:
            st.metric("持仓均价", f"{avg_cost:.3f}" if avg_cost > 0 else "—")
        with m3:
            price_delta = f"{cp - avg_cost:+.3f}" if avg_cost > 0 and cp > 0 else None
            st.metric("最新价", f"{cp:.3f}" if cp > 0 else "—", delta=price_delta)
        with m4:
            pnl_delta = f"{total_pnl_pct:+.2f}%" if total_shares > 0 else None
            st.metric("浮动盈亏", f"{total_pnl_val:+,.0f} HKD" if total_shares > 0 else "—",
                      delta=pnl_delta)
        with m5:
            st.metric("🏠 底仓股数", f"{core_shares:,}",
                      help=f"底仓保护阈值 {min_hold_shares:,} 股" if min_hold_shares > 0 else "底仓保护未启用")

        if bottom_protected:
            st.info(
                f"🏠 **底仓保护激活**：持股 {total_shares:,} 股 ≤ 阈值 {min_hold_shares:,} 股，"
                f"所有卖出信号已自动屏蔽。"
            )

        _hr()

        if active:
            st.markdown(
                f'<div style="font-size:0.74rem;font-weight:600;color:#374151;margin-bottom:10px;">'
                f'📋 持仓明细 ({len(active)} 笔)</div>',
                unsafe_allow_html=True,
            )
            for holding in active:
                h_pnl_pct = holding.profit_pct_if_sold_at(cp) * 100
                h_pnl_val = holding.profit_if_sold_at(cp)
                tp_price = holding.take_profit_price
                in_core_range = bottom_protected or holding.is_core

                if in_core_range:
                    # 底仓行：整行绿松石色背景 + 🔒 锁定图标
                    pnl_c = "#10B981" if h_pnl_pct >= 0 else "#EF4444"
                    lock_label = "🔒 底仓锁定" if holding.is_core else "🔒 阈值保护"
                    st.markdown(
                        f'<div style="background:#E0F2F1;border-left:3px solid #26A69A;'
                        f'border-radius:8px;padding:8px 14px;margin-bottom:6px;'
                        f'display:flex;justify-content:space-between;align-items:center;">'
                        f'  <div>'
                        f'    <span style="font-weight:600;color:#111827;">'
                        f'第 {holding.grid_level + 1} 格 · {holding.lot_size:,} 股</span>'
                        f'    <span style="background:#26A69A;color:#fff;border-radius:4px;'
                        f'padding:1px 6px;font-size:0.62rem;margin-left:6px;">🔒 锁定</span>'
                        f'    <div style="font-size:0.74rem;color:#5eada5;margin-top:2px;">'
                        f'成本 {holding.buy_price:.3f} · 止盈 {tp_price:.3f}</div>'
                        f'  </div>'
                        f'  <div style="text-align:right;">'
                        f'    <div style="font-size:1.05rem;font-weight:700;color:{pnl_c};">'
                        f'{h_pnl_pct:+.2f}%</div>'
                        f'    <div style="font-size:0.78rem;color:#6B7280;">{h_pnl_val:+,.0f} HKD</div>'
                        f'    <div style="font-size:0.7rem;color:#26A69A;font-weight:600;margin-top:2px;">'
                        f'{lock_label}</div>'
                        f'  </div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    # 普通行：列布局 + 卖出按钮
                    h_pnl_cls = "lv-pnl-pos" if h_pnl_pct >= 0 else "lv-pnl-neg"
                    h1, h2, h3 = st.columns([4, 2, 1])
                    with h1:
                        st.markdown(
                            f'<div style="font-weight:600;color:#111827;">'
                            f'第 {holding.grid_level + 1} 格 · {holding.lot_size:,} 股</div>'
                            f'<div style="font-size:0.74rem;color:#9CA3AF;">'
                            f'成本 {holding.buy_price:.3f} · 止盈 {tp_price:.3f}</div>',
                            unsafe_allow_html=True,
                        )
                    with h2:
                        st.markdown(
                            f'<div class="{h_pnl_cls}" style="font-size:1.05rem;">{h_pnl_pct:+.2f}%</div>'
                            f'<div style="color:#6B7280;font-size:0.78rem;">{h_pnl_val:+,.0f} HKD</div>',
                            unsafe_allow_html=True,
                        )
                    with h3:
                        if st.button("✅ 卖出", key=f"sell_{holding.holding_id}",
                                     help=f"止盈价 @{tp_price:.3f} HKD"):
                            engine.confirm_sell(holding.holding_id, tp_price)
                            state["positions"][code] = engine.to_state_dict()
                            save_state(state)
                            st.toast(f"✅ 已卖出 @{tp_price:.3f}，盈利 {h_pnl_pct:.2f}%")
                            st.rerun()
                _hr()
        else:
            st.markdown(
                '<div style="color:#9CA3AF;font-size:0.85rem;text-align:center;padding:20px 0;">'
                '暂无持仓，等待触发信号...</div>',
                unsafe_allow_html=True,
            )

    # ════════════════════════════════════════════════════════════════
    # ⚙️ 配置页
    # ════════════════════════════════════════════════════════════════
    with tab_config:
        # ── v2.4 一键对齐账户：总仓位简化录入
        with st.expander("👤 一键对齐账户（v2.4 总持仓录入）", expanded=(engine.position_summary is None)):
            st.caption("输入券商账户中的实际持仓数据，系统将据此计算浮盈、预算进度和波段仓位。底仓股数不触发止盈。")
            ps_cur = engine.position_summary or PositionSummary()
            ac1, ac2, ac3, ac4 = st.columns(4)
            with ac1:
                new_total_shares = st.number_input(
                    "总持有股数", min_value=0, step=100, key=f"ps_total_{code}",
                    value=ps_cur.total_shares,
                )
            with ac2:
                new_avg_cost = st.number_input(
                    "平均成本价 (HKD)", min_value=0.0, step=0.01, format="%.4f",
                    key=f"ps_cost_{code}", value=ps_cur.avg_cost,
                )
            with ac3:
                new_core = st.number_input(
                    "底仓锁定股数", min_value=0, step=100, key=f"ps_core_{code}",
                    value=ps_cur.core_shares,
                    help="底仓为长线烟蒂核心仓，系统严禁触发卖出提醒",
                )
            with ac4:
                new_budget = st.number_input(
                    "计划总投入 (HKD)", min_value=0.0, step=10000.0, format="%.0f",
                    key=f"ps_budget_{code}",
                    value=ps_cur.total_budget or float(stock_cfg.get("total_budget", 0)),
                )
            if st.button("✅ 同步账户", key=f"ps_sync_{code}", type="primary"):
                engine.position_summary = PositionSummary(
                    total_shares=int(new_total_shares),
                    avg_cost=float(new_avg_cost),
                    core_shares=int(new_core),
                    total_budget=float(new_budget),
                )
                state.setdefault("positions", {})[code] = engine.to_state_dict()
                save_state(state)
                st.toast(f"✅ {name} 账户已同步：{new_total_shares:,} 股 · 均价 {new_avg_cost:.3f} · 底仓 {new_core:,}")
                st.rerun()

        _hr()

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

        # ── 预初始化 session_state，避免 value= 与 key= 同时存在时的 Widget 冲突
        if f"base_{code}" not in st.session_state:
            st.session_state[f"base_{code}"] = float(engine.base_price)
        if f"step_{code}" not in st.session_state:
            st.session_state[f"step_{code}"] = float(engine._step or 0)
        if f"gc_{code}" not in st.session_state:
            st.session_state[f"gc_{code}"] = int(settings.get("grid_levels", 20))
        if f"qty_{code}" not in st.session_state:
            st.session_state[f"qty_{code}"] = int(stock_cfg.get("lot_size", 500))

        # ── 同步现价为 Base 快捷按钮
        if current_price:
            if st.button(
                f"📌 同步现价 {current_price:.3f} → Base",
                key=f"sync_base_{code}",
                help="将当前市场价填入 Base_Price 输入框，方便快速重置格子",
            ):
                st.session_state[f"base_{code}"] = float(current_price)
                st.toast("✅ 已填充最新市价，请点击【应用】生效")
                st.rerun()

        r1c1, r1c2, r1c3 = st.columns([2, 2, 1])
        with r1c1:
            new_base = st.number_input(
                "基准价 Base_Price",
                min_value=0.01, step=0.01, key=f"base_{code}",
                help="买入触发价 = Base_Price − (n × Step)",
            )
        with r1c2:
            new_step = st.number_input(
                "步长 Step（0 = 自动）",
                min_value=0.0, step=0.001, format="%.4f", key=f"step_{code}",
                help="相邻格子的价格间距",
            )
        with r1c3:
            st.markdown("<br><br>", unsafe_allow_html=True)
            apply_btn = st.button("✅ 应用", key=f"apply_{code}")

        r2c1, r2c2, _ = st.columns([2, 2, 1])
        with r2c1:
            new_grid_count = st.number_input(
                "网格总数 Grid_Count",
                min_value=1, max_value=100, step=1, key=f"gc_{code}",
                help="共布置多少个买入格子；卖出点 = 买入价 × (1 + 止盈比例)",
            )
        with r2c2:
            new_quantity = st.number_input(
                "单笔股数 Quantity",
                min_value=100, step=100, key=f"qty_{code}",
                help="每格触发时建议买入的股数",
            )

        if apply_btn:
            engine.set_base_price(new_base)
            if new_step > 0:
                engine.set_step(new_step)
            engine.grid_levels = int(new_grid_count)
            engine.lot_size = int(new_quantity)
            for s in config["stocks"]:
                if s["code"] == code:
                    s["base_price"] = new_base
                    s["step"] = new_step if new_step > 0 else None
                    s["lot_size"] = int(new_quantity)
            config["settings"]["grid_levels"] = int(new_grid_count)
            save_config(config)
            state["positions"][code] = engine.to_state_dict()
            save_state(state)
            st.toast(
                f"✅ Base={new_base:.4f} Step={engine.step:.4f} "
                f"N={int(new_grid_count)}格 Q={int(new_quantity)}股 已应用"
            )
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
                "底仓保护阈値（股数）",
                value=int(settings.get("min_holding_limit", 0)),
                min_value=0, max_value=1000000, step=500, key="g_minhold",
                help="持股总数 ≤ 此値时屏蔽全部卖出信号；0 = 不启用",
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
        'ValueShield v2.5 · 价值投资管家 · 算法为辅，主观为主</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# v2.4 观察者卡片渲染
# ─────────────────────────────────────────────────────────────────────────────

def _render_watcher_card(watcher_cfg: dict, state: dict, config: dict, engines: dict) -> None:
    """渲染零持仓观察者卡片：展示建仓价、当前价、距离、预算。"""
    code = watcher_cfg["code"]
    name = watcher_cfg["name"]
    base_price = watcher_cfg.get("base_price", 0.0)
    total_budget = watcher_cfg.get("total_budget", 0.0)
    w_price = state.get("watcher_prices", {}).get(code)

    st.markdown(
        f'<span class="lv-badge">{code}.HK</span>'
        f' <span style="font-size:1.15rem;font-weight:800;color:#111827;">👁 {name}</span>'
        f'<span style="font-size:0.72rem;color:#6B7280;margin-left:10px;">观察名单</span>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        with st.container(border=True):
            st.caption("建仓价（安全边际）")
            st.markdown(f'<div style="font-size:1.6rem;font-weight:700;color:#2563EB;">{base_price:.3f}</div><div style="font-size:0.7rem;color:#9CA3AF;">HKD</div>', unsafe_allow_html=True)
    with c2:
        with st.container(border=True):
            st.caption("当前价")
            if w_price:
                color = "#16A34A" if w_price <= base_price else "#111827"
                pct = (w_price - base_price) / base_price * 100
                st.markdown(f'<div style="font-size:1.6rem;font-weight:700;color:{color};">{w_price:.3f}</div><div style="font-size:0.7rem;color:#9CA3AF;">{pct:+.1f}% 距建仓价</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="font-size:1.6rem;font-weight:700;color:#9CA3AF;">--</div><div style="font-size:0.7rem;color:#9CA3AF;">未同步</div>', unsafe_allow_html=True)
    with c3:
        with st.container(border=True):
            st.caption("计划投入")
            budget_str = f"{total_budget:,.0f}" if total_budget > 0 else "--"
            st.markdown(f'<div style="font-size:1.6rem;font-weight:700;color:#374151;">{budget_str}</div><div style="font-size:0.7rem;color:#9CA3AF;">HKD</div>', unsafe_allow_html=True)

    if w_price and w_price <= base_price:
        st.success(f"🟢 {name} 已到达安全边际！建仓价 {base_price:.3f} HKD，现价 {w_price:.3f} HKD")
    elif w_price:
        need_drop = w_price - base_price
        st.info(f"⏳ 距建仓价还需下跌 {need_drop:.3f} HKD ({(need_drop/w_price*100):.1f}%)")

    _hr()

    # 刷新价格
    if st.button("📡 刷新价格", key=f"fetch_watcher_{code}"):
        _fetch_price_cached.clear()
        with st.spinner("数据同步中..."):
            new_price = _fetch_price_cached(watcher_cfg.get("akshare_code", code))
        if new_price:
            state.setdefault("watcher_prices", {})[code] = new_price
            save_state(state)
            st.toast(f"✅ {name} 现价 {new_price:.3f} HKD")
            st.rerun()

    _hr()

    # 一键转正（首次买入录入后移出观察名单）
    with st.expander("➕ 记录首笔买入（转入持仓）"):
        st.caption("录入首笔买入后，该标的将自动移入持仓名单。")
        f1, f2, f3 = st.columns(3)
        with f1:
            buy_price_in = st.number_input("买入价 (HKD)", min_value=0.001, step=0.01, format="%.3f", key=f"w_buy_price_{code}")
        with f2:
            buy_shares_in = st.number_input("买入股数", min_value=100, step=100, key=f"w_buy_shares_{code}")
        with f3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("✅ 确认建仓", key=f"w_confirm_{code}"):
                # 将观察者转化为持仓标的（写入 config）
                new_stock = {
                    "code": code,
                    "name": name,
                    "exchange": "HK",
                    "akshare_code": watcher_cfg.get("akshare_code", code),
                    "base_price": buy_price_in,
                    "hist_min": buy_price_in * 0.5,
                    "lot_size": int(buy_shares_in),
                    "step": None,
                    "take_profit_pct": 0.07,
                    "enabled": True,
                    "annual_dividend_hkd": 0.0,
                    "total_budget": total_budget,
                }
                config["stocks"].append(new_stock)
                # 移除观察者
                config["watchers"] = [w for w in config.get("watchers", []) if w["code"] != code]
                save_config(config)
                # 初始化 position_summary
                from engine import PositionSummary as PS
                ps_new = PS(total_shares=int(buy_shares_in), avg_cost=float(buy_price_in), core_shares=0, total_budget=total_budget)
                state.setdefault("positions", {})[code] = {"grid_occupied": {}, "holdings": [], "position_summary": ps_new.to_dict()}
                save_state(state)
                st.session_state["selected_code"] = code
                st.toast(f"✅ {name} 已从观察名单移入持仓！")
                st.rerun()



# ─────────────────────────────────────────────────────────────────────────────
# ✨ 神奇公式 Top 30 标签页
# ─────────────────────────────────────────────────────────────────────────────

def _render_magic_formula_tab(config: dict, state: dict) -> None:
    """渲染神奇公式全市场扫描看板。"""

    st.markdown("### ✨ 格林布拉特神奇公式 — 全市场 Top 30 烟蒂股")
    st.caption(
        "算法：ROC = EBIT / (净营运资本 + 净固定资产)；EY = EBIT / EV。"
        " A 股使用实际财报数据（full），H 股部分使用 PE/PB 近似（approx）。"
        " 数据每日盘前自动缓存，也可手动刷新。"
    )

    cache = load_mf_cache()
    cached_at = cache.get("cached_at", "")[:16].replace("T", " ") if cache else ""
    fresh = cache is not None and is_cache_fresh(cache)

    # ── 状态栏
    col_info, col_btn = st.columns([7, 3])
    with col_info:
        if fresh and cached_at:
            scanned = cache.get("scanned_count", 0)
            universe = cache.get("universe_size", 0)
            st.success(
                f"🟢 缓存有效（{cached_at}）：扫描宇宙 {universe} 只 → "
                f"有效财务数据 {scanned} 只"
            )
        elif cache and not fresh:
            st.warning(f"🟡 缓存已过期（{cached_at}），建议重新扫描")
        else:
            st.info("🔵 尚无缓存，请点击 **重新扫描全市场** 开始首次扫描（约 2-5 分钟）")

    with col_btn:
        do_scan = st.button("🔄 重新扫描全市场", use_container_width=True)

    # ── 执行扫描
    if do_scan:
        progress_bar = st.progress(0.0, text="准备开始扫描…")

        def _cb(pct: float, msg: str) -> None:
            progress_bar.progress(min(pct, 1.0), text=msg)

        with st.spinner("神奇公式全市场扫描中，请耐心等待…"):
            try:
                cache = scan_magic_formula(top_n=30, include_h=True, progress_callback=_cb)
                st.success("✅ 扫描完成！")
                st.rerun()
            except Exception as exc:
                st.error(f"扫描失败：{exc}")
        return

    # ── 展示 Top 30
    if not cache or not cache.get("top_stocks"):
        logger.warning("_render_magic_formula_tab: 缓存为空或 top_stocks 为空列表")
        st.warning(
            "⚠️ 暂无神奇公式数据。\n\n"
            "可能原因：\n"
            "- 首次使用，尚未执行扫描\n"
            "- 扫描过程中网络/代理异常，导致结果集为空\n"
            "- AkShare 接口返回格式变更\n\n"
            "**请点击上方「🔄 重新扫描全市场」按钮**，并观察进度条是否有数据返回。\n"
            "若扫描完成后仍为空，请检查网络连接或代理设置。"
        )
        return

    top_stocks = [StockScore.from_dict(d) for d in cache["top_stocks"]]

    # ── 汇总指标行
    m1, m2, m3, m4 = st.columns(4)
    a_count = sum(1 for s in top_stocks if s.market == "A")
    h_count = len(top_stocks) - a_count
    avg_roc = sum(s.roc for s in top_stocks) / len(top_stocks) if top_stocks else 0.0
    avg_ey = sum(s.ey for s in top_stocks) / len(top_stocks) if top_stocks else 0.0
    with m1:
        st.metric("A 股入选", a_count)
    with m2:
        st.metric("H 股入选", h_count)
    with m3:
        st.metric("平均 ROC", f"{avg_roc:.1%}")
    with m4:
        st.metric("平均 EY", f"{avg_ey:.1%}")

    st.divider()

    # ── Top 30 表格
    for stock in top_stocks:
        roc_str = f"{stock.roc:.1%}"
        ey_str = f"{stock.ey:.1%}"
        ah_str = (
            f"H股折价 {abs(stock.ah_discount_pct):.1f}%"
            if stock.ah_discount_pct is not None and stock.ah_discount_pct < -1
            else "—"
        )
        quality_badge = "🔬" if stock.data_quality == "approx" else "✅"

        with st.container(border=True):
            row1, row2, row3 = st.columns([1, 5, 4]), st.columns([2, 2, 2, 2, 2]), st.columns([3, 3, 4])

            with row1[0]:
                st.markdown(
                    f'<div style="font-size:1.5rem;font-weight:900;color:#6B7280;'
                    f'text-align:center;line-height:1.8">#{stock.combined_rank}</div>',
                    unsafe_allow_html=True,
                )
            with row1[1]:
                badge_color = "#2563EB" if stock.market == "A" else "#7C3AED"
                st.markdown(
                    f'<span style="background:{badge_color};color:#fff;'
                    f'font-size:0.72rem;padding:2px 6px;border-radius:4px;">{stock.market}</span>'
                    f' <b style="font-size:1.05rem">{stock.name}</b>'
                    f' <span style="color:#6B7280;font-size:0.85rem">{stock.code}</span>'
                    f' {quality_badge}',
                    unsafe_allow_html=True,
                )
            with row1[2]:
                st.markdown(
                    f'<div style="text-align:right;font-size:1.1rem;'
                    f'font-weight:700;color:#111827;">'
                    f'{"HKD" if stock.market == "H" else "CNY"} {stock.price:.2f}</div>',
                    unsafe_allow_html=True,
                )

            with row2[0]:
                st.metric("ROC", roc_str, help="资本回报率 = EBIT / (净营运资本 + 净固定资产)")
            with row2[1]:
                st.metric("EY", ey_str, help="盈利收益率 = EBIT / 企业价值")
            with row2[2]:
                st.metric("ROC 排名", f"#{stock.roc_rank}")
            with row2[3]:
                st.metric("EY 排名", f"#{stock.ey_rank}")
            with row2[4]:
                st.metric("综合排名", f"#{stock.combined_rank}", help="综合排名 = ROC排名 + EY排名，越低越好")

            with row3[0]:
                if ah_str != "—":
                    st.markdown(
                        f'<span style="background:#D1FAE5;color:#065F46;'
                        f'font-size:0.8rem;padding:3px 8px;border-radius:6px;">'
                        f'🔀 {ah_str}</span>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("")

            with row3[1]:
                # 一键加入观察名单
                if st.button("➕ 加入观察", key=f"mf_watch_{stock.code}"):
                    watchers = config.setdefault("watchers", [])
                    if not any(w["code"] == stock.code for w in watchers):
                        watchers.append({
                            "code": stock.code,
                            "name": stock.name,
                            "akshare_code": stock.code,
                            "base_price": round(stock.price * 0.9, 3),  # 默认安全边际 = 九折
                            "total_budget": 0.0,
                            "enabled": True,
                        })
                        from monitor import CONFIG_PATH as _CP
                        import json as _json
                        with open(_CP, "w", encoding="utf-8") as _f:
                            _json.dump(config, _f, ensure_ascii=False, indent=2)
                        st.success(f"✅ {stock.name} 已加入观察名单（建仓价 = 九折）")
                        st.rerun()
                    else:
                        st.info("已在观察名单中")

            with row3[2]:
                # 复制财务摘要
                summary = (
                    f"【神奇公式分析摘要】{stock.name}（{stock.code}）\n"
                    f"市场：{stock.market}股 | 现价：{stock.price:.2f}\n"
                    f"ROC（资本回报率）：{stock.roc:.1%}（排名 #{stock.roc_rank}）\n"
                    f"EY（盈利收益率）：{stock.ey:.1%}（排名 #{stock.ey_rank}）\n"
                    f"综合排名：#{stock.combined_rank}\n"
                    f"EBIT 估算：{stock.ebit/1e8:.2f} 亿\n"
                    f"企业价值：{stock.ev/1e8:.2f} 亿\n"
                    f"数据质量：{'实算（EBIT）' if stock.data_quality == 'full' else 'PE/PB 近似'}\n"
                    f"（以上数据来自最新年报/季报，请结合定性分析使用）"
                )
                st.code(summary, language=None)

if __name__ == "__main__":
    main()


