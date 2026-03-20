"""
tests/ut/test_monitor.py - monitor.py 单元测试
测试交易时段判断、节假日识别、is_trading_day、load/save_state 以及 run_once 主逻辑。
"""
import json
import os
import pytest
from datetime import datetime, date
from unittest.mock import patch, MagicMock

import monitor
from monitor import (
    is_trading_day,
    is_in_trading_session,
    load_state,
    save_state,
    build_engines,
    run_once,
)
from engine import GridEngine


# ─────────────────────────────────────────────────────────────────────────────
# is_trading_day
# ─────────────────────────────────────────────────────────────────────────────
class TestIsTradingDay:
    def test_saturday_is_not_trading(self):
        saturday = date(2025, 1, 4)  # 周六
        assert is_trading_day(saturday) is False

    def test_sunday_is_not_trading(self):
        sunday = date(2025, 1, 5)  # 周日
        assert is_trading_day(sunday) is False

    def test_normal_weekday_is_trading(self):
        # 2025-01-06 是周一，且非节假日
        monday = date(2025, 1, 6)
        with patch("chinese_calendar.is_holiday", return_value=False):
            assert is_trading_day(monday) is True

    def test_holiday_weekday_is_not_trading(self):
        # 假设周一被标记为节假日
        monday = date(2025, 1, 6)
        with patch("chinese_calendar.is_holiday", return_value=True):
            assert is_trading_day(monday) is False

    def test_chinese_new_year_is_not_trading(self):
        # 2025-01-29 春节（实际节假日）
        new_year = date(2025, 1, 29)
        assert is_trading_day(new_year) is False


# ─────────────────────────────────────────────────────────────────────────────
# is_in_trading_session
# ─────────────────────────────────────────────────────────────────────────────
class TestIsInTradingSession:
    def _make_dt(self, time_str: str) -> datetime:
        return datetime.strptime(f"2025-01-06 {time_str}", "%Y-%m-%d %H:%M")

    def test_morning_session_start(self):
        assert is_in_trading_session(self._make_dt("09:15")) is True

    def test_morning_session_mid(self):
        assert is_in_trading_session(self._make_dt("10:30")) is True

    def test_morning_session_end(self):
        assert is_in_trading_session(self._make_dt("12:00")) is True

    def test_before_morning_session(self):
        assert is_in_trading_session(self._make_dt("09:14")) is False

    def test_lunch_break(self):
        assert is_in_trading_session(self._make_dt("12:30")) is False

    def test_afternoon_session_start(self):
        assert is_in_trading_session(self._make_dt("13:00")) is True

    def test_afternoon_session_mid(self):
        assert is_in_trading_session(self._make_dt("14:30")) is True

    def test_afternoon_session_end(self):
        assert is_in_trading_session(self._make_dt("16:10")) is True

    def test_after_afternoon_session(self):
        assert is_in_trading_session(self._make_dt("16:11")) is False

    def test_night_is_not_session(self):
        assert is_in_trading_session(self._make_dt("20:00")) is False


# ─────────────────────────────────────────────────────────────────────────────
# load_state / save_state
# ─────────────────────────────────────────────────────────────────────────────
class TestStateIO:
    def test_save_and_load_roundtrip(self, tmp_path):
        state_path = str(tmp_path / "state.json")
        state = {"last_updated": "2025-01-01", "positions": {}, "alerts": []}
        with patch.object(monitor, "STATE_PATH", state_path):
            save_state(state)
            loaded = load_state()
        assert loaded["last_updated"] == "2025-01-01"

    def test_atomic_write_uses_tmp_file(self, tmp_path):
        state_path = str(tmp_path / "state.json")
        state = {"x": 1}
        with patch.object(monitor, "STATE_PATH", state_path):
            save_state(state)
        assert os.path.exists(state_path)
        assert not os.path.exists(state_path + ".tmp")

    def test_load_state_returns_default_when_missing(self, tmp_path):
        missing_path = str(tmp_path / "no_such_file.json")
        with patch.object(monitor, "STATE_PATH", missing_path):
            state = load_state()
        assert "positions" in state
        assert "latest_prices" in state

    def test_save_state_content_is_valid_json(self, tmp_path):
        state_path = str(tmp_path / "state.json")
        state = {"positions": {"01336": {"holdings": []}}}
        with patch.object(monitor, "STATE_PATH", state_path):
            save_state(state)
        with open(state_path) as f:
            parsed = json.load(f)
        assert parsed["positions"]["01336"]["holdings"] == []


