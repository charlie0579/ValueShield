"""
monitor.py - 主监控循环
负责交易时段判断、轮询行情、触发网格信号、推送通知、持久化状态。
可作为独立后台进程运行（python monitor.py），也可在 Streamlit 侧边栏中触发。
"""

import json
import logging
import os
import time
from datetime import datetime, date

import chinese_calendar as cc

from crawler import (
    fetch_realtime_price,
    fetch_dividend_ttm,
    compute_dividend_yield,
    compute_percentile,
)
from engine import (
    GridEngine,
    PositionSummary,
    WatcherTarget,
    compute_total_risk_capital,
    compute_total_risk_capital_v2,
    check_cash_warning,
)
from notifier import BarkNotifier

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")

TRADE_SESSIONS = [
    ("09:15", "12:00"),
    ("13:00", "16:10"),
]


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as fp:
        return json.load(fp)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {
            "positions": {},
            "latest_prices": {},
            "latest_dividend_ttm": {},
            "valuation_history": {},   # {code: {"pb": [...], "div_yield": [...]}}
            "watcher_prices": {},      # 观察标的最新价格
            "alerts": [],
        }
    with open(STATE_PATH, encoding="utf-8") as fp:
        return json.load(fp)


def save_state(state: dict) -> None:
    """原子写入，防止掉电丢失状态。"""
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_PATH)
    logger.debug("state.json 已保存")


def is_trading_day(today: date) -> bool:
    """判断是否为中国 A/H 股交易日（排除法定节假日与周末）。"""
    if today.weekday() >= 5:
        return False
    if cc.is_holiday(today):
        return False
    return True


def is_in_trading_session(now: datetime) -> bool:
    """判断当前时间是否在交易时段内。"""
    current_str = now.strftime("%H:%M")
    for start, end in TRADE_SESSIONS:
        if start <= current_str <= end:
            return True
    return False


def build_engines(config: dict, state: dict) -> dict[str, GridEngine]:
    """根据配置和持久化状态初始化所有网格引擎（含 v2.4 总仓摘要）。"""
    engines: dict[str, GridEngine] = {}
    settings = config["settings"]
    for stock in config["stocks"]:
        if not stock.get("enabled", True):
            continue
        code = stock["code"]
        engine = GridEngine(
            code=code,
            name=stock["name"],
            base_price=stock["base_price"],
            hist_min=stock["hist_min"],
            lot_size=stock["lot_size"],
            grid_levels=settings["grid_levels"],
            step=stock.get("step"),
            take_profit_pct=stock.get("take_profit_pct", settings["default_take_profit_pct"]),
        )
        pos = state.get("positions", {}).get(code, {})
        engine.sync_state(pos)
        # 若配置中指定了 total_budget，且 state 中尚无 position_summary ，则用配置初始化
        if engine.position_summary is None and stock.get("total_budget", 0) > 0:
            engine.position_summary = PositionSummary(
                total_budget=float(stock["total_budget"]),
            )
        engines[code] = engine
    return engines


def build_watchers(config: dict) -> list[WatcherTarget]:
    """从配置构建观察者标的列表。"""
    return [
        WatcherTarget.from_dict(w)
        for w in config.get("watchers", [])
        if w.get("enabled", True)
    ]


def refresh_valuation_history(config: dict, state: dict) -> dict:
    """
    刷新历史估值数据（PB / 股息率序列）并写入 state。
    供 UI 层手动触发（‘刷新估值数据’按鈕），不在主监控循环中调用。
    """
    from crawler import fetch_div_yield_history, fetch_pb_history
    current_prices = state.get("latest_prices", {})
    for stock in config.get("stocks", []):
        if not stock.get("enabled", True):
            continue
        code = stock["code"]
        akcode = stock["akshare_code"]
        price = current_prices.get(code, 0.0)
        if price <= 0:
            continue
        val_hist = state.setdefault("valuation_history", {}).setdefault(code, {})
        dy_hist = fetch_div_yield_history(akcode, price)
        if dy_hist:
            val_hist["div_yield"] = dy_hist
        pb_hist = fetch_pb_history(akcode)
        if pb_hist:
            val_hist["pb"] = pb_hist
        # v2.6.1 ROE 历史
        from crawler import fetch_roe_history
        roe_hist = fetch_roe_history(akcode)
        if roe_hist:
            val_hist["roe"] = roe_hist
        logger.info("[%s] 历史估值数据已刷新: DY %d 条，PB %d 条，ROE %d 条", code, len(dy_hist), len(pb_hist), len(roe_hist))
    return state



