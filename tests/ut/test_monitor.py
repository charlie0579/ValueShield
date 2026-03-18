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
