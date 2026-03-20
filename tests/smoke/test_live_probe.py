"""
tests/smoke/test_live_probe.py — 影子数据冒烟测试（Live Data Probe）

用 3-5 只已知的真实 A/H 股跑完整神奇公式计算流程，
验证 AkShare 接口适配是否正常，ROC/EY 是否可计算。

标记：@pytest.mark.smoke
仅在有网络连接时手动运行：
    pytest -m smoke tests/smoke/ -v

不加入 CI（CI 中用 pytest -m "not smoke" 排除）。

什么时候跑？
  - 每次更新 magic_formula.py 中的 AkShare 接口调用后
  - 收到「数据为空」的用户反馈后
  - 节假日后首个交易日，验证接口未失效
"""

from __future__ import annotations

import math
from typing import Optional

import pytest

from magic_formula import (
    StockScore,
    _is_financial_industry,
    compute_ey,
    compute_roc,
    fetch_financial_codes_a,
    fetch_financials_a,
    fetch_financials_h,
    fetch_universe_a,
    fetch_universe_h,
)

pytestmark = pytest.mark.smoke

# ── 探针股票（股票宇宙中已知的大市值、数据稳定标的）──────────────────────────
# 注意：金融类（银行/保险）股票会被神奇公式主动过滤，不应出现在 top_stocks
# 此处故意包含两种情况，以验证过滤逻辑和数据读取均正常

_PROBE_NONFINANCIAL_A = [
    # 代码, 名称, 大致市值(CNY), 大致现价
    ("600519", "贵州茅台", 2.0e12, 1700.0),  # 消费 — ROC 应极高
    ("000858", "五粮液",   3.0e11,  145.0),  # 消费
    ("601888", "中国国旅", 2.5e11,  100.0),  # 消费/旅游
]

_PROBE_FINANCIAL_A = [
    # 这些股票预期被 _is_financial_industry 过滤掉，fetch_financials_a 返回 None
    ("601398", "工商银行", 1.8e12, 6.0),
    ("601336", "新华保险", 2.0e11, 52.0),
]

_PROBE_H = [
    # H 股探针（部分使用 PE 近似法）
    ("01336", "新华保险 H", 0),  # 保险，可能被过滤
    ("00700", "腾讯控股",  0),  # 科技，应能计算（PE 近似法）
]


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数计算层：不需要网络（快速验证公式正确性）
# ─────────────────────────────────────────────────────────────────────────────

class TestPureFunctions:
    """验证 compute_roc / compute_ey 纯函数的数值正确性（无网络）。"""

    def test_compute_roc_standard_case(self):
        """ROC = EBIT / (NWC + 固定资产)，标准情形。"""
        roc = compute_roc(ebit=1e9, net_working_capital=3e9, net_fixed_assets=2e9)
        assert roc is not None
        expected = 1e9 / (3e9 + 2e9)
        assert math.isclose(roc, expected, rel_tol=1e-9)

    def test_compute_roc_negative_denominator_returns_none(self):
        """分母为负时应返回 None（避免虚假正 ROC）。"""
        roc = compute_roc(ebit=1e9, net_working_capital=-4e9, net_fixed_assets=1e9)
        # NWC + 固定资产 = -3e9 < 0 → None
        assert roc is None

    def test_compute_ey_standard_case(self):
        """EY = EBIT / EV，标准情形。"""
        ey = compute_ey(ebit=5e9, ev=100e9)
        assert ey is not None
        assert math.isclose(ey, 0.05, rel_tol=1e-9)

    def test_compute_ey_nonpositive_ebit_returns_none(self):
        """EBIT <= 0 时应返回 None。"""
        assert compute_ey(ebit=0.0, ev=100e9) is None
        assert compute_ey(ebit=-1e9, ev=100e9) is None

    def test_compute_ey_nonpositive_ev_returns_none(self):
        """EV <= 0 时应返回 None。"""
        assert compute_ey(ebit=1e9, ev=0.0) is None

    def test_financial_industry_filter_catches_banks(self):
        """金融行业过滤器应识别银行/保险类名称。"""
        assert _is_financial_industry("工商银行")
        assert _is_financial_industry("平安保险")
        assert _is_financial_industry("证券公司")
        assert not _is_financial_industry("贵州茅台")
        assert not _is_financial_industry("中国国旅")


