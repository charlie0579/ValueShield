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
_HARD_BLOCK_ON_DIVERGE = True  # 三通道均发散时，硬拒绝写入 state（返回 None）

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
                if _HARD_BLOCK_ON_DIVERGE:
                    logger.error(
                        "[%s] ⛔ 价格硬拦截：三通道均发散，本次拒绝写入 state "
                        "（主通道=%.4f EM=%.4f 上次=%.4f），请人工核查。",
                        akshare_code, price, em_price,
                        _last_known_prices.get(akshare_code, 0),
                    )
                    return None  # 硬拒绝：不写 state，不更新 _last_known_prices
                logger.warning(
                    "[%s] 三通道均出现大偏差，保留当前値 %.4f HKD，请人工核查。",
                    akshare_code, price,
                )
        except Exception as em_exc:
            logger.warning("[%s] EM Web 第三通道失败: %s，保留当前値。", akshare_code, em_exc)

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

# ─────────────────────────────────────────────────────────────────────────────
# v2.4 估值锁点插件：历史 PB + 股息率分位计算
# ─────────────────────────────────────────────────────────────────────────────


def fetch_pb_history(akshare_code: str) -> list[float]:
    """
    获取近5-10年历史 PB（市净率）序列，用于估值分位计算。
    通过百度股市通接口获取，失败时返回空列表。
    """
    try:
        df = ak.stock_hk_valuation_baidu(symbol=akshare_code, indicator="市净率")
        if df is None or df.empty:
            return []
        val_col = df.columns[1]
        return [float(v) for v in df[val_col].dropna() if float(v) > 0]
    except Exception as exc:
        logger.warning("[%s] 获取历史PB失败: %s", akshare_code, exc)
        return []


def fetch_roe_history(akshare_code: str, years: int = 10) -> list[float]:
    """
    获取标的近 N 年的 ROE（净资产收益率）历史数据。
    A 股使用 AkShare 财务指标接口；H 股无直接接口时返回空列表。
    返回：最近 years 年的 ROE 列表（小数，如 0.15 = 15%），升序排列（旧→新）。
    """
    try:
        # 优先尝试 A 股财务指标接口
        df = ak.stock_financial_analysis_indicator(symbol=akshare_code, start_year=str(datetime.now().year - years))
        if df is None or df.empty:
            return []
        # 列名适配
        roe_col = next((c for c in df.columns if "净资产收益率" in str(c) or "ROE" in str(c).upper()), None)
        date_col = next((c for c in df.columns if "日期" in str(c) or "报告期" in str(c)), None)
        if roe_col is None:
            return []
        roes: list[float] = []
        for _, row in df.iterrows():
            val = _safe_parse_pct(row.get(roe_col))
            if val is not None and val != 0.0:
                roes.append(val)
        # 去重并返回最近 years 个
        return roes[-years:] if roes else []
    except Exception as exc:
        logger.debug("[%s] fetch_roe_history 失败: %s", akshare_code, exc)
        return []


def _safe_parse_pct(val) -> Optional[float]:
    """将百分比字符串或数值解析为小数。如 '15.3%' → 0.153；0.153 → 0.153。"""
    if val is None:
        return None
    try:
        s = str(val).strip().replace("%", "")
        f = float(s)
        # 若绝对值 > 2，认为是百分比形式（如 15.3），转换为小数
        return f / 100.0 if abs(f) > 2 else f
    except (ValueError, TypeError):
        return None


def compute_roe_stability(
    roe_history: list[float],
    decline_threshold: float = 0.20,
    consecutive_years: int = 3,
) -> dict:
    """
    分析 ROE 历史序列的稳定性。

    Args:
        roe_history: ROE 序列（升序，旧→新），小数形式。
        decline_threshold: 最新值相比历史均值下滑多少触发预警（默认 20%）。
        consecutive_years: 连续下滑多少年触发预警（默认 3）。

    Returns:
        dict with keys:
            mean_roe         - 历史均值（float or None）
            latest_roe       - 最新值（float or None）
            is_declining     - 是否触发预警（bool）
            decline_reason   - 预警原因字符串（"" = 无预警）
            data_sufficient  - 数据是否充足（bool，< 3 年时为 False）
    """
    if len(roe_history) < 3:
        return {
            "mean_roe": None,
            "latest_roe": roe_history[-1] if roe_history else None,
            "is_declining": False,
            "decline_reason": "",
            "data_sufficient": False,
        }

    mean_roe = sum(roe_history) / len(roe_history)
    latest_roe = roe_history[-1]
    reasons: list[str] = []

    # 预警条件 1：最新 ROE 相比均值下滑超过阈值
    if mean_roe > 0 and latest_roe < mean_roe * (1 - decline_threshold):
        reasons.append(f"最新ROE {latest_roe:.1%} 低于均值 {mean_roe:.1%} 超过 {decline_threshold:.0%}")

    # 预警条件 2：连续 N 年下滑
    if len(roe_history) >= consecutive_years:
        tail = roe_history[-consecutive_years:]
        if all(tail[i] > tail[i + 1] for i in range(len(tail) - 1)):
            reasons.append(f"ROE 已连续 {consecutive_years} 年下滑")

    return {
        "mean_roe": mean_roe,
        "latest_roe": latest_roe,
        "is_declining": bool(reasons),
        "decline_reason": "；".join(reasons),
        "data_sufficient": True,
    }



# ─────────────────────────────────────────────────────────────────────────────
# ROE 稳定性辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _safe_parse_pct(val) -> "float | None":
    """把百分比字符串（如 '12.34%'）或数值统一转换为小数（0.1234），失败返回 None。"""
    if val is None:
        return None
    try:
        s = str(val).strip().rstrip("%")
        return float(s) / 100.0 if "%" in str(val) else float(s)
    except (ValueError, TypeError):
        return None


