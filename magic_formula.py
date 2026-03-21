"""
magic_formula.py — 格林布拉特"神奇公式"全市场扫描器 v2.5

覆盖范围：A 股（沪深主板/创业板/科创板）+ H 股（恒生综合成分股近似）
算法：
  ROC = EBIT / (净营运资本 + 净固定资产)
  EY  = EBIT / EV (EV = 市值 + 净负债)
  综合排名 = ROC排名 + EY排名 → 取前 top_n
"""

import contextlib
import json
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

import akshare as ak
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ProxyError, ConnectionError as ReqConnectionError
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_HERE, "magic_formula_cache.json")
CACHE_MAX_HOURS = 18
TOP_N_DEFAULT = 30
MAX_WORKERS = 8
_FETCH_DELAY = 1.5   # seconds per worker between calls (WAF 降频保护)
_FETCH_TIMEOUT = 5   # 硬超时：5 秒，与 crawler.py 保持一致

# 随机 User-Agent 池（同 crawler.py），防止代理因固定 UA 拒绝连接
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
]

_PROXY_RETRY_LIMIT = 3  # ProxyError 最大重试次数

# 东财域名级别 Headers（伪装真实浏览器，降低 WAF 命中率）
_EASTMONEY_HEADERS: dict[str, str] = {
    "Referer": "https://www.eastmoney.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "application/json, text/plain, */*",
}

# 种子预热 URL 与超时
_SEED_URL = "https://www.baidu.com"
_SEED_TIMEOUT = 6  # 秒

# 代理环境变量键名（清除时使用）
_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy",
)


def _build_session() -> requests.Session:
    """构建带指数退避重试的持久化 Session（避免每次请求重建 TCP 连接）。"""
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_SESSION: requests.Session = _build_session()


@contextlib.contextmanager
def _no_proxy_ctx():
    """
    临时清除系统代理环境变量，让 akshare 直连目标服务器。

    akshare 内部使用 requests，代理由 os.environ 的 HTTP_PROXY /
    HTTPS_PROXY 决定。代理故障时，直接清除即可让 akshare 绕过代理。
    os.environ 在进程内所有线程共享，因此 ThreadPoolExecutor 内的
    akshare 调用也会受益。
    """
    saved = {k: os.environ.pop(k, None) for k in _PROXY_ENV_KEYS}
    proxy_list = [k for k, v in saved.items() if v is not None]
    if proxy_list:
        logger.info("代理免疫：临时清除代理环境变量 %s，akshare 将直连", proxy_list)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        if proxy_list:
            logger.info("代理免疫：已恢复代理环境变量")


def _random_headers(extra: Optional[dict] = None) -> dict:
    """生成含随机 UA、东财 Referer 的请求头（伪装真实浏览器，降低 WAF 命中率）。"""
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        **_EASTMONEY_HEADERS,
    }
    if extra:
        headers.update(extra)
    return headers


def _proxy_resilient_get(url: str, **kwargs) -> requests.Response:
    """
    带代理免疫力的 GET 请求：
    - 随机 UA + Connection:close
    - 5 秒硬超时
    - ProxyError / ConnectionError 自动重试 3 次
    - 第 2 次起强制直连（proxies={'http': None, 'https': None}）
    """
    kwargs.setdefault("timeout", _FETCH_TIMEOUT)
    kwargs.setdefault("headers", _random_headers())

    last_exc: Exception = RuntimeError("no attempt")
    for attempt in range(_PROXY_RETRY_LIMIT):
        try:
            if attempt >= 1:
                # 强制直连，绕过系统代理
                kwargs["proxies"] = {"http": None, "https": None}
                kwargs["headers"] = _random_headers()  # 换 UA
            return _SESSION.get(url, **kwargs)
        except (ProxyError, ReqConnectionError) as exc:
            last_exc = exc
            logger.warning(
                "请求失败（第 %d/%d 次）[%s]: %s",
                attempt + 1, _PROXY_RETRY_LIMIT, url[:80], exc,
            )
            time.sleep(0.5 * (attempt + 1))
    raise last_exc

def check_network_connectivity() -> bool:
    """
    种子预热：尝试访问百度确认基本网络可达。
    先走当前代理配置，若失败则强制直连再试一次。
    两次均失败才返回 False，提示用户检查 Linux 代理连通性。
    """
    for proxies in (None, {"http": None, "https": None}):
        try:
            kwargs: dict = dict(
                timeout=_SEED_TIMEOUT,
                headers=_random_headers(),
                allow_redirects=True,
            )
            if proxies is not None:
                kwargs["proxies"] = proxies
            resp = _SESSION.get(_SEED_URL, **kwargs)
            if resp.status_code < 500:
                logger.info(
                    "种子预热成功（proxies=%s，status=%s）",
                    proxies, resp.status_code,
                )
                return True
        except Exception as exc:
            logger.warning("种子预热失败（proxies=%s）: %s", proxies, exc)
    logger.error("种子预热：有代理和直连均失败，Linux 网络不可用")
    return False


# 金融行业关键词（剔除）
_FINANCIAL_KEYWORDS: frozenset[str] = frozenset({
    "银行", "保险", "证券", "信托", "多元金融", "期货", "租赁",
})

# 静态金融股代码兜底（常见大市值，网络失败时使用）
# 覆盖四大行、股份行、城商行、保险、券商等，约 60 只核心金融股
_STATIC_FINANCIAL_CODES: frozenset[str] = frozenset({
    # 国有大行
    "601398", "601939", "601288", "601988", "601328",
    # 股份制银行
    "600036", "601166", "600016", "601169", "600000", "601998", "600015",
    "601009", "601229", "601577",
    # 城商行
    "601838", "601963", "601128", "601077", "601187", "601216",
    # 保险
    "601336", "601601", "601628", "601318", "600061",
    # 证券
    "600030", "601688", "601995", "600999", "601211", "601878",
    "600837", "601375", "601198", "601901",
    # 信托/多元金融
    "600816", "601099", "600818", "601139",
})

# 资产负债表科目候选名称（兼容不同数据源版本）
_BS_ITEMS: dict[str, list[str]] = {
    "cash": ["货币资金", "现金及现金等价物"],
    "current_assets": ["流动资产合计", "流动资产总计"],
    "current_liabilities": ["流动负债合计", "流动负债总计"],
    "st_borrowings": ["短期借款", "短期贷款", "一年内到期的长期借款"],
    "lt_borrowings": ["长期借款", "长期贷款"],
    "fixed_assets": ["固定资产", "固定资产净额", "固定资产净值"],
}

# 利润表科目候选名称
_IS_ITEMS: dict[str, list[str]] = {
    "operating_profit": ["营业利润", "经营利润"],
    "financial_expense": ["财务费用", "利息费用"],
    "net_profit": ["净利润", "归属于母公司所有者的净利润"],
}


# ─────────────────────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StockScore:
    """神奇公式单只股票得分记录。"""

    code: str
    name: str
    market: str          # "A" 或 "H"
    price: float
    roc: float           # 资本回报率（小数，如 0.25 = 25%）
    ey: float            # 盈利收益率（小数）
    roc_rank: int = 0
    ey_rank: int = 0
    combined_rank: int = 0
    ah_discount_pct: Optional[float] = None  # H股折价率（负值 = H股更便宜）
    industry: str = ""
    ebit: float = 0.0
    ev: float = 0.0
    market_cap: float = 0.0
    data_quality: str = "full"  # "full" = EBIT实算, "approx" = PE近似

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StockScore":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ─────────────────────────────────────────────────────────────────────────────
# 核心计算函数（纯函数，无 I/O）
# ─────────────────────────────────────────────────────────────────────────────

def compute_roc(
    ebit: float,
    net_working_capital: float,
    net_fixed_assets: float,
) -> Optional[float]:
    """
    资本回报率 ROC = EBIT / (净营运资本 + 净固定资产)。
    分母 ≤ 0 时返回 None（物理投入为负无意义）。
    """
    denominator = net_working_capital + net_fixed_assets
    if denominator <= 0:
        return None
    return ebit / denominator


def compute_ey(ebit: float, ev: float) -> Optional[float]:
    """
    盈利收益率 EY = EBIT / EV。
    EV ≤ 0 时返回 None（企业价值为负不适用此公式）。
    """
    if ev <= 0:
        return None
    return ebit / ev


def rank_and_select(
    scores: list[StockScore],
    top_n: int = TOP_N_DEFAULT,
) -> list[StockScore]:
    """
    对候选股按 ROC 和 EY 分别排序赋予排名，
    综合排名 = ROC排名 + EY排名，取最低的前 top_n 只。
    排名数字越小越好（1 = 最佳）。
    """
    if not scores:
        return []

    sorted_by_roc = sorted(scores, key=lambda s: s.roc, reverse=True)
    for rank, stock in enumerate(sorted_by_roc, start=1):
        stock.roc_rank = rank

    sorted_by_ey = sorted(scores, key=lambda s: s.ey, reverse=True)
    for rank, stock in enumerate(sorted_by_ey, start=1):
        stock.ey_rank = rank

    for stock in scores:
        stock.combined_rank = stock.roc_rank + stock.ey_rank

    return sorted(scores, key=lambda s: s.combined_rank)[:top_n]


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _is_financial_industry(text: str) -> bool:
    """判断股票名称/行业是否属于金融类（需剔除）。"""
    return any(kw in text for kw in _FINANCIAL_KEYWORDS)


def _safe_float(val) -> Optional[float]:
    """安全转换为 float，失败或 NaN 时返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
        return None if f != f else f  # NaN check
    except (ValueError, TypeError):
        return None


def _extract_value(df: pd.DataFrame, candidates: list[str]) -> Optional[float]:
    """
    从财报 DataFrame 中按科目名称提取最新期数值。
    支持两种常见 AkShare 格式：
      Format A：行索引为科目名，列为报告日期
      Format B：第一列为科目名（字符串列），其余列为各期数值
    """
    if df is None or df.empty:
        return None

    for name in candidates:
        # Format A: index-based
        if name in df.index:
            row = df.loc[name]
            for val in row:
                if pd.notna(val) and str(val).strip() not in {"--", "", "nan"}:
                    try:
                        return float(str(val).replace(",", ""))
                    except ValueError:
                        continue

        # Format B: first column contains item names
        for col in df.columns[:2]:
            if df[col].dtype == object or df[col].dtype.name == "string":
                mask = df[col].astype(str).str.strip() == name
                if mask.any():
                    row = df[mask].iloc[0]
                    for c in df.columns:
                        if c == col:
                            continue
                        val = row[c]
                        if pd.notna(val) and str(val).strip() not in {"--", "", "nan"}:
                            try:
                                return float(str(val).replace(",", ""))
                            except ValueError:
                                continue

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 股票宇宙获取
# ─────────────────────────────────────────────────────────────────────────────

def fetch_financial_codes_a() -> frozenset[str]:
    """
    获取 A 股金融类行业成分股代码集合（银行/保险/证券/多元金融），用于剔除。
    - 任何单个行业获取失败时静默忽略，返回已获取的部分集合。
    - 若全部行业均获取失败（网络/代理故障），回退到本地硬编码的
      _STATIC_FINANCIAL_CODES，确保扫描流程不中断。
    """
    financial_boards = ["银行", "保险", "证券", "多元金融"]
    codes: set[str] = set()
    failures = 0
    for board in financial_boards:
        try:
            df = ak.stock_board_industry_cons_em(symbol=board)
            if df is not None and not df.empty:
                code_col = next((c for c in df.columns if "代码" in str(c)), None)
                if code_col:
                    codes.update(df[code_col].astype(str).str.zfill(6).tolist())
        except Exception as exc:
            failures += 1
            logger.warning("获取金融行业成分股失败 [%s]: %s", board, exc)

    if failures == len(financial_boards):
        # 全部失败 → 回退静态兜底，保障扫描不中断
        logger.warning(
            "金融黑名单网络全部失败，回退本地静态黑名单（%d 只）",
            len(_STATIC_FINANCIAL_CODES),
        )
        return _STATIC_FINANCIAL_CODES

    logger.info("金融类黑名单: %d 只（网络获取 %d 只，%d 个行业失败）",
                len(codes), len(codes), failures)
    return frozenset(codes)


def fetch_universe_a(financial_codes: frozenset[str]) -> list[dict]:
    """
    获取 A 股非金融股宇宙。
    过滤：非金融股、非ST/退市、总市值 > 20亿 CNY、现价 > 0、PE ∈ (0, 150]。
    """
    try:
        df = ak.stock_zh_a_spot_em()
    except Exception as exc:
        logger.error("获取A股全市场数据失败: %s", exc)
        return []

    if df is None or df.empty:
        return []

    col_map: dict[str, str] = {}
    for col in df.columns:
        s = str(col)
        if s == "代码":
            col_map[col] = "code"
        elif s == "名称":
            col_map[col] = "name"
        elif "最新价" in s:
            col_map[col] = "price"
        elif "总市值" in s:
            col_map[col] = "market_cap"
        elif "市盈率" in s and "动态" in s:
            col_map[col] = "pe"
    df = df.rename(columns=col_map)

    result: list[dict] = []
    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        name = str(row.get("name", ""))
        price = _safe_float(row.get("price"))
        market_cap = _safe_float(row.get("market_cap"))
        pe = _safe_float(row.get("pe"))

        if price is None or price <= 0:
            continue
        if market_cap is None or market_cap < 2e9:
            continue
        if pe is None or pe <= 0 or pe > 150:
            continue
        if "ST" in name or "退" in name:
            continue
        if code in financial_codes:
            continue

        result.append({
            "code": code, "name": name, "price": price,
            "market_cap": market_cap, "pe": pe, "market": "A",
        })

    logger.info("A股非金融宇宙: %d 只（原始 %d 只）", len(result), len(df))
    return result


def fetch_universe_h() -> list[dict]:
    """
    获取 H 股非金融股宇宙（按市值过滤 + 金融名称过滤）。
    仅保留 总市值 > 5亿 HKD、PE ∈ (0, 100] 的标的。
    """
    try:
        df = ak.stock_hk_spot_em()
    except Exception as exc:
        logger.error("获取H股市场数据失败: %s", exc)
        return []

    if df is None or df.empty:
        return []

    col_map: dict[str, str] = {}
    for col in df.columns:
        s = str(col)
        if s == "代码":
            col_map[col] = "code"
        elif s == "名称":
            col_map[col] = "name"
        elif "最新价" in s or s == "现价":
            col_map[col] = "price"
        elif "总市值" in s:
            col_map[col] = "market_cap"
        elif "市盈" in s:
            col_map[col] = "pe"
        elif "市净" in s:
            col_map[col] = "pb"
    df = df.rename(columns=col_map)

    result: list[dict] = []
    for _, row in df.iterrows():
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        price = _safe_float(row.get("price"))
        market_cap = _safe_float(row.get("market_cap"))
        pe = _safe_float(row.get("pe"))
        pb = _safe_float(row.get("pb"))

        if price is None or price <= 0:
            continue
        if market_cap is None or market_cap < 5e8:
            continue
        if pe is None or pe <= 0 or pe > 100:
            continue
        if _is_financial_industry(name):
            continue

        result.append({
            "code": code, "name": name, "price": price,
            "market_cap": market_cap, "pe": pe, "pb": pb or 0.0, "market": "H",
        })

    logger.info("H股非金融宇宙: %d 只（原始 %d 只）", len(result), len(df))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 财务数据获取与计算
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_sheet(fetch_fn, code: str, label: str) -> Optional[pd.DataFrame]:
    """包裹财报接口调用，失败时返回 None。"""
    try:
        df = fetch_fn(symbol=code)
        return df if df is not None and not df.empty else None
    except Exception as exc:
        logger.debug("[%s] %s获取失败: %s", code, label, exc)
        return None


def _compute_from_sheets(
    bs: pd.DataFrame,
    pl: pd.DataFrame,
    market_cap: float,
) -> Optional[dict]:
    """
    从资产负债表和利润表计算 EBIT / NWC / 净固定资产 / EV。
    返回 {ebit, nwc, fixed_assets, ev} 或 None。
    """
    cash = _extract_value(bs, _BS_ITEMS["cash"]) or 0.0
    current_assets = _extract_value(bs, _BS_ITEMS["current_assets"])
    current_liabilities = _extract_value(bs, _BS_ITEMS["current_liabilities"])
    st_borrowings = _extract_value(bs, _BS_ITEMS["st_borrowings"]) or 0.0
    lt_borrowings = _extract_value(bs, _BS_ITEMS["lt_borrowings"]) or 0.0
    fixed_assets = _extract_value(bs, _BS_ITEMS["fixed_assets"]) or 0.0

    if current_assets is None or current_liabilities is None:
        return None

    operating_profit = _extract_value(pl, _IS_ITEMS["operating_profit"])
    financial_expense = _extract_value(pl, _IS_ITEMS["financial_expense"]) or 0.0

    if operating_profit is None:
        return None

    # EBIT = 营业利润 + max(0, 财务费用)（加回利息支出）
    ebit = operating_profit + max(0.0, financial_expense)

    # 净营运资本 = (流动资产 - 现金) - (流动负债 - 短期借款)
    nwc = (current_assets - cash) - (current_liabilities - st_borrowings)

    # 企业价值 = 市值 + 净负债
    net_debt = (st_borrowings + lt_borrowings) - cash
    ev = market_cap + net_debt

    return {"ebit": ebit, "nwc": nwc, "fixed_assets": fixed_assets, "ev": ev}


def fetch_financials_a(stock: dict) -> Optional[StockScore]:
    """
    获取单只 A 股的财务数据，计算并返回 StockScore。
    任何关键数据缺失（或 ROC/EY 为负）时返回 None。
    """
    code = stock["code"]
    market_cap = stock["market_cap"]
    time.sleep(_FETCH_DELAY)

    bs = _fetch_sheet(ak.stock_balance_sheet_by_report_em, code, "资产负债表")
    pl = _fetch_sheet(ak.stock_profit_sheet_by_report_em, code, "利润表")

    if bs is None or pl is None:
        return None

    metrics = _compute_from_sheets(bs, pl, market_cap)
    if metrics is None:
        return None

    ebit, nwc, fixed_assets, ev = (
        metrics["ebit"], metrics["nwc"], metrics["fixed_assets"], metrics["ev"]
    )

    if ebit <= 0 or ev <= 0:
        return None

    roc = compute_roc(ebit, nwc, fixed_assets)
    ey = compute_ey(ebit, ev)

    if roc is None or roc <= 0 or ey is None or ey <= 0:
        return None

    return StockScore(
        code=code, name=stock["name"], market="A",
        price=stock["price"], roc=roc, ey=ey,
        ebit=ebit, ev=ev, market_cap=market_cap,
        data_quality="full",
    )


def fetch_financials_h(stock: dict) -> Optional[StockScore]:
    """
    获取单只 H 股的财务数据。
    优先尝试 AkShare H 股财务报告接口（`stock_financial_hk_report_em`）；
    失败时退化到 PE 近似法（data_quality='approx'）。
    """
    code = stock["code"]
    market_cap = stock["market_cap"]
    time.sleep(_FETCH_DELAY)

    # 尝试 H 股财务报告接口
    try:
        bs = _fetch_sheet(
            lambda symbol: ak.stock_financial_hk_report_em(symbol=symbol, indicator="资产负债表"),
            code, "H股资产负债表",
        )
        pl = _fetch_sheet(
            lambda symbol: ak.stock_financial_hk_report_em(symbol=symbol, indicator="利润表"),
            code, "H股利润表",
        )
        if bs is not None and pl is not None:
            metrics = _compute_from_sheets(bs, pl, market_cap)
            if metrics is not None:
                ebit, nwc, fixed_assets, ev = (
                    metrics["ebit"], metrics["nwc"],
                    metrics["fixed_assets"], metrics["ev"]
                )
                if ebit > 0 and ev > 0:
                    roc = compute_roc(ebit, nwc, fixed_assets)
                    ey = compute_ey(ebit, ev)
                    if roc is not None and roc > 0 and ey is not None and ey > 0:
                        return StockScore(
                            code=code, name=stock["name"], market="H",
                            price=stock["price"], roc=roc, ey=ey,
                            ebit=ebit, ev=ev, market_cap=market_cap,
                            data_quality="full",
                        )
    except Exception as exc:
        logger.debug("[%s] H股财报接口失败，退化PE近似: %s", code, exc)

    # 退化：PE 近似法
    pe = _safe_float(stock.get("pe"))
    if pe is None or pe <= 0:
        return None

    # EY ≈ 1/PE × 1/(1 - 0.165)  （港股企业所得税 16.5%，换算为 EBIT 口径）
    ey_approx = (1.0 / pe) / (1.0 - 0.165)

    # ROC 近似：需要 ROE 数据，PE 法无法直接估算，故跳过
    # H股近似仅提供 EY，ROC 用 1/PB 估算（P/B 越低 ROC 越可能被低估）
    pb = _safe_float(stock.get("pb"))
    if pb is None or pb <= 0:
        return None
    roc_approx = 1.0 / pb  # 极粗略近似

    ebit_approx = ey_approx * market_cap
    ev_approx = market_cap  # 近似忽略负债

    return StockScore(
        code=code, name=stock["name"], market="H",
        price=stock["price"], roc=roc_approx, ey=ey_approx,
        ebit=ebit_approx, ev=ev_approx, market_cap=market_cap,
        data_quality="approx",
    )


# ─────────────────────────────────────────────────────────────────────────────
# AH 溢价
# ─────────────────────────────────────────────────────────────────────────────

def fetch_ah_premium_map() -> dict[str, float]:
    """
    获取 AH 股溢价率数据，返回 {H股代码: 折价率%}。
    折价率为负表示 H 股相对 A 股更便宜。
    """
    try:
        df = ak.stock_zh_ah_spot_em()
        if df is None or df.empty:
            return {}
        result: dict[str, float] = {}
        for _, row in df.iterrows():
            # 列名参考：H股代码, AH溢价率 or 溢价率
            h_code = str(row.get("H股代码", row.get("港股代码", ""))).strip()
            premium = _safe_float(
                row.get("AH溢价率", row.get("溢价率", None))
            )
            if h_code and premium is not None:
                result[h_code] = -premium  # 负值 = H股折价
        return result
    except Exception as exc:
        logger.warning("获取AH溢价数据失败: %s", exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 缓存管理
# ─────────────────────────────────────────────────────────────────────────────

def load_cache() -> Optional[dict]:
    """加载本地神奇公式缓存（JSON）。"""
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("加载神奇公式缓存失败: %s", exc)
        return None


def save_cache(result: dict) -> None:
    """原子写入神奇公式结果到本地缓存（含时间戳）。"""
    result["cached_at"] = datetime.now().isoformat()
    tmp = CACHE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_PATH)
        logger.info("神奇公式结果已缓存 → %s", CACHE_PATH)
    except Exception as exc:
        logger.error("保存神奇公式缓存失败: %s", exc)


def is_cache_fresh(cache: dict, max_hours: int = CACHE_MAX_HOURS) -> bool:
    """判断缓存是否在 max_hours 以内（当日有效）。"""
    cached_at = cache.get("cached_at")
    if not cached_at:
        return False
    try:
        cached_time = datetime.fromisoformat(str(cached_at))
        elapsed_hours = (datetime.now() - cached_time).total_seconds() / 3600
        return elapsed_hours < max_hours
    except (ValueError, TypeError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 主扫描函数
# ─────────────────────────────────────────────────────────────────────────────

def scan_magic_formula(
    top_n: int = TOP_N_DEFAULT,
    include_h: bool = True,
    progress_callback=None,
) -> dict:
    """
    全市场神奇公式扫描。

    Args:
        top_n: 返回排名前 N 只标的（默认 30）。
        include_h: 是否包含 H 股扫描。
        progress_callback: 可选进度回调 `fn(pct: float, msg: str)`。

    Returns:
        dict with keys:
            top_stocks  - list[StockScore.to_dict()]
            scanned_count - 有效财务数据的股票数量
            universe_size - 候选宇宙总量
            cached_at   - 写入缓存时的时间戳
    """
    logger.info("=== 神奇公式全市场扫描开始 ===")

    def _progress(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)
        logger.info("[%.0f%%] %s", pct * 100, msg)

    def _run_scan() -> dict:
        """核心扫描逻辑，可在有/无代理的 context 下调用。"""
        # Step 1: 构建股票宇宙
        _progress(0.02, "获取金融行业黑名单…")
        financial_codes = fetch_financial_codes_a()
        time.sleep(1.5)  # WAF 降频：黑名单请求与 A 股宇宙之间

        _progress(0.05, "获取 A 股宇宙…")
        universe_a = fetch_universe_a(financial_codes)
        time.sleep(1.5)  # WAF 降频：A 股宇宙与 H 股宇宙之间

        universe_h: list[dict] = []
        if include_h:
            _progress(0.09, "获取 H 股宇宙…")
            universe_h = fetch_universe_h()
            time.sleep(1.5)  # WAF 降频：H 股宇宙与 AH 溢价之间

        universe = universe_a + universe_h
        total = len(universe)
        _progress(0.12, f"候选宇宙：A股 {len(universe_a)} + H股 {len(universe_h)} = {total} 只")

        if total == 0:
            return {"top_stocks": [], "scanned_count": 0, "universe_size": 0}

        # Step 2: 获取 AH 溢价
        _progress(0.14, "获取 AH 溢价数据…")
        ah_map = fetch_ah_premium_map()

        # Step 3: 并行获取财务数据
        valid_scores: list[StockScore] = []
        completed = 0

        def _worker(stock: dict) -> Optional[StockScore]:
            fn = fetch_financials_a if stock["market"] == "A" else fetch_financials_h
            score = fn(stock)
            if score is not None and stock["market"] == "H":
                score.ah_discount_pct = ah_map.get(score.code)
            return score

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(_worker, s): s for s in universe}
            for future in as_completed(futures):
                completed += 1
                scan_result = future.result()
                if scan_result is not None:
                    valid_scores.append(scan_result)
                if completed % 100 == 0 or completed == total:
                    pct = 0.14 + 0.76 * completed / total
                    _progress(pct, f"财务获取 {completed}/{total}，有效 {len(valid_scores)} 只")

        logger.info("有效财务数据: %d 只", len(valid_scores))

        # Step 4: 排名与筛选
        _progress(0.92, "计算综合排名…")
        top_stocks = rank_and_select(valid_scores, top_n=top_n)

        _progress(1.0, f"扫描完成：Top {len(top_stocks)} 只神奇公式股票")

        return {
            "top_stocks": [s.to_dict() for s in top_stocks],
            "scanned_count": len(valid_scores),
            "universe_size": total,
        }

    # 种子预热：确认基本网络连通性
    _progress(0.01, "检查网络连通性…")
    if not check_network_connectivity():
        logger.error("种子预热失败，Linux 网络/代理环境不可用")
        return {
            "top_stocks": [],
            "scanned_count": 0,
            "universe_size": 0,
            "error": "network_unavailable",
        }

    # 第一次：尊重当前环境代理配置（有代理走代理，无代理直连）
    output = _run_scan()

    # 若宇宙为空且检测到代理配置，代理可能是故障原因，尝试直连重试
    _has_proxy = any(os.environ.get(k) for k in _PROXY_ENV_KEYS)
    if output.get("universe_size", 0) == 0 and _has_proxy:
        logger.warning("宇宙为空且检测到代理配置，尝试直连重试…")
        _progress(0.03, "代理疑似故障，切换直连重试…")
        with _no_proxy_ctx():
            output = _run_scan()

    save_cache(output)
    return output
