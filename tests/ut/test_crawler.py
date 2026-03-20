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


# ─────────────────────────────────────────────────────────────────────────────
# v2.4 估值分位函数
# ─────────────────────────────────────────────────────────────────────────────
from crawler import compute_percentile, get_valuation_label


class TestComputePercentile:
    def test_insufficient_data_returns_minus_one(self):
        assert compute_percentile(5.0, [1.0, 2.0, 3.0]) == -1.0

    def test_higher_is_better_100pct(self):
        # 当前值最高 → 分位=100%
        result = compute_percentile(10.0, [2.0, 4.0, 6.0, 8.0, 10.0], higher_is_better=True)
        assert result == pytest.approx(100.0)

    def test_higher_is_better_0pct(self):
        # 当前值最低 → 分位=20%（只有一个 <= 1.0 的）
        result = compute_percentile(1.0, [1.0, 4.0, 6.0, 8.0, 10.0], higher_is_better=True)
        assert result == pytest.approx(20.0)

    def test_lower_is_better_100pct(self):
        # PB 当前最低 → 历史所有都 >= → 分位=100%
        result = compute_percentile(0.5, [0.5, 0.8, 1.0, 1.2, 1.5], higher_is_better=False)
        assert result == pytest.approx(100.0)

    def test_lower_is_better_0pct(self):
        # PB 当前最高 → 无历史 >= → 分位=0%（只有当前=最高本身）
        result = compute_percentile(2.0, [0.5, 0.8, 1.0, 1.2, 2.0], higher_is_better=False)
        assert result == pytest.approx(20.0)

    def test_all_zeros_filtered(self):
        # 历史含 0 应被过滤
        result = compute_percentile(5.0, [0.0, 0.0, 0.0, 5.0], higher_is_better=True)
        assert result == -1.0  # 过滤后只剩 1 个有效值，不足 4 个


class TestGetValuationLabel:
    def test_label_extreme_underval(self):
        label = get_valuation_label(92.0, "股息率", 8.5, "%")
        assert "极度低估" in label
        assert "🚀" in label

    def test_label_overval(self):
        label = get_valuation_label(15.0, "PB", 2.5, "x")
        assert "高估" in label
        assert "🔴" in label

    def test_label_no_data(self):
        label = get_valuation_label(-1.0, "股息率", 6.0, "%")
        assert "历史数据不足" in label


# ─────────────────────────────────────────────────────────────────────────────
# fetch_pb_history
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchPbHistory:
    def test_returns_list_on_success(self):
        import akshare as ak
        import pandas as pd
        from crawler import fetch_pb_history

        fake_df = pd.DataFrame({"date": ["2023", "2022"], "pb": [1.2, 1.5]})
        with patch.object(ak, "stock_hk_valuation_baidu", return_value=fake_df):
            result = fetch_pb_history("01336")
        assert result == [1.2, 1.5]

    def test_returns_empty_on_empty_df(self):
        import akshare as ak
        import pandas as pd
        from crawler import fetch_pb_history

        with patch.object(ak, "stock_hk_valuation_baidu", return_value=pd.DataFrame()):
            result = fetch_pb_history("01336")
        assert result == []

    def test_returns_empty_on_exception(self):
        import akshare as ak
        from crawler import fetch_pb_history

        with patch.object(ak, "stock_hk_valuation_baidu", side_effect=RuntimeError("net")):
            result = fetch_pb_history("01336")
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# fetch_div_yield_history
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchDivYieldHistory:
    def test_returns_empty_when_price_nonpositive(self):
        from crawler import fetch_div_yield_history

        assert fetch_div_yield_history("01336", 0.0) == []
        assert fetch_div_yield_history("01336", -5.0) == []

    def test_returns_yield_list_on_success(self):
        import akshare as ak
        import pandas as pd
        from crawler import fetch_div_yield_history

        fake_df = pd.DataFrame({
            "除净日": ["2023-06-01", "2022-06-01", "2021-06-01"],
            "分红方案": [
                "每股派港币1.80元",
                "每股派港币1.60元",
                "每股派港币1.40元",
            ],
        })
        with patch.object(ak, "stock_hk_dividend_payout_em", return_value=fake_df):
            result = fetch_div_yield_history("01336", current_price=30.0, years=5)
        assert len(result) == 3
        assert result[0] == pytest.approx(1.80 / 30.0)

    def test_returns_empty_on_exception(self):
        import akshare as ak
        from crawler import fetch_div_yield_history

        with patch.object(ak, "stock_hk_dividend_payout_em", side_effect=RuntimeError("net")):
            result = fetch_div_yield_history("01336", 30.0)
        assert result == []



