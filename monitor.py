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

from crawler import fetch_realtime_price, fetch_dividend_ttm, compute_dividend_yield
from engine import GridEngine, compute_total_risk_capital, check_cash_warning
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
        return {"positions": {}, "latest_prices": {}, "latest_dividend_ttm": {}, "alerts": []}
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
    """根据配置和持久化状态初始化所有网格引擎。"""
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
        engines[code] = engine
    return engines


def run_once(config: dict, state: dict, engines: dict[str, GridEngine], notifier: BarkNotifier) -> dict:
    """
    单次轮询：获取行情 → 检测信号 → 推送通知 → 更新状态。
    返回更新后的 state。
    """
    settings = config["settings"]
    cash_reserve = settings["cash_reserve"]

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

        annual_div = state.get("latest_dividend_ttm", {}).get(code)
        if annual_div is None:
            annual_div = fetch_dividend_ttm(akcode)
            if annual_div == 0.0:
                annual_div = stock.get("annual_dividend_hkd", 0.0)
            state.setdefault("latest_dividend_ttm", {})[code] = annual_div

        div_yield = compute_dividend_yield(annual_div, price)
        state.setdefault("latest_prices", {})[code] = price

        buy_levels = engine.check_buy_signal(price)
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
            logger.info("[%s] 推送买入信号 第%d格 价格=%.4f", code, level + 1, grid_prices[level])

        sell_holdings = engine.check_sell_signals(price)
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
            logger.info("[%s] 推送止盈信号 holding_id=%s", code, holding.holding_id)

        state.setdefault("positions", {})[code] = engine.to_state_dict()

    total_risk = compute_total_risk_capital(engines)
    if check_cash_warning(total_risk, cash_reserve):
        risk_details = [
            {
                "code": s["code"],
                "name": s["name"],
                "risk": engines[s["code"]].compute_risk_capital(),
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
