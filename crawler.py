"""
crawler.py - 数据获取模块
负责从 AkShare 获取港股实时行情与分红数据。
智能三通道：状态记忆 + 快熔断（5s）+ 自动降级 + 20% 偏差校验 + 随机 UA + 日志脱水。

通道优先级（可动态切换）：
  akshare  → 东方财富 stock_hk_spot_em
  sina     → 新浪财经 hq.sinajs.cn/list=hkXXXXX（现代格式，字段9=当前价）
  em_web   → 东方财富 Web 备用 push2.eastmoney.com JSON 接口
"""

import logging
import random
import re
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# 随机 User-Agent 池，防止代理因固定 UA 拒绝连接
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
]

_FAST_TIMEOUT = 5        # 秒：快熔断超时
_DRIFT_THRESHOLD = 0.20  # 20% 偏差阈值，超过则告警并尝试第三通道

# 模块级状态：记忆上次成功通道 + 上次成功价格（用于偏差校验）
_preferred_channel: str = "akshare"
_last_known_prices: dict[str, float] = {}  # code → 上次成功价格


def _random_headers(extra: Optional[dict] = None) -> dict:
    """生成含随机 UA、Connection:close、Cache-Control:no-cache 的请求头。"""
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Connection": "close",
        "Cache-Control": "no-cache",   # 防止代理返回缓存旧数据
        "Pragma": "no-cache",
    }
    if extra:
        headers.update(extra)
    return headers


def _try_akshare(akshare_code: str) -> float:
    """通道 A：东方财富 AkShare stock_hk_spot_em，单次，超时 5s。"""
    symbol = akshare_code.lstrip("0") or "0"
    df: pd.DataFrame = ak.stock_hk_spot_em()
    if df is None or df.empty:
        raise ValueError("stock_hk_spot_em 返回空数据")
    row = df[df["代码"] == akshare_code]
    if row.empty:
        row = df[df["代码"] == symbol]
    if row.empty:
        raise ValueError(f"未找到 {akshare_code}（也尝试了 {symbol}）")
    return float(row.iloc[0]["最新价"])


def _try_sina(akshare_code: str) -> float:
    """
    通道 B：新浪财经现代接口 hq.sinajs.cn/list=hkXXXXX。
    格式：var hq_str_hk01336="名称,XX,现价,昨收,今开,最高,最低,...";
    字段索引：[2]=现价（非 rt_hk 的旧格式）。
    """
    # 新浪现代港股格式：hkXXXXX（不补零，直接用原始代码）
    symbol_key = f"hk{akshare_code}"
    url = f"https://hq.sinajs.cn/list={symbol_key}"
    headers = _random_headers({"Referer": "https://finance.sina.com.cn"})
    resp = requests.get(url, headers=headers, timeout=_FAST_TIMEOUT)
    resp.raise_for_status()
    match = re.search(r'"([^"]*)"', resp.text)
    if not match:
        raise ValueError(f"Sina 返回格式异常: {resp.text[:120]}")
    fields = match.group(1).split(",")
    # 字段[2] = 当前价；字段[1] = 昨收（可作为验证）
    if len(fields) < 3 or not fields[2].strip():
        raise ValueError(f"Sina 字段不足或价格为空: {fields[:6]}")
    price = float(fields[2].strip())
    if price <= 0:
        raise ValueError(f"Sina 返回价格无效（可能非交易时段）: {price}")
    return price