# ─────────────────────────────────────────────────────────────────────────────
# build_engines
# ─────────────────────────────────────────────────────────────────────────────
class TestBuildEngines:
    def test_builds_correct_number_of_engines(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        assert len(engines) == 2
        assert "01336" in engines
        assert "00525" in engines

    def test_engine_has_correct_parameters(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        e = engines["01336"]
        assert e.base_price == pytest.approx(28.5)
        assert e.hist_min == pytest.approx(14.0)
        assert e.lot_size == 500

    def test_disabled_stock_excluded(self, sample_config, sample_state):
        sample_config["stocks"][1]["enabled"] = False
        engines = build_engines(sample_config, sample_state)
        assert "00525" not in engines

    def test_engines_restore_state(self, sample_config, sample_state):
        # 预置一笔持仓
        from engine import Holding
        h = Holding(grid_level=2, buy_price=26.0, lot_size=500)
        sample_state["positions"]["01336"]["holdings"] = [h.to_dict()]
        sample_state["positions"]["01336"]["grid_occupied"] = {"2": h.holding_id}
        engines = build_engines(sample_config, sample_state)
        assert len(engines["01336"].active_holdings()) == 1


# ─────────────────────────────────────────────────────────────────────────────
# run_once 主逻辑
# ─────────────────────────────────────────────────────────────────────────────
class TestRunOnce:
    def _make_notifier(self):
        n = MagicMock()
        n.notify_buy.return_value = True
        n.notify_sell.return_value = True
        n.notify_risk_warning.return_value = True
        return n

    def test_run_once_updates_latest_prices(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        notifier = self._make_notifier()
        with patch("monitor.fetch_realtime_price", return_value=27.0):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.067):
                    updated = run_once(sample_config, sample_state, engines, notifier)
        assert updated["latest_prices"]["01336"] == pytest.approx(27.0)
        assert updated["latest_prices"]["00525"] == pytest.approx(27.0)

    def test_run_once_skips_stock_when_price_unavailable(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        notifier = self._make_notifier()
        with patch("monitor.fetch_realtime_price", return_value=None):
            updated = run_once(sample_config, sample_state, engines, notifier)
        notifier.notify_buy.assert_not_called()

    def test_run_once_triggers_buy_notification(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        notifier = self._make_notifier()
        # 把价格设到第一格触发价以下
        prices = engines["01336"].grid_prices()
        trigger_price = prices[0] - 0.01
        with patch("monitor.fetch_realtime_price", return_value=trigger_price):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier)
        notifier.notify_buy.assert_called()

    def test_run_once_triggers_risk_warning_when_cash_exceeded(self, sample_config, sample_state):
        sample_config["settings"]["cash_reserve"] = 1.0  # 设极小预留触发预警
        engines = build_engines(sample_config, sample_state)
        notifier = self._make_notifier()
        with patch("monitor.fetch_realtime_price", return_value=27.0):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier)
        notifier.notify_risk_warning.assert_called_once()

    def test_run_once_no_risk_warning_when_cash_sufficient(self, sample_config, sample_state):
        sample_config["settings"]["cash_reserve"] = 9_999_999.0
        engines = build_engines(sample_config, sample_state)
        notifier = self._make_notifier()
        with patch("monitor.fetch_realtime_price", return_value=27.0):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier)
        notifier.notify_risk_warning.assert_not_called()

    def test_run_once_updates_last_updated_timestamp(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        notifier = self._make_notifier()
        with patch("monitor.fetch_realtime_price", return_value=27.0):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    updated = run_once(sample_config, sample_state, engines, notifier)
        assert updated["last_updated"] != ""


# ─────────────────────────────────────────────────────────────────────────────
# v1.7+ _add_pending 去重逻辑
# ─────────────────────────────────────────────────────────────────────────────
class TestAddPending:
    """_add_pending 内部函数：同 code+type+grid_level+holding_id 去重。"""

    def test_add_pending_first_entry_appended(self):
        state = {}
        entry = {"code": "01336", "type": "buy", "grid_level": 0, "holding_id": "h1"}
        monitor._add_pending(state, entry)
        assert len(state["pending_confirmations"]) == 1

    def test_add_pending_duplicate_updates_existing(self):
        state = {}
        e1 = {"code": "01336", "type": "buy", "grid_level": 0, "holding_id": "h1", "price": 27.0}
        e2 = {"code": "01336", "type": "buy", "grid_level": 0, "holding_id": "h1", "price": 26.5}
        monitor._add_pending(state, e1)
        monitor._add_pending(state, e2)
        # 去重：列表长度仍为 1，但 price 被更新
        assert len(state["pending_confirmations"]) == 1
        assert state["pending_confirmations"][0]["price"] == 26.5

    def test_add_pending_different_level_not_deduped(self):
        state = {}
        e1 = {"code": "01336", "type": "buy", "grid_level": 0, "holding_id": "h1"}
        e2 = {"code": "01336", "type": "buy", "grid_level": 1, "holding_id": "h2"}
        monitor._add_pending(state, e1)
        monitor._add_pending(state, e2)
        assert len(state["pending_confirmations"]) == 2

    def test_add_pending_different_type_not_deduped(self):
        state = {}
        e1 = {"code": "01336", "type": "buy",  "grid_level": 0, "holding_id": "h1"}
        e2 = {"code": "01336", "type": "sell", "grid_level": 0, "holding_id": "h1"}
        monitor._add_pending(state, e1)
        monitor._add_pending(state, e2)
        assert len(state["pending_confirmations"]) == 2

    def test_add_pending_different_code_not_deduped(self):
        state = {}
        e1 = {"code": "01336", "type": "buy", "grid_level": 0, "holding_id": "h1"}
        e2 = {"code": "00525", "type": "buy", "grid_level": 0, "holding_id": "h2"}
        monitor._add_pending(state, e1)
        monitor._add_pending(state, e2)
        assert len(state["pending_confirmations"]) == 2

    def test_add_pending_initializes_list_if_missing(self):
        state = {}
        monitor._add_pending(state, {"code": "x", "type": "buy", "grid_level": 0})
        assert "pending_confirmations" in state


# ─────────────────────────────────────────────────────────────────────────────
# v1.8+ min_holding_limit 透传到 check_sell_signals
# ─────────────────────────────────────────────────────────────────────────────
class TestRunOnceMinHolding:
    """run_once 将 settings.min_holding_limit 透传给 check_sell_signals。"""

    def test_min_holding_suppresses_sell_signal(self, sample_config, sample_state):
        """设置阈值后，即使价格超过止盈价也不产生卖出通知。"""
        sample_config["settings"]["min_holding_limit"] = 500  # 阈值=1手
        engines = build_engines(sample_config, sample_state)
        notifier_mock = MagicMock()
        notifier_mock.notify_buy.return_value = True
        notifier_mock.notify_sell.return_value = True
        notifier_mock.notify_risk_warning.return_value = True

        target = engines["01336"]
        holding = target.confirm_buy(0)
        tp_price = holding.take_profit_price + 0.01  # 超过止盈价

        with patch("monitor.fetch_realtime_price", return_value=tp_price):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier_mock)

        # 只有 500 股（=阈值），卖出信号被屏蔽
        notifier_mock.notify_sell.assert_not_called()

    def test_min_holding_zero_allows_sell_signal(self, sample_config, sample_state):
        """阈值为 0 时，正常触发卖出通知。"""
        sample_config["settings"]["min_holding_limit"] = 0
        engines = build_engines(sample_config, sample_state)
        notifier_mock = MagicMock()
        notifier_mock.notify_buy.return_value = True
        notifier_mock.notify_sell.return_value = True
        notifier_mock.notify_risk_warning.return_value = True

        target = engines["01336"]
        holding = target.confirm_buy(0)
        tp_price = holding.take_profit_price + 0.01

        with patch("monitor.fetch_realtime_price", return_value=tp_price):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier_mock)

        notifier_mock.notify_sell.assert_called()

    def test_min_holding_limit_default_zero_when_missing(self, sample_config, sample_state):
        """settings 中没有 min_holding_limit 时默认为 0，不影响正常卖出。"""
        # 确保 settings 中没有该键
        sample_config["settings"].pop("min_holding_limit", None)
        engines = build_engines(sample_config, sample_state)
        notifier_mock = MagicMock()
        notifier_mock.notify_buy.return_value = True
        notifier_mock.notify_sell.return_value = True
        notifier_mock.notify_risk_warning.return_value = True

        target = engines["01336"]
        holding = target.confirm_buy(0)
        tp_price = holding.take_profit_price + 0.01

        with patch("monitor.fetch_realtime_price", return_value=tp_price):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier_mock)

        notifier_mock.notify_sell.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# v2.4 新增：build_watchers / v2 风险 / 观察者通知