# ─────────────────────────────────────────────────────────────────────────────
# fetch_pb_history
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchPbHistory:
    def test_returns_list_on_success(self):
        import akshare as ak
        import pandas as pd
        from crawler import fetch_pb_history

        fake_df = pd.DataFrame({"date": ["2023", "2022"], "pb": [1.2, 1.5]})
        with patch.object(ak, "stock_hk_valuation_baidu", return_value=fake_df):
            result = fetch_pb_history("01336")
        assert result == [1.2, 1.5]

    def test_returns_empty_on_empty_df(self):
        import akshare as ak
        import pandas as pd
        from crawler import fetch_pb_history

        with patch.object(ak, "stock_hk_valuation_baidu", return_value=pd.DataFrame()):
            result = fetch_pb_history("01336")
        assert result == []

    def test_returns_empty_on_exception(self):
        import akshare as ak
        from crawler import fetch_pb_history

        with patch.object(ak, "stock_hk_valuation_baidu", side_effect=RuntimeError("net")):
            result = fetch_pb_history("01336")
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# fetch_div_yield_history
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchDivYieldHistory:
    def test_returns_empty_when_price_nonpositive(self):
        from crawler import fetch_div_yield_history

        assert fetch_div_yield_history("01336", 0.0) == []
        assert fetch_div_yield_history("01336", -5.0) == []

    def test_returns_yield_list_on_success(self):
        import akshare as ak
        import pandas as pd
        from crawler import fetch_div_yield_history

        fake_df = pd.DataFrame({
            "除净日": ["2023-06-01", "2022-06-01", "2021-06-01"],
            "分红方案": [
                "每股派港币1.80元",
                "每股派港币1.60元",
                "每股派港币1.40元",
            ],
        })
        with patch.object(ak, "stock_hk_dividend_payout_em", return_value=fake_df):
            result = fetch_div_yield_history("01336", current_price=30.0, years=5)
        assert len(result) == 3
        assert result[0] == pytest.approx(1.80 / 30.0)

    def test_returns_empty_on_exception(self):
        import akshare as ak
        from crawler import fetch_div_yield_history

        with patch.object(ak, "stock_hk_dividend_payout_em", side_effect=RuntimeError("net")):
            result = fetch_div_yield_history("01336", 30.0)
        assert result == []



