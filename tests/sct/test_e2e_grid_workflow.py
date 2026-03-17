"""
tests/sct/test_e2e_grid_workflow.py - 端到端场景测试（SCT）
模拟完整的交易生命周期：行情拉取 → 网格信号 → 推送 → 确认成交 → 状态持久化。
所有外部依赖（AkShare、Bark）均通过 Mock 隔离。
"""
import json
import os
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

import monitor
from monitor import build_engines, run_once, save_state, load_state
from engine import GridEngine, compute_total_risk_capital, check_cash_warning
from notifier import BarkNotifier
from crawler import compute_dividend_yield


# ─────────────────────────────────────────────────────────────────────────────
# 辅助：构造 mock notifier
# ─────────────────────────────────────────────────────────────────────────────
def make_notifier(web_url: str = "http://localhost:8501") -> MagicMock:
    n = MagicMock(spec=BarkNotifier)
    n.notify_buy.return_value = True
    n.notify_sell.return_value = True
    n.notify_risk_warning.return_value = True
    return n


# ─────────────────────────────────────────────────────────────────────────────
# SCT-01: 完整"下跌触发买入"场景
# ─────────────────────────────────────────────────────────────────────────────
class TestSCT01_BuyTriggerWorkflow:
    """
    场景：股价从高位跌穿第 0 格触发价 → 推送买入通知 → 用户确认 → 状态写入磁盘。
    """

    def test_full_buy_trigger_and_confirm(self, sample_config, sample_state, tmp_path):
        engines = build_engines(sample_config, sample_state)
        notifier = make_notifier()

        target = engines["01336"]
        trigger_price = target.grid_prices()[0] - 0.01

        state_path = str(tmp_path / "state.json")
        with patch.object(monitor, "STATE_PATH", state_path):
            with patch("monitor.fetch_realtime_price", return_value=trigger_price):
                with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                    with patch("monitor.compute_dividend_yield", return_value=0.065):
                        updated = run_once(sample_config, sample_state, engines, notifier)

        # 验证 Bark 推送被调用
        notifier.notify_buy.assert_called()
        call_kwargs = notifier.notify_buy.call_args
        assert call_kwargs.kwargs["code"] == "01336" or call_kwargs[1]["code"] == "01336"

        # 模拟用户在 Web 端确认成交（第 0 格）
        holding = target.confirm_buy(0)
        assert holding.grid_level == 0
        assert "0" in target.grid_occupied

        # 将状态写入磁盘并验证读取
        updated["positions"]["01336"] = target.to_state_dict()
        save_state(updated)
        loaded = load_state()
        assert loaded["positions"]["01336"]["grid_occupied"]["0"] == holding.holding_id

    def test_buy_at_multiple_levels(self, sample_config, sample_state):
        """股价大幅下跌，触发多个格子的买入信号。"""
        engines = build_engines(sample_config, sample_state)
        notifier = make_notifier()

        target = engines["01336"]
        prices = target.grid_prices()
        crash_price = prices[4] - 0.001  # 低于第 4 格

        with patch("monitor.fetch_realtime_price", return_value=crash_price):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier)

        # 至少调用了 5 次买入推送（格 0～4）
        assert notifier.notify_buy.call_count >= 5


# ─────────────────────────────────────────────────────────────────────────────
# SCT-02: 完整"持仓止盈"场景
# ─────────────────────────────────────────────────────────────────────────────
class TestSCT02_TakeProfitWorkflow:
    """
    场景：已持仓 → 股价上涨触及止盈价 → 推送卖出通知 → 确认卖出 → 格子释放。
    """

    def test_take_profit_triggered_and_confirmed(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        notifier = make_notifier()

        target = engines["01336"]
        # 先确认买入第 3 格
        holding = target.confirm_buy(3)
        tp_price = holding.take_profit_price

        # 价格上涨触及止盈价
        with patch("monitor.fetch_realtime_price", return_value=tp_price + 0.01):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier)

        notifier.notify_sell.assert_called()

        # 确认卖出
        result = target.confirm_sell(holding.holding_id, tp_price)
        assert result is not None
        assert result.sold is True
        assert "3" not in target.grid_occupied
        assert len(target.active_holdings()) == 0

    def test_custom_take_profit_respected(self, sample_config, sample_state):
        """自定义止盈比例高于默认时，不应在默认止盈价触发。"""
        engines = build_engines(sample_config, sample_state)
        target = engines["01336"]

        holding = target.confirm_buy(1)
        target.set_custom_take_profit(holding.holding_id, 0.20)

        default_tp = holding.buy_price * 1.07
        to_sell = target.check_sell_signals(default_tp + 0.001)
        assert holding.holding_id not in [h.holding_id for h in to_sell]

        custom_tp = holding.buy_price * 1.20
        to_sell = target.check_sell_signals(custom_tp + 0.001)
        assert holding.holding_id in [h.holding_id for h in to_sell]


