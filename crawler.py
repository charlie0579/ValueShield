"""
crawler.py - 数据获取模块
负责从 AkShare 获取港股实时行情与分红数据，含自动重试机制。
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def _retry(func, *args, retries: int = MAX_RETRIES, delay: float = RETRY_DELAY_SECONDS, **kwargs):
    """通用重试装饰器逻辑，捕获网络异常并自动重试。"""
    last_exception = None
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exception = exc
            logger.warning(
                "第 %d 次调用 %s 失败: %s，%d 秒后重试...",
                attempt, func.__name__, exc, delay
            )
            if attempt < retries:
                time.sleep(delay)
    raise RuntimeError(
        f"调用 {func.__name__} 在 {retries} 次重试后仍失败: {last_exception}"
    ) from last_exception


def fetch_realtime_price(akshare_code: str) -> Optional[float]:
    """
    获取港股实时最新价（收盘价或最新行情价）。
    akshare_code: 如 '01336'、'00525'
    返回 float 价格，失败返回 None。
    """
    def _fetch():
        symbol = akshare_code.lstrip("0") or "0"
        logger.info("[%s] 正在调用 stock_hk_spot_em() 获取实时行情...", akshare_code)
        df: pd.DataFrame = ak.stock_hk_spot_em()
        if df is None or df.empty:
            raise ValueError("stock_hk_spot_em 返回空数据")
        logger.info("[%s] 接口返回 %d 条数据，列名: %s", akshare_code, len(df), df.columns.tolist())
        row = df[df["代码"] == akshare_code]
        if row.empty:
            row = df[df["代码"] == symbol]
        if row.empty:
            all_codes = df["代码"].tolist()[:10]
            raise ValueError(
                f"未找到股票代码 {akshare_code}（也尝试了 {symbol}），"
                f"接口返回代码样本: {all_codes}"
            )
        price = float(row.iloc[0]["最新价"])
        logger.info("[%s] 获取到最新价: %.4f HKD", akshare_code, price)
        return price

    try:
        return _retry(_fetch)
    except Exception as exc:
        logger.error(
            "[%s] 获取实时价格失败（已重试 %d 次）: %s",
            akshare_code, MAX_RETRIES, exc,
        )
        return None


def fetch_dividend_ttm(akshare_code: str, years: int = 1) -> float:
    """
    获取港股近 12 个月（TTM）每股分红总额（港元）。
    通过 AkShare stock_hk_dividend_payout_em 接口查询，累加近 1 年内的每股股息。
    分红方案文本格式：'每股派人民币X.XX元(相当于港币Y.YY元)'，提取港元金额。
    失败时返回 0.0。
    """
    import re

    def _fetch():
        df: pd.DataFrame = ak.stock_hk_dividend_payout_em(symbol=akshare_code)
        if df is None or df.empty:
            return 0.0
        # 日期列：除净日（格式 YYYY-MM-DD）
        date_col = "除净日"
        plan_col = "分红方案"
        if date_col not in df.columns or plan_col not in df.columns:
            logger.warning("分红数据列名识别失败，列名: %s", df.columns.tolist())
            return 0.0
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        cutoff = datetime.now() - timedelta(days=365 * years)
        recent = df[df[date_col] >= cutoff]
        total_div = 0.0
        hkd_pattern = re.compile(r"港币(\d+\.?\d*)元")
        for plan in recent[plan_col].dropna():
            match = hkd_pattern.search(str(plan))
            if match:
                total_div += float(match.group(1))
        return total_div

    try:
        return _retry(_fetch)
    except Exception as exc:
        logger.error("获取 %s 分红数据失败: %s", akshare_code, exc)
        return 0.0


def fetch_stock_name(akshare_code: str) -> str:
    """
    获取港股名称，失败时返回股票代码本身。
    """
    def _fetch():
        df: pd.DataFrame = ak.stock_hk_spot_em()
        row = df[df["代码"] == akshare_code]
        if row.empty:
            return akshare_code
        return str(row.iloc[0]["名称"])

    try:
        return _retry(_fetch)
    except Exception as exc:
        logger.warning("获取 %s 股票名称失败: %s，使用代码代替", akshare_code, exc)
        return akshare_code


def compute_dividend_yield(annual_dividend_hkd: float, current_price: float) -> float:
    """
    计算动态股息率 (TTM)。
    annual_dividend_hkd: 近 12 个月每股分红（港元）
    current_price: 最新股价（港元）
    返回股息率（0~1 小数，如 0.068 表示 6.8%）。
    """
    if current_price <= 0:
        return 0.0
    return annual_dividend_hkd / current_price
