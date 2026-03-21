"""
tests/ut/test_magic_formula.py — 神奇公式扫描器单元测试

Mock 所有 AkShare API，仅测试纯计算逻辑与数据流。
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import requests

import magic_formula
from magic_formula import (
    CACHE_MAX_HOURS,
    StockScore,
    _extract_value,
    _is_financial_industry,
    _safe_float,
    compute_ey,
    compute_roc,
    fetch_ah_premium_map,
    fetch_financial_codes_a,
    fetch_financials_a,
    fetch_financials_h,
    fetch_universe_a,
    fetch_universe_h,
    is_cache_fresh,
    load_cache,
    rank_and_select,
    save_cache,
    scan_magic_formula,
)


# ─────────────────────────────────────────────────────────────────────────────
# StockScore
# ─────────────────────────────────────────────────────────────────────────────
class TestStockScore:
    def test_to_dict_contains_all_fields(self):
        s = StockScore(code="000001", name="测试", market="A", price=10.0, roc=0.2, ey=0.1)
        d = s.to_dict()
        assert d["code"] == "000001"
        assert d["roc"] == pytest.approx(0.2)
        assert d["data_quality"] == "full"

    def test_from_dict_roundtrip(self):
        s = StockScore(
            code="01336", name="新华保险", market="H", price=28.0,
            roc=0.25, ey=0.12, roc_rank=3, ey_rank=5,
            combined_rank=8, ah_discount_pct=-15.0, industry="保险",
            ebit=1000.0, ev=8000.0, market_cap=7000.0, data_quality="approx",
        )
        restored = StockScore.from_dict(s.to_dict())
        assert restored.code == "01336"
        assert restored.ah_discount_pct == pytest.approx(-15.0)
        assert restored.data_quality == "approx"

    def test_from_dict_ignores_unknown_keys(self):
        d = StockScore(code="000002", name="万科", market="A", price=8.0, roc=0.1, ey=0.05).to_dict()
        d["future_field"] = "ignored"
        restored = StockScore.from_dict(d)
        assert restored.code == "000002"


# ─────────────────────────────────────────────────────────────────────────────
# compute_roc
# ─────────────────────────────────────────────────────────────────────────────
class TestComputeRoc:
    def test_basic_calculation(self):
        # EBIT=100, NWC=200, fixed=300 → ROC = 100/500 = 0.2
        assert compute_roc(100.0, 200.0, 300.0) == pytest.approx(0.2)

    def test_zero_denominator_returns_none(self):
        assert compute_roc(100.0, 0.0, 0.0) is None

    def test_negative_denominator_returns_none(self):
        assert compute_roc(100.0, -400.0, 100.0) is None

    def test_large_roc(self):
        # NWC 极小时 ROC 仍正常计算
        result = compute_roc(100.0, 1.0, 99.0)
        assert result == pytest.approx(1.0)

    def test_negative_nwc_but_positive_total(self):
        # NWC=-50, fixed=200 → denom=150 > 0
        result = compute_roc(100.0, -50.0, 200.0)
        assert result == pytest.approx(100.0 / 150.0)


# ─────────────────────────────────────────────────────────────────────────────
# compute_ey
# ─────────────────────────────────────────────────────────────────────────────
class TestComputeEy:
    def test_basic_calculation(self):
        # EBIT=100, EV=1000 → EY = 0.1
        assert compute_ey(100.0, 1000.0) == pytest.approx(0.1)

    def test_zero_ev_returns_none(self):
        assert compute_ey(100.0, 0.0) is None

    def test_negative_ev_returns_none(self):
        assert compute_ey(100.0, -500.0) is None

    def test_high_yield(self):
        assert compute_ey(200.0, 1000.0) == pytest.approx(0.2)


# ─────────────────────────────────────────────────────────────────────────────
# rank_and_select
# ─────────────────────────────────────────────────────────────────────────────
class TestRankAndSelect:
    def _make(self, code, roc, ey) -> StockScore:
        return StockScore(code=code, name=code, market="A", price=10.0, roc=roc, ey=ey)

    def test_assigns_roc_rank_correctly(self):
        stocks = [self._make("A", 0.1, 0.05), self._make("B", 0.3, 0.04)]
        result = rank_and_select(stocks, top_n=2)
        b = next(s for s in result if s.code == "B")
        assert b.roc_rank == 1  # highest ROC

    def test_assigns_ey_rank_correctly(self):
        stocks = [self._make("A", 0.1, 0.15), self._make("B", 0.3, 0.04)]
        result = rank_and_select(stocks, top_n=2)
        a = next(s for s in result if s.code == "A")
        assert a.ey_rank == 1  # highest EY

    def test_combined_rank_is_sum(self):
        stocks = [self._make("A", 0.1, 0.15), self._make("B", 0.3, 0.04)]
        result = rank_and_select(stocks, top_n=2)
        for s in result:
            assert s.combined_rank == s.roc_rank + s.ey_rank

    def test_top_n_limits_output(self):
        stocks = [self._make(str(i), 0.1 + i * 0.01, 0.05 + i * 0.01) for i in range(10)]
        result = rank_and_select(stocks, top_n=3)
        assert len(result) == 3

    def test_returns_all_when_fewer_than_top_n(self):
        stocks = [self._make("A", 0.2, 0.1), self._make("B", 0.3, 0.08)]
        result = rank_and_select(stocks, top_n=30)
        assert len(result) == 2

    def test_empty_input_returns_empty(self):
        assert rank_and_select([], top_n=30) == []

    def test_best_combined_rank_comes_first(self):
        # A: ROC rank 2, EY rank 1 → combined 3
        # B: ROC rank 1, EY rank 2 → combined 3
        # C: ROC rank 3, EY rank 3 → combined 6
        stocks = [
            self._make("A", 0.20, 0.15),
            self._make("B", 0.25, 0.10),
            self._make("C", 0.05, 0.02),
        ]
        result = rank_and_select(stocks, top_n=3)
        # C should be last
        assert result[-1].code == "C"


# ─────────────────────────────────────────────────────────────────────────────
# _is_financial_industry
# ─────────────────────────────────────────────────────────────────────────────
class TestIsFinancialIndustry:
    def test_bank_is_financial(self):
        assert _is_financial_industry("招商银行") is True

    def test_insurance_is_financial(self):
        assert _is_financial_industry("平安保险") is True

    def test_securities_is_financial(self):
        assert _is_financial_industry("中信证券") is True

    def test_tech_is_not_financial(self):
        assert _is_financial_industry("腾讯控股") is False

    def test_empty_string(self):
        assert _is_financial_industry("") is False


# ─────────────────────────────────────────────────────────────────────────────
# _safe_float
# ─────────────────────────────────────────────────────────────────────────────
class TestSafeFloat:
    def test_converts_int(self):
        assert _safe_float(10) == pytest.approx(10.0)

    def test_converts_string_float(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_returns_none_for_none(self):
        assert _safe_float(None) is None

    def test_returns_none_for_invalid_string(self):
        assert _safe_float("abc") is None

    def test_returns_none_for_nan(self):
        import math
        assert _safe_float(float("nan")) is None

    def test_converts_negative(self):
        assert _safe_float(-5.5) == pytest.approx(-5.5)


# ─────────────────────────────────────────────────────────────────────────────
# _extract_value
# ─────────────────────────────────────────────────────────────────────────────
class TestExtractValue:
    def test_format_a_index_based(self):
        df = pd.DataFrame({"2023-12": [1234.5, 999.0]}, index=["货币资金", "流动资产合计"])
        assert _extract_value(df, ["货币资金"]) == pytest.approx(1234.5)

    def test_format_b_first_column_label(self):
        df = pd.DataFrame({"科目": ["货币资金", "流动资产合计"], "2023-12": [500.0, 2000.0]})
        assert _extract_value(df, ["货币资金"]) == pytest.approx(500.0)

    def test_returns_none_when_item_not_found(self):
        df = pd.DataFrame({"2023-12": [100.0]}, index=["其他科目"])
        assert _extract_value(df, ["货币资金"]) is None

    def test_returns_none_for_empty_df(self):
        assert _extract_value(pd.DataFrame(), ["货币资金"]) is None

    def test_returns_none_for_none_df(self):
        assert _extract_value(None, ["货币资金"]) is None

    def test_tries_second_candidate(self):
        df = pd.DataFrame({"2023-12": [777.0]}, index=["现金及现金等价物"])
        assert _extract_value(df, ["货币资金", "现金及现金等价物"]) == pytest.approx(777.0)

    def test_skips_dash_placeholder(self):
        df = pd.DataFrame({"2023-12": ["--"], "2023-09": [888.0]}, index=["货币资金"])
        assert _extract_value(df, ["货币资金"]) == pytest.approx(888.0)


# ─────────────────────────────────────────────────────────────────────────────
# fetch_financial_codes_a
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchFinancialCodesA:
    def test_returns_frozenset_of_codes(self):
        fake_df = pd.DataFrame({"代码": ["000001", "600036"]})
        with patch("magic_formula.ak.stock_board_industry_cons_em", return_value=fake_df):
            codes = fetch_financial_codes_a()
        assert "000001" in codes
        assert isinstance(codes, frozenset)

    def test_returns_static_fallback_on_all_exceptions(self):
        """v2.6.2: 全部行业接口失败时回退到静态兜底（非空），不再返回空集合。"""
        from magic_formula import _STATIC_FINANCIAL_CODES
        with patch("magic_formula.ak.stock_board_industry_cons_em", side_effect=RuntimeError("net")):
            codes = fetch_financial_codes_a()
        assert codes == _STATIC_FINANCIAL_CODES
        assert len(codes) >= 30

    def test_zero_pads_short_codes(self):
        fake_df = pd.DataFrame({"代码": ["1", "600036"]})
        with patch("magic_formula.ak.stock_board_industry_cons_em", return_value=fake_df):
            codes = fetch_financial_codes_a()
        assert "000001" in codes


# ─────────────────────────────────────────────────────────────────────────────
# fetch_universe_a
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchUniverseA:
    def _fake_spot(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_filters_st_stocks(self):
        df = self._fake_spot([
            {"代码": "000001", "名称": "*ST银行", "最新价": 5.0, "总市值": 5e9, "市盈率-动态": 10.0},
            {"代码": "000002", "名称": "万科", "最新价": 8.0, "总市值": 30e9, "市盈率-动态": 12.0},
        ])
        with patch("magic_formula.ak.stock_zh_a_spot_em", return_value=df):
            result = fetch_universe_a(frozenset())
        assert all(s["code"] != "000001" for s in result)
        assert any(s["code"] == "000002" for s in result)

    def test_filters_financial_codes(self):
        df = self._fake_spot([
            {"代码": "600036", "名称": "招商银行", "最新价": 30.0, "总市值": 100e9, "市盈率-动态": 6.0},
            {"代码": "000002", "名称": "万科", "最新价": 8.0, "总市值": 30e9, "市盈率-动态": 12.0},
        ])
        with patch("magic_formula.ak.stock_zh_a_spot_em", return_value=df):
            result = fetch_universe_a(frozenset(["600036"]))
        assert all(s["code"] != "600036" for s in result)

    def test_filters_micro_caps(self):
        df = self._fake_spot([
            {"代码": "300001", "名称": "小盘股", "最新价": 3.0, "总市值": 1e9, "市盈率-动态": 15.0},
        ])
        with patch("magic_formula.ak.stock_zh_a_spot_em", return_value=df):
            result = fetch_universe_a(frozenset())
        assert result == []

    def test_filters_negative_pe(self):
        df = self._fake_spot([
            {"代码": "000003", "名称": "亏损股", "最新价": 5.0, "总市值": 5e9, "市盈率-动态": -10.0},
        ])
        with patch("magic_formula.ak.stock_zh_a_spot_em", return_value=df):
            result = fetch_universe_a(frozenset())
        assert result == []

    def test_returns_empty_on_api_failure(self):
        with patch("magic_formula.ak.stock_zh_a_spot_em", side_effect=RuntimeError("net")):
            result = fetch_universe_a(frozenset())
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# fetch_universe_h
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchUniverseH:
    def test_filters_financial_names(self):
        df = pd.DataFrame([
            {"代码": "00939", "名称": "建设银行", "最新价": 6.0, "总市值": 10e9, "市盈率": 5.0, "市净率": 0.5},
            {"代码": "02800", "名称": "盈富基金", "最新价": 80.0, "总市值": 50e9, "市盈率": 15.0, "市净率": 1.2},
        ])
        with patch("magic_formula.ak.stock_hk_spot_em", return_value=df):
            result = fetch_universe_h()
        assert all(s["code"] != "00939" for s in result)

    def test_filters_micro_caps_h(self):
        df = pd.DataFrame([
            {"代码": "09999", "名称": "小盘H", "最新价": 1.0, "总市值": 1e7, "市盈率": 10.0, "市净率": 1.0},
        ])
        with patch("magic_formula.ak.stock_hk_spot_em", return_value=df):
            result = fetch_universe_h()
        assert result == []

    def test_returns_empty_on_api_failure(self):
        with patch("magic_formula.ak.stock_hk_spot_em", side_effect=RuntimeError("net")):
            result = fetch_universe_h()
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# fetch_financials_a
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchFinancialsA:
    def _make_stock(self) -> dict:
        return {"code": "000001", "name": "测试股", "price": 10.0, "market_cap": 50e9, "market": "A"}

    def _bs(self, cash=5e9, ca=20e9, cl=10e9, st=2e9, lt=3e9, fa=8e9) -> pd.DataFrame:
        return pd.DataFrame({
            "2023-12-31": [cash, ca, cl, st, lt, fa],
        }, index=["货币资金", "流动资产合计", "流动负债合计", "短期借款", "长期借款", "固定资产"])

    def _pl(self, op=4e9, fe=0.5e9) -> pd.DataFrame:
        return pd.DataFrame({
            "2023-12-31": [op, fe],
        }, index=["营业利润", "财务费用"])

    def test_returns_stock_score_on_valid_data(self):
        stock = self._make_stock()
        with patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=self._bs()), \
             patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=self._pl()), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_a(stock)
        assert result is not None
        assert result.code == "000001"
        assert result.roc > 0
        assert result.ey > 0
        assert result.data_quality == "full"

    def test_returns_none_on_empty_balance_sheet(self):
        stock = self._make_stock()
        with patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=pd.DataFrame()), \
             patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=self._pl()), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_a(stock)
        assert result is None

    def test_returns_none_on_empty_income_statement(self):
        stock = self._make_stock()
        with patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=self._bs()), \
             patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=pd.DataFrame()), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_a(stock)
        assert result is None

    def test_returns_none_when_ebit_zero_or_negative(self):
        stock = self._make_stock()
        with patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=self._bs()), \
             patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=self._pl(op=-1e9, fe=0.0)), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_a(stock)
        assert result is None

    def test_ebit_includes_financial_expense(self):
        """EBIT = 营业利润 + max(0, 财务费用)"""
        stock = self._make_stock()
        with patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=self._bs()), \
             patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=self._pl(op=3e9, fe=1e9)), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_a(stock)
        # EBIT = 3e9 + 1e9 = 4e9
        assert result is not None
        assert result.ebit == pytest.approx(4e9)

    def test_returns_none_on_api_exception(self):
        stock = self._make_stock()
        with patch("magic_formula.ak.stock_balance_sheet_by_report_em", side_effect=RuntimeError("net")), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_a(stock)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# fetch_financials_h (近似法)
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchFinancialsH:
    def _make_h_stock(self, pe=12.0, pb=1.5) -> dict:
        return {
            "code": "02800", "name": "盈富基金", "price": 80.0,
            "market_cap": 50e9, "pe": pe, "pb": pb, "market": "H",
        }

    def test_fallback_approx_returns_score(self):
        stock = self._make_h_stock()
        # 强制 H 股财报接口失败 → 退化 PE 近似
        with patch("magic_formula.ak.stock_financial_hk_report_em", side_effect=RuntimeError("no api")), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_h(stock)
        assert result is not None
        assert result.ey > 0
        assert result.data_quality == "approx"

    def test_returns_none_when_pe_nonpositive(self):
        stock = self._make_h_stock(pe=-5.0)
        with patch("magic_formula.ak.stock_financial_hk_report_em", side_effect=RuntimeError("no api")), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_h(stock)
        assert result is None

    def test_returns_none_when_pb_nonpositive(self):
        stock = self._make_h_stock(pb=0.0)
        with patch("magic_formula.ak.stock_financial_hk_report_em", side_effect=RuntimeError("no api")), \
             patch("magic_formula.time.sleep"):
            result = fetch_financials_h(stock)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# fetch_ah_premium_map
# ─────────────────────────────────────────────────────────────────────────────
class TestFetchAhPremiumMap:
    def test_returns_negative_discount(self):
        fake_df = pd.DataFrame({
            "H股代码": ["00939"],
            "AH溢价率": [20.0],  # H 股比 A 股便宜 20%
        })
        with patch("magic_formula.ak.stock_zh_ah_spot_em", return_value=fake_df):
            result = fetch_ah_premium_map()
        assert result["00939"] == pytest.approx(-20.0)

    def test_returns_empty_on_exception(self):
        with patch("magic_formula.ak.stock_zh_ah_spot_em", side_effect=RuntimeError("net")):
            result = fetch_ah_premium_map()
        assert result == {}

    def test_returns_empty_on_empty_df(self):
        with patch("magic_formula.ak.stock_zh_ah_spot_em", return_value=pd.DataFrame()):
            result = fetch_ah_premium_map()
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# 缓存管理
# ─────────────────────────────────────────────────────────────────────────────
class TestCacheOperations:
    def test_save_and_load_roundtrip(self, tmp_path):
        cache_file = tmp_path / "test_cache.json"
        data = {"top_stocks": [{"code": "000001"}], "scanned_count": 1, "universe_size": 100}
        import magic_formula as mf
        orig_path = mf.CACHE_PATH
        mf.CACHE_PATH = str(cache_file)
        try:
            save_cache(data)
            loaded = load_cache()
            assert loaded is not None
            assert loaded["top_stocks"][0]["code"] == "000001"
            assert "cached_at" in loaded
        finally:
            mf.CACHE_PATH = orig_path

    def test_load_returns_none_when_file_missing(self, tmp_path):
        import magic_formula as mf
        orig_path = mf.CACHE_PATH
        mf.CACHE_PATH = str(tmp_path / "nonexistent.json")
        try:
            assert load_cache() is None
        finally:
            mf.CACHE_PATH = orig_path

    def test_is_cache_fresh_recent(self):
        cache = {"cached_at": datetime.now().isoformat()}
        assert is_cache_fresh(cache, max_hours=18) is True

    def test_is_cache_fresh_stale(self):
        old_time = datetime.now() - timedelta(hours=20)
        cache = {"cached_at": old_time.isoformat()}
        assert is_cache_fresh(cache, max_hours=18) is False

    def test_is_cache_fresh_missing_timestamp(self):
        assert is_cache_fresh({}, max_hours=18) is False

    def test_is_cache_fresh_invalid_timestamp(self):
        assert is_cache_fresh({"cached_at": "invalid"}, max_hours=18) is False


# ─────────────────────────────────────────────────────────────────────────────
# scan_magic_formula (集成测试，全 mock)
# ─────────────────────────────────────────────────────────────────────────────
class TestScanMagicFormula:
    def _bs(self) -> pd.DataFrame:
        return pd.DataFrame({
            "2023-12-31": [5e9, 20e9, 10e9, 2e9, 3e9, 8e9],
        }, index=["货币资金", "流动资产合计", "流动负债合计", "短期借款", "长期借款", "固定资产"])

    def _pl(self) -> pd.DataFrame:
        return pd.DataFrame({
            "2023-12-31": [4e9, 0.5e9],
        }, index=["营业利润", "财务费用"])

    def _spot_a(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"代码": "000001", "名称": "股票A", "最新价": 10.0, "总市值": 50e9, "市盈率-动态": 12.0},
            {"代码": "000002", "名称": "股票B", "最新价": 8.0, "总市值": 30e9, "市盈率-动态": 15.0},
        ])

    def test_returns_top_stocks(self, tmp_path):
        import magic_formula as mf
        orig_path = mf.CACHE_PATH
        mf.CACHE_PATH = str(tmp_path / "scan_cache.json")
        try:
            with patch("magic_formula.ak.stock_board_industry_cons_em", return_value=pd.DataFrame({"代码": []})), \
                 patch("magic_formula.ak.stock_zh_a_spot_em", return_value=self._spot_a()), \
                 patch("magic_formula.ak.stock_hk_spot_em", return_value=pd.DataFrame()), \
                 patch("magic_formula.ak.stock_zh_ah_spot_em", side_effect=RuntimeError("net")), \
                 patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=self._bs()), \
                 patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=self._pl()), \
                 patch("magic_formula.time.sleep"):
                result = scan_magic_formula(top_n=5, include_h=False)
            assert "top_stocks" in result
            assert result["scanned_count"] >= 1
            assert len(result["top_stocks"]) <= 5
        finally:
            mf.CACHE_PATH = orig_path

    def test_saves_cache_after_scan(self, tmp_path):
        import magic_formula as mf
        orig_path = mf.CACHE_PATH
        cache_file = tmp_path / "scan_cache2.json"
        mf.CACHE_PATH = str(cache_file)
        try:
            with patch("magic_formula.ak.stock_board_industry_cons_em", return_value=pd.DataFrame({"代码": []})), \
                 patch("magic_formula.ak.stock_zh_a_spot_em", return_value=self._spot_a()), \
                 patch("magic_formula.ak.stock_hk_spot_em", return_value=pd.DataFrame()), \
                 patch("magic_formula.ak.stock_zh_ah_spot_em", side_effect=RuntimeError("net")), \
                 patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=self._bs()), \
                 patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=self._pl()), \
                 patch("magic_formula.time.sleep"):
                scan_magic_formula(top_n=5, include_h=False)
            assert cache_file.exists()
        finally:
            mf.CACHE_PATH = orig_path

    def test_progress_callback_called(self, tmp_path):
        import magic_formula as mf
        orig_path = mf.CACHE_PATH
        mf.CACHE_PATH = str(tmp_path / "progress_cache.json")
        calls: list[tuple] = []

        def cb(pct, msg):
            calls.append((pct, msg))

        try:
            with patch("magic_formula.ak.stock_board_industry_cons_em", return_value=pd.DataFrame({"代码": []})), \
                 patch("magic_formula.ak.stock_zh_a_spot_em", return_value=self._spot_a()), \
                 patch("magic_formula.ak.stock_hk_spot_em", return_value=pd.DataFrame()), \
                 patch("magic_formula.ak.stock_zh_ah_spot_em", side_effect=RuntimeError("net")), \
                 patch("magic_formula.ak.stock_balance_sheet_by_report_em", return_value=self._bs()), \
                 patch("magic_formula.ak.stock_profit_sheet_by_report_em", return_value=self._pl()), \
                 patch("magic_formula.time.sleep"):
                scan_magic_formula(top_n=5, include_h=False, progress_callback=cb)
            assert len(calls) > 0
            # Last call should be 100%
            assert calls[-1][0] == pytest.approx(1.0)
        finally:
            mf.CACHE_PATH = orig_path

    def test_retries_without_proxy_when_universe_empty_and_proxy_set(
        self, monkeypatch, tmp_path
    ):
        """若宇宙为空且检测到代理配置，应切换直连重试第二次。"""
        import magic_formula as mf

        orig_path = mf.CACHE_PATH
        mf.CACHE_PATH = str(tmp_path / "cache.json")
        monkeypatch.setenv("HTTP_PROXY", "http://broken-proxy:8080")
        call_count = {"n": 0}

        def _side_effect(financial_codes):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return []  # 第一次：代理故障，宇宙为空
            return [
                {
                    "code": "000001",
                    "name": "股票A",
                    "market": "A",
                    "current_price": 10.0,
                    "total_cap": 50e9,
                    "pe": 12.0,
                }
            ]

        try:
            with patch("magic_formula.fetch_universe_a", side_effect=_side_effect),                     patch("magic_formula.fetch_financial_codes_a", return_value=[]),                     patch("magic_formula.fetch_financials_a", return_value=None),                     patch("magic_formula.fetch_ah_premium_map", return_value={}),                     patch("magic_formula.rank_and_select", return_value=[]):
                result = scan_magic_formula(include_h=False, top_n=1)
            assert call_count["n"] == 2, "应触发直连重试（fetch_universe_a 被调用两次）"
            assert result["universe_size"] == 1
        finally:
            mf.CACHE_PATH = orig_path

    def test_no_retry_when_universe_empty_without_proxy(self, monkeypatch, tmp_path):
        """若宇宙为空但无代理配置，不应触发直连重试。"""
        import magic_formula as mf

        orig_path = mf.CACHE_PATH
        mf.CACHE_PATH = str(tmp_path / "cache.json")
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(k, raising=False)
        call_count = {"n": 0}

        def _side_effect(financial_codes):
            call_count["n"] += 1
            return []

        try:
            with patch("magic_formula.fetch_universe_a", side_effect=_side_effect),                     patch("magic_formula.fetch_financial_codes_a", return_value=[]):
                result = scan_magic_formula(include_h=False, top_n=1)
            assert call_count["n"] == 1, "无代理时不应重试"
            assert result["universe_size"] == 0
        finally:
            mf.CACHE_PATH = orig_path




# ─────────────────────────────────────────────────────────────────────────────
# v2.6.2 代理免疫力测试
# ─────────────────────────────────────────────────────────────────────────────

from unittest.mock import MagicMock, call, patch as _patch

import requests as _requests
from requests.exceptions import ProxyError as _ProxyError

from magic_formula import (
    _proxy_resilient_get,
    _STATIC_FINANCIAL_CODES,
    fetch_financial_codes_a,
)


class TestProxyResilientGet:
    """验证 _proxy_resilient_get 的重试与直连切换逻辑（使用 _SESSION.get）。"""

    def test_succeeds_on_first_attempt(self, monkeypatch):
        """首次请求成功时直接返回，无重试。"""
        mock_resp = MagicMock(status_code=200)
        mock_get = MagicMock(return_value=mock_resp)
        monkeypatch.setattr(magic_formula._SESSION, "get", mock_get)
        resp = _proxy_resilient_get("http://example.com/data")
        assert resp.status_code == 200
        assert mock_get.call_count == 1

    def test_retries_on_proxy_error_then_succeeds(self, monkeypatch):
        """首次 ProxyError，第二次成功（切直连）。"""
        mock_resp = MagicMock(status_code=200)
        mock_get = MagicMock(side_effect=[_ProxyError("proxy down"), mock_resp])
        monkeypatch.setattr(magic_formula._SESSION, "get", mock_get)
        resp = _proxy_resilient_get("http://example.com/data")
        assert resp.status_code == 200
        assert mock_get.call_count == 2
        # 第二次调用应强制直连（proxies={'http': None, 'https': None}）
        second_call_kwargs = mock_get.call_args_list[1][1]
        assert second_call_kwargs.get("proxies") == {"http": None, "https": None}

    def test_raises_after_all_retries_exhausted(self, monkeypatch):
        """所有重试耗尽后，向上抛出最后一次异常。"""
        mock_get = MagicMock(side_effect=_ProxyError("persistent proxy error"))
        monkeypatch.setattr(magic_formula._SESSION, "get", mock_get)
        import pytest as _pytest
        with _pytest.raises(_ProxyError):
            _proxy_resilient_get("http://example.com/data")

    def test_timeout_set_to_five_seconds(self, monkeypatch):
        """默认超时必须是 5 秒。"""
        mock_resp = MagicMock(status_code=200)
        mock_get = MagicMock(return_value=mock_resp)
        monkeypatch.setattr(magic_formula._SESSION, "get", mock_get)
        _proxy_resilient_get("http://example.com/data")
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs.get("timeout") == 5


class TestFinancialCodesStaticFallback:
    """验证黑名单网络全失败时回退到静态兜底。"""

    def test_static_fallback_when_all_boards_fail(self):
        """所有行业接口失败时，返回静态兜底集合。"""
        import akshare as ak
        with _patch.object(
            ak, "stock_board_industry_cons_em", side_effect=Exception("proxy error")
        ):
            result = fetch_financial_codes_a()
        # 应回退到静态集合（非空）
        assert result == _STATIC_FINANCIAL_CODES
        assert len(result) >= 30

    def test_partial_success_uses_network_data(self):
        """部分行业成功时，使用网络获取的数据（不回退静态）。"""
        import pandas as pd
        import akshare as ak
        mock_df = pd.DataFrame({"代码": ["600036", "601166"]})

        call_count = [0]

        def side_effect(symbol):
            call_count[0] += 1
            if symbol == "银行":
                return mock_df
            raise Exception("proxy error")

        with _patch.object(ak, "stock_board_industry_cons_em", side_effect=side_effect):
            result = fetch_financial_codes_a()

        # 有部分成功 → 返回网络数据，不是静态兜底
        assert result != _STATIC_FINANCIAL_CODES
        assert "600036" in result



class TestNoProxyCtx:
    """验证 _no_proxy_ctx 能正确清除并恢复代理环境变量。"""

    def test_clears_proxy_env_vars_during_execution(self):
        """执行期间代理环境变量应被清除。"""
        import os
        from magic_formula import _no_proxy_ctx

        os.environ["HTTPS_PROXY"] = "http://proxy.example.com:8080"
        try:
            with _no_proxy_ctx():
                assert "HTTPS_PROXY" not in os.environ
                assert "HTTP_PROXY" not in os.environ
        finally:
            os.environ.pop("HTTPS_PROXY", None)

    def test_restores_proxy_env_vars_after_execution(self):
        """退出 context manager 后代理变量必须恢复。"""
        import os
        from magic_formula import _no_proxy_ctx

        os.environ["HTTP_PROXY"] = "http://proxy.example.com:3128"
        os.environ["HTTPS_PROXY"] = "http://proxy.example.com:3128"
        try:
            with _no_proxy_ctx():
                pass
            assert os.environ["HTTP_PROXY"] == "http://proxy.example.com:3128"
            assert os.environ["HTTPS_PROXY"] == "http://proxy.example.com:3128"
        finally:
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("HTTPS_PROXY", None)

    def test_restores_on_exception(self):
        """即使内部抛异常，代理变量也必须恢复（RAII 保证）。"""
        import os
        from magic_formula import _no_proxy_ctx

        os.environ["HTTPS_PROXY"] = "http://proxy.example.com:8080"
        try:
            with _no_proxy_ctx():
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass
        finally:
            restored = os.environ.pop("HTTPS_PROXY", None)
            assert restored == "http://proxy.example.com:8080", "代理变量未恢复"

    def test_no_proxy_set_is_noop(self):
        """无代理环境变量时，context manager 应静默无副作用。"""
        import os
        from magic_formula import _no_proxy_ctx

        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ.pop(k, None)

        with _no_proxy_ctx():
            assert "HTTPS_PROXY" not in os.environ
        # 退出后仍无代理变量（不应凭空创建）
        assert "HTTPS_PROXY" not in os.environ


# ─────────────────────────────────────────────────────────────────────────────
# TestPersistentSession
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistentSession:
    """验证 _SESSION 为持久化 Session 且已配置指数退避重试 Adapter。"""

    def test_session_is_requests_session(self):
        """_SESSION 必须是 requests.Session 实例。"""
        from requests.adapters import HTTPAdapter
        assert isinstance(magic_formula._SESSION, requests.Session)

    def test_session_has_retry_adapter_for_http(self):
        """http:// 必须使用带重试的 HTTPAdapter，total=5。"""
        from requests.adapters import HTTPAdapter
        adapter = magic_formula._SESSION.get_adapter("http://example.com")
        assert isinstance(adapter, HTTPAdapter)
        assert adapter.max_retries.total == 5

    def test_session_has_retry_adapter_for_https(self):
        """https:// 必须使用带重试的 HTTPAdapter，total=5。"""
        from requests.adapters import HTTPAdapter
        adapter = magic_formula._SESSION.get_adapter("https://example.com")
        assert isinstance(adapter, HTTPAdapter)
        assert adapter.max_retries.total == 5


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckNetworkConnectivity
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckNetworkConnectivity:
    """验证 check_network_connectivity 在各种网络状态下的行为。"""

    def test_returns_true_when_seed_responds_200(self, monkeypatch):
        """种子页面返回 200 时，应返回 True。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        monkeypatch.setattr(magic_formula._SESSION, "get", MagicMock(return_value=mock_resp))
        assert magic_formula.check_network_connectivity() is True

    def test_returns_false_when_all_attempts_fail(self, monkeypatch):
        """两次尝试（代理+直连）均抛异常时，应返回 False。"""
        monkeypatch.setattr(
            magic_formula._SESSION,
            "get",
            MagicMock(side_effect=Exception("connection refused")),
        )
        assert magic_formula.check_network_connectivity() is False

    def test_returns_true_on_direct_connect_fallback(self, monkeypatch):
        """代理失败但直连成功时，应返回 True。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("proxy dead")
            return mock_resp

        monkeypatch.setattr(magic_formula._SESSION, "get", side_effect)
        assert magic_formula.check_network_connectivity() is True


# ─────────────────────────────────────────────────────────────────────────────
# TestFetchDelay
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchDelay:
    """验证 _FETCH_DELAY 已提升到 WAF 安全阈值（≥1.5s）。"""

    def test_fetch_delay_is_throttled(self):
        assert magic_formula._FETCH_DELAY >= 1.5, (
            f"_FETCH_DELAY={magic_formula._FETCH_DELAY} 应 ≥ 1.5 秒（WAF 降频要求）"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TestNetworkGating
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkGating:
    """验证 scan_magic_formula 在网络不可用时快速返回 error=network_unavailable。"""

    def test_returns_network_unavailable_when_connectivity_fails(self, monkeypatch):
        """网络不可通时，scan 应立即返回 error=network_unavailable，不触发扫描。"""
        monkeypatch.setattr(
            "magic_formula.check_network_connectivity",
            lambda: False,
        )
        result = scan_magic_formula(progress_callback=None)
        assert result.get("error") == "network_unavailable"
        assert result["universe_size"] == 0
        assert result["top_stocks"] == []