# ─────────────────────────────────────────────────────────────────────────────
from monitor import build_watchers
from engine import PositionSummary, WatcherTarget


class TestBuildWatchers:
    def test_build_from_config(self, sample_config):
        sample_config["watchers"] = [
            {"code": "02800", "name": "盈富基金", "akshare_code": "02800",
             "base_price": 80.0, "total_budget": 100000.0, "enabled": True}
        ]
        result = build_watchers(sample_config)
        assert len(result) == 1
        assert result[0].code == "02800"
        assert result[0].base_price == pytest.approx(80.0)

    def test_disabled_watcher_excluded(self, sample_config):
        sample_config["watchers"] = [
            {"code": "02800", "name": "盈富", "akshare_code": "02800",
             "base_price": 80.0, "enabled": False}
        ]
        result = build_watchers(sample_config)
        assert result == []

    def test_no_watchers_key(self, sample_config):
        sample_config.pop("watchers", None)
        result = build_watchers(sample_config)
        assert result == []


class TestBuildEnginesLoadsPositionSummary:
    def test_loads_ps_from_state(self, sample_config, sample_state):
        """state.json 中有 position_summary 时，应正确加载到引擎。"""
        ps_data = {"total_shares": 5000, "avg_cost": 28.0, "core_shares": 1000, "total_budget": 200000.0}
        sample_state.setdefault("positions", {}).setdefault("01336", {})["position_summary"] = ps_data
        engines = build_engines(sample_config, sample_state)
        ps = engines["01336"].position_summary
        assert ps is not None
        assert ps.total_shares == 5000
        assert ps.core_shares == 1000

    def test_initializes_ps_from_config_budget(self, sample_config, sample_state):
        """config 中有 total_budget 时，若无 state ps，应用 config 预算初始化。"""
        sample_config["stocks"][0]["total_budget"] = 250000.0
        # state 中无 position_summary
        sample_state.get("positions", {}).pop("01336", None)
        engines = build_engines(sample_config, sample_state)
        ps = engines["01336"].position_summary
        assert ps is not None
        assert ps.total_budget == pytest.approx(250000.0)


