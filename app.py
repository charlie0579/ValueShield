"""
app.py - ValueShield Web 看板 v1.1
修复：
  - 热力网格改用 st.components.v1.html() 渲染，避免 raw HTML 显示
  - 新增"待确认提醒"一键确认流程（无需手动输入价格）
  - 新增"一键根据现价对齐格子"自动校准功能
  - 卖出确认直接用止盈价，删除手动输入卖出价
  - 汇总栏增加"待确认条数"徽标
"""

import json
import os
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

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
# 全局 CSS
# ─────────────────────────────────────────────────────────────────────────────
DARK_CSS = """
<style>
html, body, [data-testid="stAppViewContainer"] {
    background-color: #0d1117 !important;
    color: #e6edf3 !important;
    font-family: 'SF Pro Display', 'PingFang SC', 'Helvetica Neue', Arial, sans-serif;
}
[data-testid="stSidebar"] { background-color: #161b22 !important; }
.vs-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 14px 18px; margin-bottom: 12px;
}
.vs-card-title { font-size: 0.72rem; color: #8b949e; text-transform: uppercase;
    letter-spacing: 0.08em; margin-bottom: 4px; }
.vs-card-value { font-size: 1.5rem; font-weight: 700; color: #f0f6fc; }
.vs-card-value.up   { color: #3fb950; }
.vs-card-value.down { color: #f85149; }
.vs-card-value.warn { color: #d29922; }
.risk-banner {
    background: linear-gradient(135deg, #3d1a1a, #5c1a1a);
    border: 2px solid #f85149; border-radius: 10px;
    padding: 12px 18px; margin-bottom: 14px;
    color: #ff7b72; font-weight: 700; text-align: center;
}
.pending-card {
    background: #1c2128; border-radius: 10px; padding: 10px 14px; margin-bottom: 8px;
    border-left: 4px solid #d29922;
}
.pending-card.buy-card  { border-left-color: #3fb950; }
.pending-card.sell-card { border-left-color: #388bfd; }
.holding-pnl-pos { color: #3fb950; font-size: 1rem; font-weight: 700; }
.holding-pnl-neg { color: #f85149; font-size: 1rem; font-weight: 700; }
.stock-badge {
    background: #21262d; border: 1px solid #388bfd; color: #79c0ff;
    border-radius: 6px; padding: 2px 8px; font-size: 0.75rem; font-weight: 600;
}
hr.vs-divider { border-color: #21262d; margin: 16px 0; }
#MainMenu, footer, header { visibility: hidden; }
.stButton > button {
    background: #21262d !important; border: 1px solid #30363d !important;
    color: #e6edf3 !important; border-radius: 8px !important;
}
.stButton > button:hover { background: #30363d !important; }
@media (max-width: 480px) { .vs-card-value { font-size: 1.1rem; } }
</style>
"""
st.markdown(DARK_CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=2)