def _add_pending(state: dict, entry: dict) -> None:
    """向 state 中添加一条待确认记录（去重：同 code+type+grid_level）。"""
    pending = state.setdefault("pending_confirmations", [])
    for existing in pending:
        if (
            existing.get("code") == entry.get("code")
            and existing.get("type") == entry.get("type")
            and existing.get("grid_level") == entry.get("grid_level")
            and existing.get("holding_id", "") == entry.get("holding_id", "")
        ):
            existing.update(entry)
            return
    pending.append(entry)


def run_once(config: dict, state: dict, engines: dict[str, GridEngine], notifier: BarkNotifier) -> dict:
    """
    单次轮询：获取行情 → v2.4 检测信号 → 推送通知 → 更新状态。
    返回更新后的 state。
    """
    settings = config["settings"]
    cash_reserve = settings["cash_reserve"]
    current_prices: dict[str, float] = {}

    for stock in config["stocks"]:
        if not stock.get("enabled", True):
            continue
        code = stock["code"]
        akcode = stock["akshare_code"]
        engine = engines[code]

        price = fetch_realtime_price(akcode)
        if price is None:
            logger.warning("[%s] 获取价格失败，跳过本轮", code)
            continue
        current_prices[code] = price

        annual_div = state.get("latest_dividend_ttm", {}).get(code)
        if annual_div is None:
            annual_div = fetch_dividend_ttm(akcode)
            if annual_div == 0.0:
                annual_div = stock.get("annual_dividend_hkd", 0.0)
            state.setdefault("latest_dividend_ttm", {})[code] = annual_div

        div_yield = compute_dividend_yield(annual_div, price)
        state.setdefault("latest_prices", {})[code] = price

        # ── 估值分位（仅读 state 缓存，不实时抓取；历史数据由 refresh_valuation_history 填充）
        val_hist = state.get("valuation_history", {}).get(code, {})
        dy_hist = val_hist.get("div_yield", [])
        # PB 分位需实时 PB 值，监控层无此数据，固定 -1 跳过熔断
        pb_pct = -1.0
        dy_pct = compute_percentile(div_yield, dy_hist, higher_is_better=True)

        # ── v2.4 买入信号（PB > 80% 分位时熔断）
        buy_levels = engine.check_buy_signal_v2(price, pb_percentile=pb_pct)
        for level in buy_levels:
            grid_prices = engine.grid_prices()
            notifier.notify_buy(
                code=code,
                name=stock["name"],
                current_price=price,
                dividend_yield=div_yield,
                grid_level=level,
                grid_price=grid_prices[level],
            )
            _add_pending(state, {
                "type": "buy",
                "code": code,
                "name": stock["name"],
                "grid_level": level,
                "grid_price": round(grid_prices[level], 4),
                "current_price": price,
                "dividend_yield": round(div_yield, 4),
                "timestamp": datetime.now().isoformat(),
                "holding_id": "",
            })
            logger.info("[%s] 推送买入信号 第%d格 价格=%.4f", code, level + 1, grid_prices[level])

        # ── v2.4 卖出信号（band_shares==0 屏蔽；min_holding_limit 底仓保护 v1 兼容；股息率>80% 钝化）
        sell_holdings = engine.check_sell_signals_v2(
            price,
            dy_percentile=dy_pct,
            min_holding_limit=int(settings.get("min_holding_limit", 0)),
        )
        for holding in sell_holdings:
            notifier.notify_sell(
                code=code,
                name=stock["name"],
                current_price=price,
                dividend_yield=div_yield,
                grid_level=holding.grid_level,
                buy_price=holding.buy_price,
                profit_pct=holding.profit_pct_if_sold_at(price),
                holding_id=holding.holding_id,
            )
            _add_pending(state, {
                "type": "sell",
                "code": code,
                "name": stock["name"],
                "grid_level": holding.grid_level,
                "grid_price": round(holding.take_profit_price, 4),
                "current_price": price,
                "dividend_yield": round(div_yield, 4),
                "timestamp": datetime.now().isoformat(),
                "holding_id": holding.holding_id,
                "buy_price": holding.buy_price,
                "profit_pct": round(holding.profit_pct_if_sold_at(price), 4),
            })
            logger.info("[%s] 推送止盈信号 holding_id=%s", code, holding.holding_id)

        state.setdefault("positions", {})[code] = engine.to_state_dict()

    # ── 观察者模式检查
    for watcher in build_watchers(config):
        w_price = fetch_realtime_price(watcher.akshare_code)
        if w_price is not None:
            state.setdefault("watcher_prices", {})[watcher.code] = w_price
            if watcher.is_opportunity(w_price):
                notifier.notify_watcher(
                    code=watcher.code,
                    name=watcher.name,
                    current_price=w_price,
                    base_price=watcher.base_price,
                )
                logger.info("[%s] 观察者建仓机会：现价=%.3f <= 建仓价=%.3f", watcher.code, w_price, watcher.base_price)

    # ── v2.4 风险预警（按预算公式）
    total_risk = compute_total_risk_capital_v2(engines, current_prices)
    if total_risk == 0.0:  # 如果无预算数据，退化为旧网格方式
        total_risk = compute_total_risk_capital(engines)
    if check_cash_warning(total_risk, cash_reserve):
        risk_details = [
            {
                "code": s["code"],
                "name": s["name"],
                "risk": engines[s["code"]].compute_risk_capital_v2(
                    current_prices.get(s["code"], 0.0)
                ),
            }
            for s in config["stocks"]
            if s.get("enabled", True) and s["code"] in engines
        ]
        notifier.notify_risk_warning(total_risk, cash_reserve, risk_details)
        state.setdefault("alerts", []).append({
            "time": datetime.now().isoformat(),
            "type": "cash_warning",
            "total_risk": total_risk,
        })

    state["last_updated"] = datetime.now().isoformat()
    return state