class TestRunOnceWatcherNotification:
    def test_watcher_opportunity_triggers_notify(self, sample_config, sample_state):
        """现价 <= 建仓价时应调用 notify_watcher。"""
        sample_config["watchers"] = [
            {"code": "02800", "name": "盈富", "akshare_code": "02800",
             "base_price": 80.0, "enabled": True}
        ]
        engines = build_engines(sample_config, sample_state)
        notifier_mock = MagicMock()
        notifier_mock.notify_buy.return_value = True
        notifier_mock.notify_sell.return_value = True
        notifier_mock.notify_risk_warning.return_value = True
        notifier_mock.notify_watcher.return_value = True

        def fake_price(code):
            return 78.0 if code == "02800" else 30.0

        with patch("monitor.fetch_realtime_price", side_effect=fake_price):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier_mock)

        notifier_mock.notify_watcher.assert_called_once_with(
            code="02800", name="盈富", current_price=78.0, base_price=80.0
        )


# ─────────────────────────────────────────────────────────────────────────────
# refresh_valuation_history
# ─────────────────────────────────────────────────────────────────────────────
class TestRefreshValuationHistory:
    """refresh_valuation_history 函数独立于主循环，需单独测试。"""

    def test_skips_stock_when_price_is_zero(self, sample_config, sample_state):
        """价格为 0 时跳过该标的，不调用 fetch 函数。"""
        from monitor import refresh_valuation_history

        sample_state["latest_prices"] = {"01336": 0.0, "00525": 0.0}
        with patch("monitor.fetch_div_yield_history") as mock_dy, \
             patch("monitor.fetch_pb_history") as mock_pb:
            # make lazy-imported functions patchable
            import sys
            sys.modules.setdefault("monitor", __import__("monitor"))
            with patch("crawler.fetch_div_yield_history", return_value=[]) as mock_dy2, \
                 patch("crawler.fetch_pb_history", return_value=[]) as mock_pb2:
                refresh_valuation_history(sample_config, sample_state)
        # 零价格时两个 fetch 都不应被调用
        mock_dy2.assert_not_called()
        mock_pb2.assert_not_called()

    def test_updates_state_with_valuation_data(self, sample_config, sample_state):
        """当价格有效时，state 中应写入 dy/pb 历史数据。"""
        from monitor import refresh_valuation_history

        sample_state["latest_prices"] = {"01336": 30.0, "00525": 4.0}
        fake_dy = [0.06, 0.065, 0.07]
        fake_pb = [1.2, 1.5, 1.8]
        with patch("crawler.fetch_div_yield_history", return_value=fake_dy), \
             patch("crawler.fetch_pb_history", return_value=fake_pb):
            result = refresh_valuation_history(sample_config, sample_state)
        assert "valuation_history" in result
        assert result["valuation_history"]["01336"]["div_yield"] == fake_dy
        assert result["valuation_history"]["01336"]["pb"] == fake_pb

    def test_skips_disabled_stock(self, sample_config, sample_state):
        """disabled=False 的标的不刷新估值数据。"""
        from monitor import refresh_valuation_history

        sample_config["stocks"][0]["enabled"] = False
        sample_state["latest_prices"] = {"01336": 30.0, "00525": 4.0}
        with patch("crawler.fetch_div_yield_history", return_value=[0.06]) as mock_dy, \
             patch("crawler.fetch_pb_history", return_value=[1.2]) as mock_pb:
            refresh_valuation_history(sample_config, sample_state)
        # 01336 被禁用，只有 00525 会触发 fetch；mock 至多调用1次
        assert mock_dy.call_count <= 1



