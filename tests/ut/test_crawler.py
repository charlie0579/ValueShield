"""
tests/ut/test_crawler.py - crawler.py 单元测试
通过 Mock AkShare 接口，隔离网络依赖，测试重试机制和数据解析逻辑。
"""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch, call

import crawler
from crawler import (
    fetch_realtime_price,
    fetch_dividend_ttm,
    fetch_stock_name,
    compute_dividend_yield,
    _retry,
)


# ─────────────────────────────────────────────────────────────────────────────
# _retry 通用重试机制
# ─────────────────────────────────────────────────────────────────────────────
class TestRetry:
    def test_success_on_first_attempt(self):
        func = MagicMock(return_value=42)
        func.__name__ = "mock_func"
        result = _retry(func, retries=3, delay=0)
        assert result == 42
        func.assert_called_once()

    def test_success_on_second_attempt(self):
        func = MagicMock(side_effect=[Exception("err"), 99])
        func.__name__ = "mock_func"
        result = _retry(func, retries=3, delay=0)
        assert result == 99
        assert func.call_count == 2

    def test_raises_after_max_retries(self):
        func = MagicMock(side_effect=Exception("always fails"))
        func.__name__ = "mock_func"
        with pytest.raises(RuntimeError, match="always fails"):
            _retry(func, retries=3, delay=0)
        assert func.call_count == 3

    def test_exact_retry_count(self):
        func = MagicMock(side_effect=[ValueError("1"), ValueError("2"), "ok"])
        func.__name__ = "mock_func"
        result = _retry(func, retries=3, delay=0)
        assert result == "ok"
        assert func.call_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# fetch_realtime_price
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchRealtimePrice:
    def _make_spot_df(self, code: str, price: float) -> pd.DataFrame:
        return pd.DataFrame([{"代码": code, "名称": "测试股票", "最新价": str(price)}])

    def test_returns_price_by_exact_code(self):
        df = self._make_spot_df("01336", 28.5)
        with patch("akshare.stock_hk_spot_em", return_value=df):
            result = fetch_realtime_price("01336")
        assert result == pytest.approx(28.5)

    def test_returns_none_on_network_failure(self):
        with patch("akshare.stock_hk_spot_em", side_effect=Exception("timeout")):
            with patch("requests.get", side_effect=ConnectionError("sina also down")):
                result = fetch_realtime_price("01336")
        assert result is None

    def test_returns_none_when_code_not_found(self):
        empty_df = pd.DataFrame(columns=["代码", "名称", "最新价"])
        with patch("akshare.stock_hk_spot_em", return_value=empty_df):
            result = fetch_realtime_price("99999")
        assert result is None

    def test_retries_on_transient_error(self):
        df = self._make_spot_df("01336", 30.0)
        call_count = 0

        def flaky_spot():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return df

        with patch("akshare.stock_hk_spot_em", side_effect=flaky_spot):
            with patch("time.sleep"):
                result = fetch_realtime_price("01336")
        assert result == pytest.approx(30.0)
        assert call_count == 3

    def test_returns_float_type(self):
        df = self._make_spot_df("00525", 4.2)
        with patch("akshare.stock_hk_spot_em", return_value=df):
            result = fetch_realtime_price("00525")
        assert isinstance(result, float)


# ─────────────────────────────────────────────────────────────────────────────
# fetch_dividend_ttm
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchDividendTTM:
    """分红方案格式: '每股派人民币X.XX元(相当于港币Y.YY元)'，提取港元金额。"""

    def _make_div_df(self, dates: list[str], plans: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"除净日": dates, "分红方案": plans})

    def test_sums_dividends_within_12_months(self):
        df = self._make_div_df(
            ["2025-06-01", "2025-12-01", "2024-01-01"],
            [
                "每股派人民币0.44元(相当于港币0.50元)",
                "每股派人民币0.70元(相当于港币0.80元)",
                "每股派人民币0.88元(相当于港币1.0元)",  # 超过1年，不计入
            ],
        )
        with patch("akshare.stock_hk_dividend_payout_em", return_value=df):
            result = fetch_dividend_ttm("01336", years=1)
        assert result == pytest.approx(1.3, rel=1e-4)

    def test_returns_zero_for_empty_dataframe(self):
        empty = pd.DataFrame()
        with patch("akshare.stock_hk_dividend_payout_em", return_value=empty):
            result = fetch_dividend_ttm("01336")
        assert result == pytest.approx(0.0)

    def test_returns_zero_on_exception(self):
        with patch("akshare.stock_hk_dividend_payout_em", side_effect=Exception("err")):
            result = fetch_dividend_ttm("01336")
        assert result == pytest.approx(0.0)

    def test_all_dividends_too_old_returns_zero(self):
        df = self._make_div_df(
            ["2020-01-01", "2019-06-01"],
            [
                "每股派人民币1.0元(相当于港币1.0元)",
                "每股派人民币0.5元(相当于港币0.5元)",
            ],
        )
        with patch("akshare.stock_hk_dividend_payout_em", return_value=df):
            result = fetch_dividend_ttm("01336", years=1)
        assert result == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# fetch_stock_name
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchStockName:
    def test_returns_name(self):
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "28.5"}])
        with patch("akshare.stock_hk_spot_em", return_value=df):
            name = fetch_stock_name("01336")
        assert name == "新华保险"

    def test_returns_code_on_failure(self):
        with patch("akshare.stock_hk_spot_em", side_effect=Exception("err")):
            name = fetch_stock_name("01336")
        assert name == "01336"

    def test_returns_code_when_not_found(self):
        empty = pd.DataFrame(columns=["代码", "名称", "最新价"])
        with patch("akshare.stock_hk_spot_em", return_value=empty):
            name = fetch_stock_name("99999")
        assert name == "99999"


# ─────────────────────────────────────────────────────────────────────────────
# compute_dividend_yield
# ─────────────────────────────────────────────────────────────────────────────
class TestComputeDividendYield:
    def test_basic_calculation(self):
        result = compute_dividend_yield(annual_dividend_hkd=1.8, current_price=28.5)
        assert result == pytest.approx(1.8 / 28.5, rel=1e-6)

    def test_zero_price_returns_zero(self):
        result = compute_dividend_yield(1.8, 0.0)
        assert result == 0.0

    def test_negative_price_returns_zero(self):
        result = compute_dividend_yield(1.8, -5.0)
        assert result == 0.0

    def test_zero_dividend_returns_zero(self):
        result = compute_dividend_yield(0.0, 28.5)
        assert result == pytest.approx(0.0)

    def test_high_yield_scenario(self):
        result = compute_dividend_yield(5.0, 20.0)
        assert result == pytest.approx(0.25)