# ─────────────────────────────────────────────────────────────────────────────
# 接口层：需要真实网络（smoke 标记，手动运行）
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveUniverseFetch:
    """验证股票宇宙抓取接口正常，返回足够数量的标的。"""

    def test_financial_codes_returns_nonempty(self):
        codes = fetch_financial_codes_a()
        assert isinstance(codes, frozenset), "应返回 frozenset"
        assert len(codes) >= 10, (
            f"金融行业代码集过少（{len(codes)} 个），接口可能失效"
        )

    def test_universe_a_returns_minimum_stocks(self):
        codes = fetch_financial_codes_a()
        universe = fetch_universe_a(codes)
        assert len(universe) >= 100, (
            f"A 股宇宙只有 {len(universe)} 只，期望 >= 100\n"
            "可能原因：AkShare 接口返回格式变更，或代理/网络异常"
        )

    def test_universe_a_stock_has_required_fields(self):
        codes = fetch_financial_codes_a()
        universe = fetch_universe_a(codes)
        if not universe:
            pytest.skip("宇宙为空，跳过字段校验")
        stock = universe[0]
        for field in ("code", "name", "price", "market_cap", "market"):
            assert field in stock, f"宇宙股票缺少字段：{field!r}"
        assert stock["market"] == "A"
        assert stock["price"] > 0
        assert stock["market_cap"] > 2e9

    def test_universe_h_returns_minimum_stocks(self):
        universe = fetch_universe_h()
        assert len(universe) >= 50, (
            f"H 股宇宙只有 {len(universe)} 只，期望 >= 50\n"
            "可能原因：AkShare 港股接口失效"
        )


class TestLiveNonfinancialAStock:
    """
    对 3 只已知非金融 A 股跑 fetch_financials_a，
    验证至少有 1 只能成功算出 ROC 和 EY。
    """

    def _make_stock_dict(self, code: str, name: str, market_cap: float, price: float) -> dict:
        return {
            "code": code,
            "name": name,
            "price": price,
            "market_cap": market_cap,
            "pe": 20.0,
            "market": "A",
        }

    def test_at_least_one_probe_stock_computes_roc_ey(self):
        """至少 1 只探针股票应返回有效 StockScore（ROC > 0 且 EY > 0）。"""
        successes: list[tuple[str, StockScore]] = []
        failures: list[tuple[str, str]] = []

        for code, name, market_cap, price in _PROBE_NONFINANCIAL_A:
            stock_dict = self._make_stock_dict(code, name, market_cap, price)
            result: Optional[StockScore] = fetch_financials_a(stock_dict)
            if result is not None and result.roc > 0 and result.ey > 0:
                successes.append((code, result))
            else:
                reason = "返回 None" if result is None else f"ROC={result.roc:.2%} EY={result.ey:.2%}"
                failures.append((code, reason))

        assert successes, (
            f"所有探针 A 股均未能计算 ROC/EY，接口适配可能失效！\n"
            f"失败详情：{failures}\n"
            f"请检查：AkShare 财务报表接口 / 网络代理 / 数据列名映射"
        )

    def test_financial_stocks_are_filtered_out(self):
        """金融类 A 股应被过滤（fetch_financials_a 返回 None）。"""
        for code, name, market_cap, price in _PROBE_FINANCIAL_A:
            stock_dict = self._make_stock_dict(code, name, market_cap, price)
            result = fetch_financials_a(stock_dict)
            assert result is None, (
                f"{code} ({name}) 是金融股，应被过滤返回 None，实际：{result}"
            )

    @pytest.mark.parametrize("code,name,market_cap,price", _PROBE_NONFINANCIAL_A)
    def test_individual_probe_stock(self, code: str, name: str, market_cap: float, price: float):
        """逐只检查探针股票的 ROC/EY 值域合理性（可单独定位问题股票）。"""
        stock_dict = self._make_stock_dict(code, name, market_cap, price)
        result = fetch_financials_a(stock_dict)

        if result is None:
            pytest.xfail(
                f"{code} ({name}): fetch_financials_a 返回 None，"
                "可能是财报数据暂缺或接口格式变更（xfail，不计为失败）"
            )

        assert result.roc > 0, f"{code}: ROC={result.roc:.2%} 应 > 0"
        assert result.ey > 0, f"{code}: EY={result.ey:.2%} 应 > 0"
        # 合理值域：ROC 不超过 2000%，EY 不超过 500%
        assert result.roc < 20.0, f"{code}: ROC={result.roc:.2%} 异常偏大，可能计算错误"
        assert result.ey < 5.0, f"{code}: EY={result.ey:.2%} 异常偏大，可能计算错误"


class TestLiveHStock:
    """对 H 股探针验证接口适配（包含全算法和 PE 近似法）。"""

    def test_at_least_one_h_stock_computable(self):
        """至少 1 只 H 股探针应返回有效 StockScore。"""
        successes = []
        for code, name, _ in _PROBE_H:
            stock_dict = {"code": code, "name": name, "price": 10.0,
                          "market_cap": 1e11, "market": "H"}
            result = fetch_financials_h(stock_dict)
            if result is not None:
                successes.append(code)

        assert successes, (
            f"所有 H 股探针均返回 None，接口适配可能失效：{[c for c, *_ in _PROBE_H]}\n"
            "请检查：AkShare H 股财务接口 / PE 近似法回退是否正常"
        )