# ─────────────────────────────────────────────────────────────────────────────
# refresh_valuation_history
# ─────────────────────────────────────────────────────────────────────────────
class TestRefreshValuationHistory:
    """refresh_valuation_history 独立于主循环，需单独测试。"""

    def test_skips_stock_when_price_is_zero(self, sample_config, sample_state):
        """价格为 0 时跳过该标的，不调用 fetch 函数。"""
        from monitor import refresh_valuation_history

        sample_state["latest_prices"] = {"01336": 0.0, "00525": 0.0}
        with patch("crawler.fetch_div_yield_history", return_value=[]) as mock_dy, \
             patch("crawler.fetch_pb_history", return_value=[]) as mock_pb:
            refresh_valuation_history(sample_config, sample_state)
        mock_dy.assert_not_called()
        mock_pb.assert_not_called()

    def test_updates_state_with_valuation_data(self, sample_config, sample_state):
        """价格有效时，state 中应写入 dy 和 pb 历史数据。"""
        from monitor import refresh_valuation_history

        sample_state["latest_prices"] = {"01336": 30.0, "00525": 4.0}
        fake_dy = [0.06, 0.065, 0.07]
        fake_pb = [1.2, 1.5, 1.8]
        with patch("crawler.fetch_div_yield_history", return_value=fake_dy), \
             patch("crawler.fetch_pb_history", return_value=fake_pb):
            result = refresh_valuation_history(sample_config, sample_state)
        assert "valuation_history" in result
        assert result["valuation_history"]["01336"]["div_yield"] == fake_dy
        assert result["valuation_history"]["01336"]["pb"] == fake_pb

    def test_skips_disabled_stock(self, sample_config, sample_state):
        """enabled=False 的标的不应刷新估值数据。"""
        from monitor import refresh_valuation_history

        sample_config["stocks"][0]["enabled"] = False
        sample_state["latest_prices"] = {"01336": 30.0, "00525": 4.0}
        with patch("crawler.fetch_div_yield_history", return_value=[0.06]) as mock_dy, \
             patch("crawler.fetch_pb_history", return_value=[1.2]):
            refresh_valuation_history(sample_config, sample_state)
        # 01336 被禁用，只有 00525 会触发 fetch；mock 至多调用 1 次
        assert mock_dy.call_count <= 1