def fetch_roe_history(akshare_code: str, years: int = 10) -> list[float]:
    """抓取近 N 年（年报）ROE，返回升序列表（最早 → 最新），单位为小数。

    数据源：akshare.stock_financial_analysis_indicator（东方财富）。
    若数据不足 2 条则返回空列表（调用方应做健壮性处理）。
    """
    try:
        import akshare as ak
        df = ak.stock_financial_analysis_indicator(symbol=akshare_code, start_year="2005")
        if df is None or df.empty:
            return []
        # 列名因版本差异，兼容两种命名
        roe_col = next(
            (c for c in df.columns if "净资产收益率" in c or "ROE" in c.upper()),
            None,
        )
        if roe_col is None:
            logger.warning("[%s] fetch_roe_history: 未找到 ROE 列，列名=%s", akshare_code, list(df.columns))
            return []
        # 筛选年报（报告期包含 "12-31"）
        if "报告期" in df.columns:
            df = df[df["报告期"].astype(str).str.endswith("12-31")]
        elif "REPORT_DATE" in df.columns:
            df = df[df["REPORT_DATE"].astype(str).str.endswith("12-31")]
        df = df.tail(years)
        result = [_safe_parse_pct(v) for v in df[roe_col].tolist()]
        return [v for v in result if v is not None]
    except Exception as exc:
        logger.warning("[%s] fetch_roe_history 失败: %s", akshare_code, exc)
        return []


def compute_roe_stability(
    roe_history: list[float],
    decline_threshold: float = 0.20,
    consecutive_years: int = 3,
) -> dict:
    """分析 ROE 序列的稳定性，返回结构化结果。

    Returns
    -------
    dict with keys:
        stable (bool): 无报警时为 True
        consecutive_decline (int): 连续下降年数
        max_drop (float): 相对历史峰值的最大跌幅
        alert (str): 空字符串或警告信息
    """
    if len(roe_history) < 2:
        return {"stable": True, "consecutive_decline": 0, "max_drop": 0.0, "alert": ""}

    # 连续下降年数（从最新往前数）
    consecutive = 0
    for i in range(len(roe_history) - 1, 0, -1):
        if roe_history[i] < roe_history[i - 1]:
            consecutive += 1
        else:
            break

    # 相对历史峰值的最大跌幅
    peak = max(roe_history)
    latest = roe_history[-1]
    max_drop = (peak - latest) / peak if peak > 0 else 0.0

    alerts: list[str] = []
    if consecutive >= consecutive_years:
        alerts.append(f"ROE 已连续 {consecutive} 年下滑")
    if max_drop >= decline_threshold:
        alerts.append(f"ROE 较峰值下跌 {max_drop:.0%}")

    alert_str = "⚠️ " + "；".join(alerts) if alerts else ""
    return {
        "stable": not bool(alerts),
        "consecutive_decline": consecutive,
        "max_drop": max_drop,
        "alert": alert_str,
    }


def fetch_div_yield_history(
    akshare_code: str,
    current_price: float,
    years: int = 10,
) -> list[float]:
    """
    获取近 N 年历史股息率序列（粗估：年度分红 ÷ 当前股价）。
    数据不足时返回空列表。
    """
    if current_price <= 0:
        return []
    try:
        df = ak.stock_hk_dividend_payout_em(symbol=akshare_code)
        if df is None or df.empty:
            return []
        date_col, plan_col = "除净日", "分红方案"
        if date_col not in df.columns:
            return []
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        hkd_pattern = re.compile(r"港币(\d+\.?\d*)元")
        yearly: dict[int, float] = {}
        for _, row in df.iterrows():
            dt = row[date_col]
            if pd.isna(dt):
                continue
            year = int(dt.year)
            m = hkd_pattern.search(str(row.get(plan_col, "")))
            if m:
                yearly[year] = yearly.get(year, 0.0) + float(m.group(1))
        cutoff_year = datetime.now().year - years
        return [
            div / current_price
            for year, div in yearly.items()
            if year >= cutoff_year and div > 0
        ]
    except Exception as exc:
        logger.warning("[%s] 获取历史股息率失败: %s", akshare_code, exc)
        return []


def compute_percentile(
    current: float,
    history: list[float],
    higher_is_better: bool = False,
) -> float:
    """
    计算 current 在 history 中的百分位（0-100）。数据不足（< 4 个）时返回 -1。
    higher_is_better=True（股息率）：current 越高 → 分位越高 → 越低估。
    higher_is_better=False（PB）：current 越低 → 分位越高 → 越低估。
    """
    valid = [v for v in history if v > 0]
    if len(valid) < 4:
        return -1.0
    if higher_is_better:
        count = sum(1 for v in valid if v <= current)
    else:
        count = sum(1 for v in valid if v >= current)
    return round(count / len(valid) * 100, 1)


def get_valuation_label(
    percentile: float,
    metric_name: str,
    current_val: float,
    unit: str = "%",
    years: int = 10,
) -> str:
    """生成估值分位文本标签（含 emoji 和中文描述）。"""
    if percentile < 0:
        return f"{metric_name}: {current_val:.2f}{unit} (历史数据不足)"
    if percentile >= 90:
        emoji, desc = "🚀", "极度低估"
    elif percentile >= 75:
        emoji, desc = "📈", "低估"
    elif percentile >= 50:
        emoji, desc = "⚖️", "合理"
    elif percentile >= 25:
        emoji, desc = "📉", "偏高"
    else:
        emoji, desc = "🔴", "高估"
    return (
        f"{metric_name}: {current_val:.2f}{unit} "
        f"({years}年分位: {percentile:.0f}% {emoji} {desc})"
    )