def render_grid_heatmap(engine: GridEngine, current_price: float) -> None:
    """
    使用 st.components.v1.html() 在 iframe 内渲染热力网格，
    确保 CSS 内联生效，避免 st.markdown 输出原始 HTML 的问题。
    """
    prices = engine.grid_prices()
    cells_html = ""

    for i, price in enumerate(prices):
        level_key = str(i)
        is_occupied = level_key in engine.grid_occupied
        is_current = (
            current_price is not None
            and current_price > 0
            and abs(current_price - price) <= engine.step * 0.6
        )

        if is_occupied and is_current:
            bg = "linear-gradient(135deg,#1a4a2e,#2ea043)"
            border = "#3fb950"; text_color = "#aff5b4"; icon = "📍"
        elif is_occupied:
            bg = "linear-gradient(135deg,#1a4a2e,#2ea043)"
            border = "#3fb950"; text_color = "#aff5b4"; icon = "●"
        elif is_current:
            bg = "linear-gradient(135deg,#1a3a5c,#1f6feb)"
            border = "#388bfd"; text_color = "#cae8ff"; icon = "▼"
        else:
            bg = "#1c2128"; border = "#30363d"; text_color = "#8b949e"; icon = ""

        cells_html += (
            f'<div style="background:{bg};border:1px solid {border};color:{text_color};'
            f'border-radius:6px;padding:4px 2px;text-align:center;'
            f'font-size:0.6rem;font-weight:600;line-height:1.4;" '
            f'title="第{i+1}格 触发价:{price:.3f}">'
            f'<div style="font-size:0.7rem">{i+1}</div>'
            f'<div>{price:.2f}</div>'
            f'<div>{icon}</div></div>'
        )

    legend_html = (
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:6px;'
        'font-size:0.7rem;color:#8b949e;">'
        '<span><span style="width:10px;height:10px;border-radius:3px;background:#2ea043;'
        'display:inline-block;margin-right:4px;vertical-align:middle;"></span>已持仓</span>'
        '<span><span style="width:10px;height:10px;border-radius:3px;background:#1f6feb;'
        'display:inline-block;margin-right:4px;vertical-align:middle;"></span>当前价</span>'
        '<span><span style="width:10px;height:10px;border-radius:3px;background:#1c2128;'
        'border:1px solid #30363d;display:inline-block;margin-right:4px;vertical-align:middle;"></span>空格</span>'
        '</div>'
    )

    full_html = (
        '<html><body style="margin:0;background:#0d1117;">'
        '<div style="display:grid;grid-template-columns:repeat(10,1fr);gap:4px;padding:4px;">'
        f'{cells_html}</div>{legend_html}</body></html>'
    )
    components.html(full_html, height=130)


# ─────────────────────────────────────────────────────────────────────────────
# 待确认操作 UI
# ─────────────────────────────────────────────────────────────────────────────