# 补充 get_valuation_label 剩余分支：低估/合理/偏高
class TestGetValuationLabelBranches:
    def test_label_undervalued_75_pct(self):
        from crawler import get_valuation_label
        label = get_valuation_label(80.0, "PB", 1.2, "x")
        assert "📈" in label
        assert "低估" in label

    def test_label_fair_50_pct(self):
        from crawler import get_valuation_label
        label = get_valuation_label(60.0, "PB", 1.5, "x")
        assert "⚖️" in label
        assert "合理" in label

    def test_label_high_25_pct(self):
        from crawler import get_valuation_label
        label = get_valuation_label(35.0, "PB", 2.0, "x")
        assert "📉" in label
        assert "偏高" in label

    def test_fetch_div_yield_history_empty_df(self):
        """df 为空时应返回空列表（覆盖 line 306）。"""
        import akshare as ak
        import pandas as pd
        from crawler import fetch_div_yield_history

        with patch.object(ak, "stock_hk_dividend_payout_em", return_value=pd.DataFrame()):
            result = fetch_div_yield_history("01336", current_price=30.0)
        assert result == []

    def test_fetch_div_yield_history_missing_date_column(self):
        """df 缺少除净日列时应返回空列表（覆盖 line 309）。"""
        import akshare as ak
        import pandas as pd
        from crawler import fetch_div_yield_history

        fake_df = pd.DataFrame({"分红方案": ["每股派港币1.80元"]})
        with patch.object(ak, "stock_hk_dividend_payout_em", return_value=fake_df):
            result = fetch_div_yield_history("01336", current_price=30.0)
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# v2.6 价格硬拦截测试（_HARD_BLOCK_ON_DIVERGE）
# ─────────────────────────────────────────────────────────────────────────────
class TestPriceOutlier:
    """验证三通道均发散时 fetch_realtime_price 返回 None（硬拦截）。"""

    def setup_method(self):
        import crawler as _crawler
        _crawler._last_known_prices.pop("01336", None)
        _crawler._last_known_prices["01336"] = 28.0  # 上次已知价

    def teardown_method(self):
        import crawler as _crawler
        _crawler._last_known_prices.pop("01336", None)

    def test_price_jump_hard_blocked_returns_none(self):
        """主通道 50 HKD (78% 偏差) + EM Web 也偏离 → 硬拦截返回 None。"""
        import crawler as _crawler
        _crawler._HARD_BLOCK_ON_DIVERGE = True
        # AkShare: 50.0 HKD（78% 偏差）；EM Web: 60.0 HKD（同样大偏差）
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "50.0"}])
        em_resp = MagicMock()
        em_resp.raise_for_status = MagicMock()
        em_resp.json.return_value = {"data": {"f43": 60000, "f57": "01336"}}
        with patch("akshare.stock_hk_spot_em", return_value=df):
            with patch("requests.get", return_value=em_resp):
                result = fetch_realtime_price("01336")
        assert result is None, "三通道均发散时应返回 None"

    def test_first_price_always_accepted(self):
        """首次采集（无历史价）不触发漂移检查，直接返回。"""
        import crawler as _crawler
        _crawler._last_known_prices.pop("01336", None)
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "52.5"}])
        with patch("akshare.stock_hk_spot_em", return_value=df):
            result = fetch_realtime_price("01336")
        assert result == pytest.approx(52.5)

    def test_third_channel_saves_when_em_within_threshold(self):
        """主通道偏离，但 EM Web 在阈值内 → 采用 EM Web 值（非 None）。"""
        import crawler as _crawler
        _crawler._last_known_prices["01336"] = 28.0
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "50.0"}])
        em_resp = MagicMock()
        em_resp.raise_for_status = MagicMock()
        # EM Web 返回 28.5，偏差 1.8% < 20% → 应采用
        em_resp.json.return_value = {"data": {"f43": 28500, "f57": "01336"}}
        with patch("akshare.stock_hk_spot_em", return_value=df):
            with patch("requests.get", return_value=em_resp):
                result = fetch_realtime_price("01336")
        assert result == pytest.approx(28.5)

    def test_hard_block_logs_error(self, caplog):
        """硬拦截时应记录 ERROR 级别日志。"""
        import logging
        import crawler as _crawler
        _crawler._HARD_BLOCK_ON_DIVERGE = True
        _crawler._last_known_prices["01336"] = 28.0
        df = pd.DataFrame([{"代码": "01336", "名称": "新华保险", "最新价": "50.0"}])
        em_resp = MagicMock()
        em_resp.raise_for_status = MagicMock()
        em_resp.json.return_value = {"data": {"f43": 60000, "f57": "01336"}}
        with caplog.at_level(logging.ERROR, logger="crawler"):
            with patch("akshare.stock_hk_spot_em", return_value=df):
                with patch("requests.get", return_value=em_resp):
                    fetch_realtime_price("01336")
        assert any("硬拦截" in r.message or "⛔" in r.message for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# v2.6 ROE 稳定性测试
# ─────────────────────────────────────────────────────────────────────────────
from crawler import compute_roe_stability, _safe_parse_pct


class TestROEStability:
    """验证 compute_roe_stability 正确识别 ROE 趋势恶化。"""

    def test_stable_roe_no_alert(self):
        """ROE 持续增长 → stable=True，无报警。"""
        history = [0.10, 0.12, 0.14, 0.15, 0.16]
        result = compute_roe_stability(history)
        assert result["stable"] is True
        assert result["alert"] == ""
        assert result["consecutive_decline"] == 0

    def test_three_consecutive_declines(self):
        """ROE 连续 3 年下滑 → stable=False，报警含"连续"。"""
        history = [0.15, 0.16, 0.14, 0.13, 0.12]
        result = compute_roe_stability(history, consecutive_years=3)
        assert result["stable"] is False
        assert result["consecutive_decline"] == 3
        assert "连续" in result["alert"]

    def test_peak_drop_over_20pct(self):
        """ROE 较峰值下跌 > 20% → stable=False，报警含"峰值"或"%"。"""
        history = [0.10, 0.20, 0.20, 0.15]  # peak=0.20, latest=0.15 → drop=25%
        result = compute_roe_stability(history, decline_threshold=0.20)
        assert result["stable"] is False
        assert result["max_drop"] == pytest.approx(0.25)
        assert "%" in result["alert"]

    def test_insufficient_history_is_stable(self):
        """历史不足 2 条 → stable=True（数据不足不报警）。"""
        assert compute_roe_stability([0.15])["stable"] is True
        assert compute_roe_stability([])["stable"] is True

    def test_safe_parse_pct_percent_string(self):
        """12.34% 字符串应解析为 0.1234。"""
        assert _safe_parse_pct("12.34%") == pytest.approx(0.1234)

    def test_safe_parse_pct_float(self):
        """纯浮点数 0.15 → 0.15（无转换）。"""
        assert _safe_parse_pct(0.15) == pytest.approx(0.15)

    def test_safe_parse_pct_none_returns_none(self):
        """None 输入返回 None。"""
        assert _safe_parse_pct(None) is None

    def test_safe_parse_pct_invalid_returns_none(self):
        """无效字符串返回 None。"""
        assert _safe_parse_pct("N/A") is None


# ─────────────────────────────────────────────────────────────────────────────
# v2.6 估值分位精度测试
# ─────────────────────────────────────────────────────────────────────────────
class TestPercentileAccuracy:
    """用固定数列验证 compute_percentile 的分位计算精度。

    compute_percentile 语义：
    - higher_is_better=True（股息率）：count(v <= current) / N * 100
    - higher_is_better=False（PB）：count(v >= current) / N * 100
    - 数据不足 4 条时返回 -1.0（即 "无数据" 标记）
    """

    def test_median_position(self):
        """当前值约为历史中位数时，分位应在 45~55 之间。"""
        from crawler import compute_percentile
        history = list(range(1, 21))  # 1..20，共 20 个，高优先
        pct = compute_percentile(10.5, history, higher_is_better=True)
        assert 45 <= pct <= 55, f"Expected ~50, got {pct}"

    def test_extreme_high_100pct(self):
        """高于或等于所有历史值 → 100 分位（higher_is_better=True）。"""
        from crawler import compute_percentile
        history = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert compute_percentile(5.0, history, higher_is_better=True) == 100

    def test_below_all_history_is_0pct(self):
        """低于全部历史值 → 0 分位（higher_is_better=True）。"""
        from crawler import compute_percentile
        history = [1.0, 2.0, 3.0, 4.0, 5.0]
        # 0.5 < min(history)，count=0，结果应为 0
        assert compute_percentile(0.5, history, higher_is_better=True) == 0

    def test_lower_is_better_inverted(self):
        """PB 低优先：低于全部历史 → 100；高于全部历史 → 0。"""
        from crawler import compute_percentile
        # 需要 >= 4 个数据点，否则返回 -1
        history = [10.0, 20.0, 30.0, 40.0]
        assert compute_percentile(9.0, history, higher_is_better=False) == 100   # below all → count(v>=9)=4=100%
        assert compute_percentile(41.0, history, higher_is_better=False) == 0    # above all → count(v>=41)=0=0%

    def test_out_of_range_capped(self):
        """超出历史范围的值应落在 [0, 100] 内，不越界。"""
        from crawler import compute_percentile
        history = [5.0, 10.0, 15.0, 20.0]  # 4 个点
        high = compute_percentile(100.0, history, higher_is_better=True)
        low = compute_percentile(0.0, history, higher_is_better=True)
        assert 0 <= high <= 100
        assert 0 <= low <= 100

    def test_insufficient_data_returns_minus_one(self):
        """不足 4 条历史时返回 -1.0（无数据标记）。"""
        from crawler import compute_percentile
        assert compute_percentile(5.0, [1.0, 2.0, 3.0], higher_is_better=True) == -1.0