def _try_em_web(akshare_code: str) -> float:
    """
    通道 C：东方财富 Web 备用 push2.eastmoney.com JSON 接口。
    仅在前两通道均偏差过大或失败时启用。
    """
    # 东方财富港股代码格式：116.XXXXX（116 = 港股市场代码）
    em_code = f"116.{akshare_code.zfill(5)}"
    url = (
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={em_code}&fields=f43,f57,f58&ut=fa5fd1943c7b386f172d6893dbfba10b"
    )
    headers = _random_headers({"Referer": "https://quote.eastmoney.com"})
    resp = requests.get(url, headers=headers, timeout=_FAST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    # f43 = 最新价（单位：分，需 ÷ 1000；港股有时是原始值，按实际调整）
    raw = data.get("data", {}).get("f43")
    if not raw or raw <= 0:
        raise ValueError(f"EM Web 返回无效价格: {raw}")
    # 东方财富港股价格精度：÷ 1000 得 HKD
    price = raw / 1000.0
    if price <= 0:
        raise ValueError(f"EM Web 价格换算异常: {price}")
    return price


def _check_drift(akshare_code: str, price: float, channel: str) -> bool:
    """
    检查价格与上次成功价是否偏差超过阈值。
    超过则记录 WARNING 并返回 True（触发第三通道校验）。
    """
    last = _last_known_prices.get(akshare_code)
    if last and last > 0:
        drift = abs(price - last) / last
        if drift > _DRIFT_THRESHOLD:
            logger.warning(
                "[%s] ⚠️ 价格漂移过大！通道=%s 新价=%.4f 上次=%.4f 偏差=%.1f%% "
                "（阈值 %.0f%%），将尝试第三通道交叉验证。",
                akshare_code, channel, price, last, drift * 100, _DRIFT_THRESHOLD * 100,
            )
            return True
    return False


def fetch_realtime_price(akshare_code: str) -> Optional[float]:
    """
    获取港股实时最新价（港元计价）。

    流程：
    1. 优先调用 _preferred_channel（AkShare 或 Sina）
    2. 若失败，切换到另一通道，并记忆新通道
    3. 若成功价格与上次偏差 > 20%，启动第三通道（EM Web）交叉验证：
       - 三通道价格两两接近 → 采用 EM Web 价格，视为数据源异常纠偏
       - 无法验证 → 保留当前价并记录 WARNING
    """
    global _preferred_channel, _last_known_prices

    primary = _preferred_channel
    secondary = "sina" if primary == "akshare" else "akshare"
    channel_funcs = {"akshare": _try_akshare, "sina": _try_sina, "em_web": _try_em_web}

    price: Optional[float] = None
    used_channel: Optional[str] = None

    # ── 主通道 & 备用通道
    for channel in [primary, secondary]:
        try:
            price = channel_funcs[channel](akshare_code)
            used_channel = channel
            break
        except Exception as exc:
            logger.warning("[%s] 通道 %s 失败: %s", akshare_code, channel, exc)

    if price is None:
        logger.error("[%s] 主备通道均不可用，本次跳过。", akshare_code)
        return None

    # ── 更新通道记忆（发生切换时打印）
    if used_channel != primary:
        logger.warning(
            "[%s] 通道切换：%s → %s（%.4f HKD）",
            akshare_code, primary, used_channel, price,
        )
        _preferred_channel = used_channel

    # ── 20% 偏差校验：触发第三通道 EM Web 交叉验证
    if _check_drift(akshare_code, price, used_channel):
        try:
            em_price = _try_em_web(akshare_code)
            last = _last_known_prices.get(akshare_code, price)
            em_drift = abs(em_price - last) / last if last > 0 else 1.0
            if em_drift <= _DRIFT_THRESHOLD:
                logger.warning(
                    "[%s] 第三通道纠偏：采用 EM Web %.4f HKD（原值 %.4f 已丢弃）",
                    akshare_code, em_price, price,
                )
                price = em_price
            else:
                logger.warning(
                    "[%s] 三通道均出现大偏差，保留当前值 %.4f HKD，请人工核查。",
                    akshare_code, price,
                )
        except Exception as em_exc:
            logger.warning("[%s] EM Web 第三通道失败: %s，保留当前值。", akshare_code, em_exc)

    # ── 记录本次价格供下次偏差校验
    _last_known_prices[akshare_code] = price
    return price


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
        return _fetch()
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
        return _fetch()
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