def maybe_refresh_magic_formula(config: dict) -> None:
    """
    检查神奇公式缓存是否过期，若过期则在盘前自动触发一次全市场扫描。
    由 main_loop 在每次进入交易日时调用一次。
    失败时静默忽略，不影响主监控循环。
    """
    try:
        from magic_formula import load_cache, is_cache_fresh, scan_magic_formula

        cache = load_cache()
        if cache is not None and is_cache_fresh(cache):
            logger.info("神奇公式缓存有效，跳过盘前扫描")
            return
        logger.info("神奇公式缓存过期或不存在，开始盘前自动扫描…")
        scan_magic_formula(top_n=30, include_h=True)
        logger.info("神奇公式盘前扫描完成")
    except Exception as exc:
        logger.warning("神奇公式盘前扫描失败（不影响主循环）: %s", exc)


def main_loop() -> None:
    """主监控循环入口，供 `python monitor.py` 直接运行。"""
    config = load_config()
    settings = config["settings"]
    poll_interval = settings["poll_interval_seconds"]
    notifier = BarkNotifier(
        bark_url=settings["bark_api_url"],
        bark_token=settings["bark_token"],
        web_server_url=settings["web_server_url"],
    )

    logger.info("ValueShield 监控服务已启动，轮询间隔 %ds", poll_interval)

    while True:
        now = datetime.now()
        today = now.date()

        if not is_trading_day(today):
            logger.info("非交易日，静默等待 300 秒...")
            time.sleep(300)
            continue

        if not is_in_trading_session(now):
            logger.info("非交易时段（%s），等待下一个时段...", now.strftime("%H:%M"))
            time.sleep(60)
            continue

        maybe_refresh_magic_formula(config)
        state = load_state()
        engines = build_engines(config, state)

        try:
            state = run_once(config, state, engines, notifier)
        except Exception as exc:
            logger.error("本轮轮询异常: %s", exc)
        finally:
            save_state(state)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main_loop()