# ─────────────────────────────────────────────────────────────────────────────
# v2.6 E2E 通知链测试（买入信号 → state 写入 → Bark 通知）
# ─────────────────────────────────────────────────────────────────────────────
class TestNotificationChain:
    """端到端验证：价格触发买入阈值时，monitor.run_once 完整执行通知链。

    run_once(config, state, engines, notifier) → dict
    """

    @pytest.fixture
    def minimal_config(self):
        return {
            "settings": {
                "poll_interval_seconds": 30,
                "bark_api_url": "https://api.day.app",
                "bark_token": "test_token",
                "cash_reserve": 0.0,
                "lot_size_default": 500,
                "grid_levels": 3,
                "default_take_profit_pct": 0.07,
                "min_holding_limit": 0,
                "max_capital_usage": 0.0,
                "web_server_url": "http://localhost:8501",
            },
            "stocks": [
                {
                    "code": "01336",
                    "name": "新华保险",
                    "exchange": "HK",
                    "akshare_code": "01336",
                    "base_price": 50.0,
                    "hist_min": 14.0,
                    "lot_size": 500,
                    "step": 2.0,
                    "take_profit_pct": 0.07,
                    "enabled": True,
                    "annual_dividend_hkd": 1.8,
                    "total_budget": 100000.0,
                    "trading_mode": "manual",
                }
            ],
            "watchers": [],
        }

    @pytest.fixture
    def minimal_state(self):
        return {
            "positions": {},
            "pending_confirmations": [],
            "latest_prices": {},
            "latest_dividend_ttm": {},
            "valuation_history": {},
        }

    @pytest.fixture
    def mock_engines(self, minimal_config):
        """构造 GridEngine 实例（使用真实 engine，不 mock 内部逻辑）。"""
        from engine import GridEngine
        stock = minimal_config["stocks"][0]
        engine = GridEngine(
            code=stock["code"],
            name=stock["name"],
            base_price=stock["base_price"],
            step=stock["step"],
            grid_levels=minimal_config["settings"]["grid_levels"],
            lot_size=stock["lot_size"],
            take_profit_pct=stock["take_profit_pct"],
            hist_min=stock["hist_min"],
        )
        return {"01336": engine}

    def test_buy_signal_appended_to_pending(self, minimal_config, minimal_state, mock_engines):
        """价格触发买入格时，pending_confirmations 应新增一条 buy 记录。"""
        from monitor import run_once
        mock_notifier = MagicMock()

        # grid_prices: [50, 48, 46]；price=47.5 < 48 → 第 2 格触发
        with patch("monitor.fetch_realtime_price", return_value=47.5),              patch("monitor.fetch_dividend_ttm", return_value=1.8),              patch("monitor.save_state"):
            new_state = run_once(minimal_config, minimal_state, mock_engines, mock_notifier)

        pending = new_state.get("pending_confirmations", [])
        buy_items = [p for p in pending if p.get("type") == "buy" and p.get("code") == "01336"]
        assert len(buy_items) >= 1, f"应有 buy pending，实际 pending={pending}"

    def test_no_buy_signal_when_price_above_all_grids(self, minimal_config, minimal_state, mock_engines):
        """价格高于所有网格 → 无 buy pending。"""
        from monitor import run_once
        mock_notifier = MagicMock()

        with patch("monitor.fetch_realtime_price", return_value=999.0),              patch("monitor.fetch_dividend_ttm", return_value=1.8),              patch("monitor.save_state"):
            new_state = run_once(minimal_config, minimal_state, mock_engines, mock_notifier)

        buy_items = [p for p in new_state.get("pending_confirmations", [])
                     if p.get("type") == "buy" and p.get("code") == "01336"]
        assert len(buy_items) == 0

    def test_price_none_skips_state_write(self, minimal_config, minimal_state, mock_engines):
        """fetch_realtime_price 返回 None（硬拦截）时，latest_prices 不更新。"""
        from monitor import run_once
        mock_notifier = MagicMock()

        with patch("monitor.fetch_realtime_price", return_value=None),              patch("monitor.fetch_dividend_ttm", return_value=1.8),              patch("monitor.save_state"):
            new_state = run_once(minimal_config, minimal_state, mock_engines, mock_notifier)

        assert "01336" not in new_state.get("latest_prices", {}),             "价格为 None 时不应写入 latest_prices"
