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
)


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

    def test_falls_back_to_sina_when_akshare_fails(self):
        """AkShare 失败时自动降级到新浪通道（现代 hk格式）。"""
        import crawler as _crawler
        _crawler._last_known_prices.pop("01336", None)  # 清除偏差校验状态
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        # 新浪现代格式：var hq_str_hk01336="名称,昨收,现价,..."
        mock_resp.text = 'var hq_str_hk01336="新华保险,29.0,30.5,...";'
        with patch("akshare.stock_hk_spot_em", side_effect=Exception("timeout")):
            with patch("requests.get", return_value=mock_resp):
                result = fetch_realtime_price("01336")
        assert result == pytest.approx(30.5)

    def test_channel_memory_updates_on_fallback(self):
        """备用通道成功后，_preferred_channel 应更新为 sina。"""
        import crawler as _crawler
        _crawler._preferred_channel = "akshare"  # 重置初始状态
        _crawler._last_known_prices.pop("01336", None)  # 清除偏差校验状态
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = 'var hq_str_hk01336="新华保险,27.0,28.0,...";'
        with patch("akshare.stock_hk_spot_em", side_effect=Exception("ak down")):
            with patch("requests.get", return_value=mock_resp):
                fetch_realtime_price("01336")
        assert _crawler._preferred_channel == "sina"
        _crawler._preferred_channel = "akshare"  # 恢复默认状态

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


# ─────────────────────────────────────────────────────────────────────────────
# 价格偏差校验 & EM Web 第三通道
# ─────────────────────────────────────────────────────────────────────────────
class TestPriceDriftAndEmWeb:
    def setup_method(self):
        """每个测试前清除 _last_known_prices 状态。"""
        import crawler as _crawler
        _crawler._last_known_prices.pop("01336", None)

    def test_em_web_triggered_on_large_drift(self):
        """价格偏差 > 20% 时触发 EM Web 第三通道，最终采用 EM Web 结果。"""
        import crawler as _crawler
        _crawler._last_known_prices["01336"] = 28.0  # 上次已知价
        # AkShare 返回 50.0，偏差 78% > 20%，应触发 EM Web
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "50.0"}])
        em_resp = MagicMock()
        em_resp.raise_for_status = MagicMock()
        em_resp.json.return_value = {"data": {"f43": 28500, "f57": "01336"}}
        with patch("akshare.stock_hk_spot_em", return_value=df):
            with patch("requests.get", return_value=em_resp):
                result = fetch_realtime_price("01336")
        # EM Web 返回 28500÷1000 = 28.5
        assert result == pytest.approx(28.5)
        _crawler._last_known_prices.pop("01336", None)

    def test_no_em_web_when_drift_small(self):
        """价格偏差 < 20% 时不触发 EM Web，直接返回 AkShare 结果。"""
        import crawler as _crawler
        _crawler._last_known_prices["01336"] = 28.0  # 上次已知价
        # AkShare 返回 29.0，偏差 3.6% < 20%，不应发起额外 HTTP 请求
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "29.0"}])
        with patch("akshare.stock_hk_spot_em", return_value=df):
            with patch("requests.get") as mock_get:
                result = fetch_realtime_price("01336")
        mock_get.assert_not_called()
        assert result == pytest.approx(29.0)
        _crawler._last_known_prices.pop("01336", None)

    def test_price_drift_warning_logged(self, caplog):
        """偏差 > 20% 时记录 WARNING 日志。"""
        import logging
        import crawler as _crawler
        _crawler._last_known_prices["01336"] = 28.0
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "50.0"}])
        em_resp = MagicMock()
        em_resp.raise_for_status = MagicMock()
        em_resp.json.return_value = {"data": {"f43": 28500, "f57": "01336"}}
        with caplog.at_level(logging.WARNING, logger="crawler"):
            with patch("akshare.stock_hk_spot_em", return_value=df):
                with patch("requests.get", return_value=em_resp):
                    fetch_realtime_price("01336")
        assert any("drift" in r.message.lower() or "偏差" in r.message for r in caplog.records)
        _crawler._last_known_prices.pop("01336", None)