def render_pending_confirmations(state: dict, engines: dict) -> bool:
    """
    渲染待确认操作列表。
    返回 True 表示本轮有按钮被点击（调用方应 st.rerun()）。
    """
    pending = state.get("pending_confirmations", [])
    if not pending:
        return False

    st.markdown(f"### 📬 待确认操作 &nbsp;`{len(pending)} 条`")
    to_remove = []
    did_action = False

    for idx, item in enumerate(pending):
        code = item.get("code", "")
        name = item.get("name", code)
        item_type = item.get("type", "buy")
        grid_level = item.get("grid_level", 0)
        grid_price = item.get("grid_price", 0.0)
        ts = item.get("timestamp", "")[:16].replace("T", " ")
        div_yield = item.get("dividend_yield", 0.0)
        current_price = item.get("current_price", 0.0)
        profit_pct = item.get("profit_pct", 0.0)

        card_class = "buy-card" if item_type == "buy" else "sell-card"
        type_label = "🟢 买入提醒" if item_type == "buy" else "🔵 止盈提醒"
        extra = f" | 预期盈利 {profit_pct * 100:.2f}%" if item_type == "sell" else ""

        col_info, col_btn, col_dismiss = st.columns([5, 1, 1])
        with col_info:
            st.markdown(
                f'<div class="pending-card {card_class}">'
                f'<b>{type_label}</b> &nbsp;'
                f'<span class="stock-badge">{code}.HK</span> {name}<br>'
                f'第 <b>{grid_level + 1}</b> 格 &nbsp;·&nbsp; '
                f'预设价格 <b>{grid_price:.3f} HKD</b> &nbsp;·&nbsp; '
                f'触发时现价 {current_price:.3f} &nbsp;·&nbsp; '
                f'股息率 {div_yield * 100:.2f}%{extra}<br>'
                f'<span style="color:#8b949e;font-size:0.72rem">{ts}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        btn_label = "✅ 确认买入" if item_type == "buy" else "✅ 确认卖出"
        with col_btn:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(btn_label, key=f"pend_{idx}_{code}_{grid_level}"):
                engine = engines.get(code)
                if engine:
                    if item_type == "buy":
                        if str(grid_level) not in engine.grid_occupied:
                            holding = engine.confirm_buy(grid_level)
                            state["positions"][code] = engine.to_state_dict()
                            st.success(
                                f"✅ {name} 第{grid_level+1}格 买入确认，"
                                f"价格 {grid_price:.3f} HKD"
                            )
                        else:
                            st.warning("该格子已有持仓，已自动忽略。")
                    else:
                        holding_id = item.get("holding_id", "")
                        engine.confirm_sell(holding_id, grid_price)
                        state["positions"][code] = engine.to_state_dict()
                        buy_price = item.get("buy_price", grid_price)
                        pct = (grid_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                        st.success(
                            f"✅ {name} 第{grid_level+1}格 卖出确认，"
                            f"卖价 {grid_price:.3f} HKD，盈利 {pct:.2f}%"
                        )
                to_remove.append(idx)
                did_action = True

        with col_dismiss:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("✖ 忽略", key=f"dismiss_{idx}_{code}_{grid_level}"):
                to_remove.append(idx)
                did_action = True

    if to_remove:
        state["pending_confirmations"] = [
            item for i, item in enumerate(pending) if i not in to_remove
        ]
        save_state(state)

    return did_action


# ─────────────────────────────────────────────────────────────────────────────
# 自动校准
# ─────────────────────────────────────────────────────────────────────────────

def render_auto_align(
    engine: GridEngine,
    code: str,
    name: str,
    current_price: float,
    state: dict,
) -> bool:
    """
    一键对齐：将所有"触发价 >= 现价"的空格子标记为已买入（使用预设价格）。
    返回 True 表示已执行（调用方应 st.rerun()）。
    """
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

    price_range = f"{prices[to_fill[0]]:.3f} ~ {prices[to_fill[-1]]:.3f}"
    st.warning(
        f"⚠️ 检测到 **{len(to_fill)}** 个格子（触发价 {price_range} HKD）"
        f"高于现价 **{current_price:.3f}** HKD，尚未记录持仓。"
    )
    details = "  ".join(f"`第{i+1}格 {prices[i]:.3f}`" for i in to_fill)
    st.markdown(f"将标记为已买入：{details}")

    if st.button(
        f"🎯 确认一键对齐 [{code}]（{len(to_fill)} 格）",
        key=f"align_confirm_{code}",
    ):
        for i in to_fill:
            engine.confirm_buy(i)
        state["positions"][code] = engine.to_state_dict()
        save_state(state)
        st.success(f"✅ 已对齐 {len(to_fill)} 个格子！")
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

    # ── 顶部标题栏 ────────────────────────────────────────────────────────────
    col_title, col_refresh = st.columns([5, 1])
    with col_title:
        st.markdown(
            '<div style="font-size:1.4rem;font-weight:800;color:#f0f6fc;">'
            '🛡️ ValueShield '
            '<span style="color:#8b949e;font-size:0.95rem;font-weight:400;">'
            'H股价值投资助手</span></div>',
            unsafe_allow_html=True,
        )
    with col_refresh:
        if st.button("🔄 刷新"):
            st.rerun()

    last_updated = state.get("last_updated", "—")
    st.markdown(
        f'<div style="color:#8b949e;font-size:0.72rem">最后更新：{last_updated}</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr class="vs-divider">', unsafe_allow_html=True)

    # ── 全局风险预警 ──────────────────────────────────────────────────────────
    total_risk = compute_total_risk_capital(engines)
    cash_reserve = settings["cash_reserve"]
    if check_cash_warning(total_risk, cash_reserve):
        st.markdown(
            f'<div class="risk-banner">⚠️ 风险预警：总风险资金需求 {total_risk:,.0f} HKD '
            f'已超过现金预留 {cash_reserve:,.0f} HKD，'
            f'超出 {total_risk - cash_reserve:,.0f} HKD！</div>',
            unsafe_allow_html=True,
        )

    # ── 汇总指标卡 ────────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    risk_cls = "warn" if check_cash_warning(total_risk, cash_reserve) else ""
    with c1:
        st.markdown(
            f'<div class="vs-card"><div class="vs-card-title">总风险资金需求</div>'
            f'<div class="vs-card-value {risk_cls}">{total_risk:,.0f} '
            f'<span style="font-size:0.9rem;color:#8b949e">HKD</span></div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        total_holdings = sum(len(e.active_holdings()) for e in engines.values())
        st.markdown(
            f'<div class="vs-card"><div class="vs-card-title">当前持仓笔数</div>'
            f'<div class="vs-card-value">{total_holdings} '
            f'<span style="font-size:0.9rem;color:#8b949e">笔</span></div></div>',
            unsafe_allow_html=True,
        )
    with c3:
        n_pending = len(state.get("pending_confirmations", []))
        badge_color = "#d29922" if n_pending > 0 else "#8b949e"
        st.markdown(
            f'<div class="vs-card"><div class="vs-card-title">待确认操作</div>'
            f'<div class="vs-card-value" style="color:{badge_color}">{n_pending} '
            f'<span style="font-size:0.9rem;color:#8b949e">条</span></div></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<hr class="vs-divider">', unsafe_allow_html=True)

    # ── 待确认操作（一键确认区）──────────────────────────────────────────────
    if render_pending_confirmations(state, engines):
        st.rerun()

    if state.get("pending_confirmations"):
        st.markdown('<hr class="vs-divider">', unsafe_allow_html=True)

    # ── 各股票详情面板 ────────────────────────────────────────────────────────
    for stock_cfg in config["stocks"]:
        if not stock_cfg.get("enabled", True):
            continue

        code = stock_cfg["code"]
        name = stock_cfg["name"]
        engine = engines[code]
        current_price = state.get("latest_prices", {}).get(code)
        annual_div = state.get("latest_dividend_ttm", {}).get(
            code, stock_cfg.get("annual_dividend_hkd", 0.0)
        )
        div_yield = compute_dividend_yield(annual_div, current_price) if current_price else 0.0

        # 股票头部
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
            f'<span class="stock-badge">{code}.HK</span>'
            f'<span style="font-size:1.15rem;font-weight:700;color:#f0f6fc;">{name}</span>'
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

        # 手动刷新行情
        ref_col, _ = st.columns([1, 4])
        with ref_col:
            if st.button(f"📡 刷新行情 [{code}]", key=f"fetch_{code}"):
                with st.spinner(f"正在获取 {name} 最新价格..."):
                    new_price = fetch_realtime_price(stock_cfg["akshare_code"])
                if new_price:
                    state.setdefault("latest_prices", {})[code] = new_price
                    save_state(state)
                    st.success(f"✅ {name} 最新价：{new_price:.3f} HKD")
                    st.rerun()
                else:
                    st.error("❌ 行情获取失败（网络/代理问题），请查看后台日志。")

        # 热力网格（st.components.v1.html 渲染，避免 raw HTML）
        st.markdown("**📊 网格热力图**")
        render_grid_heatmap(engine, current_price or 0)

        # 持仓列表
        active = engine.active_holdings()
        if active:
            st.markdown(f"**📋 当前持仓 ({len(active)} 笔)**")
            for holding in active:
                cp = current_price or holding.buy_price
                pnl_pct = holding.profit_pct_if_sold_at(cp) * 100
                pnl_val = holding.profit_if_sold_at(cp)
                pnl_cls = "holding-pnl-pos" if pnl_pct >= 0 else "holding-pnl-neg"
                tp_price = holding.take_profit_price

                h1, h2, h3, h4 = st.columns([2, 2, 2, 2])
                with h1:
                    st.markdown(
                        f'<div style="color:#8b949e;font-size:0.7rem">ID: {holding.holding_id}</div>'
                        f'<div>第 <b>{holding.grid_level+1}</b> 格</div>'
                        f'<div style="color:#8b949e;font-size:0.8rem">买入: {holding.buy_price:.3f}</div>',
                        unsafe_allow_html=True,
                    )
                with h2:
                    st.markdown(
                        f'<div class="{pnl_cls}">{pnl_pct:+.2f}%</div>'
                        f'<div style="color:#8b949e;font-size:0.8rem">{pnl_val:+.1f} HKD</div>'
                        f'<div style="color:#8b949e;font-size:0.75rem">止盈价: {tp_price:.3f}</div>',
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
                    # 直接用止盈价确认，无需手动输入
                    if st.button(
                        f"✅ 确认卖出 ({tp_price:.3f})",
                        key=f"sell_{holding.holding_id}",
                    ):
                        engine.confirm_sell(holding.holding_id, tp_price)
                        state["positions"][code] = engine.to_state_dict()
                        save_state(state)
                        st.success(
                            f"✅ 已卖出 {name} 第{holding.grid_level+1}格，"
                            f"卖价 {tp_price:.3f} HKD，盈利 {pnl_pct:.2f}%"
                        )
                        st.rerun()

        # 折叠操作面板
        with st.expander("⚙️ 网格参数设置", expanded=False):
            op1, op2, op3 = st.columns(3)
            with op1:
                new_base = st.number_input(
                    "Base_Price",
                    value=float(engine.base_price),
                    min_value=0.01, step=0.01,
                    key=f"base_{code}",
                )
            with op2:
                new_step = st.number_input(
                    "手动 Step (0=自动)",
                    value=float(engine._step or 0),
                    min_value=0.0, step=0.001, format="%.4f",
                    key=f"step_{code}",
                )
            with op3:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button(f"✅ 应用 [{code}]", key=f"apply_{code}"):
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
                    st.success(f"参数已更新！Base={new_base:.4f} Step={engine.step:.4f}")
                    st.rerun()

        with st.expander("🎯 自动校准 — 一键根据现价对齐格子", expanded=False):
            if render_auto_align(engine, code, name, current_price or 0, state):
                st.rerun()

        with st.expander("🟢 手动补录买入", expanded=False):
            grid_prices_list = engine.grid_prices()
            buy_options = {
                f"第 {i+1} 格  触发价 {p:.3f} HKD": i
                for i, p in enumerate(grid_prices_list)
                if str(i) not in engine.grid_occupied
            }
            if buy_options:
                selected_buy = st.selectbox(
                    "选择格子", list(buy_options.keys()),
                    key=f"buy_sel_{code}",
                )
                if st.button(f"✅ 确认补录买入 [{code}]", key=f"confirm_buy_{code}"):
                    level_idx = buy_options[selected_buy]
                    engine.confirm_buy(level_idx)
                    state["positions"][code] = engine.to_state_dict()
                    save_state(state)
                    st.success(
                        f"✅ 已补录 {name} 第{level_idx+1}格，"
                        f"价格 {grid_prices_list[level_idx]:.3f} HKD"
                    )
                    st.rerun()
            else:
                st.info("所有格子均已占用。")

        st.markdown('<hr class="vs-divider">', unsafe_allow_html=True)

    # ── 全局设置 ──────────────────────────────────────────────────────────────
    with st.expander("🔧 全局设置", expanded=False):
        g1, g2 = st.columns(2)
        with g1:
            new_cash = st.number_input(
                "现金预留 (HKD)", value=float(settings["cash_reserve"]),
                min_value=0.0, step=10000.0, key="g_cash",
            )
            new_bark = st.text_input(
                "Bark Token", value=settings.get("bark_token", ""),
                type="password", key="g_bark",
            )
        with g2:
            new_poll = st.number_input(
                "轮询间隔 (秒)", value=int(settings["poll_interval_seconds"]),
                min_value=5, max_value=300, step=5, key="g_poll",
            )
            new_url = st.text_input(
                "Web 服务地址",
                value=settings.get("web_server_url", "http://localhost:8501"),
                key="g_url",
            )
        if st.button("💾 保存全局设置"):
            config["settings"].update({
                "cash_reserve": new_cash, "bark_token": new_bark,
                "poll_interval_seconds": new_poll, "web_server_url": new_url,
            })
            save_config(config)
            st.success("全局设置已保存！")
            st.rerun()

    st.markdown(
        '<div style="text-align:center;color:#484f58;font-size:0.68rem;padding-top:16px;">'
        'ValueShield v1.1 · 算法为辅，主观为主 · 数据本地化 · 移动端优先'
        '</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