# ─────────────────────────────────────────────────────────────────────────────
# SCT-03: "影子网格"补录与对账场景
# ─────────────────────────────────────────────────────────────────────────────
class TestSCT03_ShadowGridReconciliation:
    """
    场景：用户在手机上手动操作了两笔，但程序没收到确认 → 通过 manual_supplement 补录。
    """

    def test_supplement_matches_real_account(self, sample_config, sample_state, tmp_path):
        engines = build_engines(sample_config, sample_state)
        target = engines["01336"]

        # 用户在手机上买了第 5、8 格，但程序没收到确认
        target.manual_supplement(5, target.grid_prices()[5])
        target.manual_supplement(8, target.grid_prices()[8])

        assert len(target.active_holdings()) == 2
        assert "5" in target.grid_occupied
        assert "8" in target.grid_occupied

        # 将状态持久化
        state_path = str(tmp_path / "state.json")
        state = sample_state.copy()
        state["positions"]["01336"] = target.to_state_dict()
        with patch.object(monitor, "STATE_PATH", state_path):
            save_state(state)

        # 重新加载并恢复，验证补录数据完整
        with patch.object(monitor, "STATE_PATH", state_path):
            restored_state = load_state()
        engines2 = build_engines(sample_config, restored_state)
        assert len(engines2["01336"].active_holdings()) == 2

    def test_supplement_then_normal_buy_coexist(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        target = engines["01336"]

        target.manual_supplement(0, target.grid_prices()[0])
        target.confirm_buy(3)

        assert len(target.active_holdings()) == 2
        assert "0" in target.grid_occupied
        assert "3" in target.grid_occupied


# ─────────────────────────────────────────────────────────────────────────────
# SCT-04: 现金压力预警端到端场景
# ─────────────────────────────────────────────────────────────────────────────
class TestSCT04_CashWarningWorkflow:
    """
    场景：总风险资金超过现金预留 → Web 看板预警 + Bark 推送 + 警报写入 state。
    """

    def test_warning_triggered_and_stored_in_state(self, sample_config, sample_state):
        sample_config["settings"]["cash_reserve"] = 100.0  # 极小预留必然触发
        engines = build_engines(sample_config, sample_state)
        notifier = make_notifier()

        with patch("monitor.fetch_realtime_price", return_value=27.0):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    updated = run_once(sample_config, sample_state, engines, notifier)

        notifier.notify_risk_warning.assert_called_once()
        assert len(updated.get("alerts", [])) >= 1
        assert updated["alerts"][0]["type"] == "cash_warning"

    def test_warning_disappears_after_sufficient_cash(self, sample_config, sample_state):
        sample_config["settings"]["cash_reserve"] = 999_999_999.0
        engines = build_engines(sample_config, sample_state)
        notifier = make_notifier()

        with patch("monitor.fetch_realtime_price", return_value=27.0):
            with patch("monitor.fetch_dividend_ttm", return_value=1.8):
                with patch("monitor.compute_dividend_yield", return_value=0.065):
                    run_once(sample_config, sample_state, engines, notifier)

        notifier.notify_risk_warning.assert_not_called()

    def test_risk_reduces_as_grids_occupied(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        target = engines["01336"]
        initial_risk = compute_total_risk_capital(engines)

        # 占用 3 个格子后风险资金应减少
        target.confirm_buy(0)
        target.confirm_buy(5)
        target.confirm_buy(10)
        new_risk = compute_total_risk_capital(engines)
        assert new_risk < initial_risk


# ─────────────────────────────────────────────────────────────────────────────
# SCT-05: Base/Step 重置后网格重新计算
# ─────────────────────────────────────────────────────────────────────────────
class TestSCT05_GridResetWorkflow:
    """
    场景：用户手动修改 Base_Price → 所有格子价格重新计算 → 已占用格子不受影响（位置变化）。
    """

    def test_base_reset_changes_all_grid_prices(self, engine_1336: GridEngine):
        old_prices = engine_1336.grid_prices().copy()
        engine_1336.set_base_price(35.0)
        new_prices = engine_1336.grid_prices()
        assert new_prices != old_prices
        assert new_prices[0] < 35.0

    def test_step_override_changes_grid_spacing(self, engine_1336: GridEngine):
        engine_1336.set_step(2.0)
        prices = engine_1336.grid_prices()
        # 任意相邻两格的差应约等于 step
        for i in range(len(prices) - 1):
            assert abs(prices[i] - prices[i + 1]) == pytest.approx(2.0, rel=1e-4)

    def test_holdings_preserve_original_buy_price_after_reset(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(2)
        original_buy_price = holding.buy_price
        engine_1336.set_base_price(40.0)
        # 历史持仓的买入价不变
        assert engine_1336.active_holdings()[0].buy_price == pytest.approx(original_buy_price)

    def test_new_grid_prices_cover_full_range_after_reset(self, engine_1336: GridEngine):
        engine_1336.set_base_price(30.0)
        prices = engine_1336.grid_prices()
        assert len(prices) == engine_1336.grid_levels
        assert prices[0] < 30.0  # 最高格在 base 以下
        assert prices[-1] > engine_1336.hist_min - 1  # 最低格不低于历史最低太多


# ─────────────────────────────────────────────────────────────────────────────
# SCT-06: 状态持久化 + 断电恢复
# ─────────────────────────────────────────────────────────────────────────────
class TestSCT06_StatePersistenceRecovery:
    """
    场景：模拟程序重启，验证 state.json 中的持仓、网格占用完整恢复。
    """

    def test_full_state_recovery_after_restart(self, sample_config, sample_state, tmp_path):
        # 第一轮：建立若干持仓
        engines_1 = build_engines(sample_config, sample_state)
        engines_1["01336"].confirm_buy(1)
        engines_1["01336"].confirm_buy(4)
        engines_1["00525"].confirm_buy(0)

        state = sample_state.copy()
        state["positions"]["01336"] = engines_1["01336"].to_state_dict()
        state["positions"]["00525"] = engines_1["00525"].to_state_dict()
        state["latest_prices"] = {"01336": 27.0, "00525": 4.3}

        state_path = str(tmp_path / "state.json")
        with patch.object(monitor, "STATE_PATH", state_path):
            save_state(state)
            restored_state = load_state()

        # 第二轮：用恢复的 state 重建引擎
        engines_2 = build_engines(sample_config, restored_state)

        assert len(engines_2["01336"].active_holdings()) == 2
        assert len(engines_2["00525"].active_holdings()) == 1
        assert "1" in engines_2["01336"].grid_occupied
        assert "4" in engines_2["01336"].grid_occupied
        assert "0" in engines_2["00525"].grid_occupied

    def test_atomic_write_prevents_corruption(self, tmp_path):
        """验证写入过程不会留下 .tmp 残留文件。"""
        state_path = str(tmp_path / "state.json")
        state = {"positions": {}, "last_updated": datetime.now().isoformat()}
        with patch.object(monitor, "STATE_PATH", state_path):
            save_state(state)

        assert os.path.exists(state_path)
        assert not os.path.exists(state_path + ".tmp")

    def test_concurrent_writes_do_not_corrupt(self, tmp_path):
        """多次连续写入后文件仍是合法 JSON。"""
        state_path = str(tmp_path / "state.json")
        with patch.object(monitor, "STATE_PATH", state_path):
            for i in range(10):
                save_state({"iteration": i, "positions": {}})
            final = load_state()
        assert final["iteration"] == 9


# ─────────────────────────────────────────────────────────────────────────────
# SCT-07: 两只股票并行监控
# ─────────────────────────────────────────────────────────────────────────────
class TestSCT07_MultiStockMonitoring:
    """
    场景：同时监控新华保险和广深铁路，两只股票互相独立。
    """

    def test_buy_signal_only_for_triggered_stock(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)
        notifier = make_notifier()

        prices_1336 = engines["01336"].grid_prices()
        prices_0525 = engines["00525"].grid_prices()

        def mock_price(code: str):
            if code == "01336":
                return prices_1336[0] - 0.001  # 01336 触发
            return prices_0525[0] + 1.0  # 00525 不触发

        with patch("monitor.fetch_realtime_price", side_effect=mock_price):
            with patch("monitor.fetch_dividend_ttm", return_value=1.0):
                with patch("monitor.compute_dividend_yield", return_value=0.06):
                    run_once(sample_config, sample_state, engines, notifier)

        # 只有 01336 的买入通知被调用
        for call in notifier.notify_buy.call_args_list:
            kwargs = call.kwargs if call.kwargs else call[1]
            assert kwargs.get("code") == "01336"

    def test_independent_risk_capital_per_stock(self, sample_config, sample_state):
        engines = build_engines(sample_config, sample_state)

        # 占用 01336 部分格子
        engines["01336"].confirm_buy(0)
        engines["01336"].confirm_buy(1)

        risk_1336 = engines["01336"].compute_risk_capital()
        risk_0525 = engines["00525"].compute_risk_capital()

        # 两只股票风险资金独立计算
        total = compute_total_risk_capital(engines)
        assert total == pytest.approx(risk_1336 + risk_0525, rel=1e-6)
