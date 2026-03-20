"""
tests/ut/test_engine.py - engine.py 单元测试
覆盖：Holding、GridEngine 全部公开方法，边界情况，序列化/反序列化。
"""
import pytest
from engine import (
    Holding,
    GridEngine,
    compute_total_risk_capital,
    check_cash_warning,
)


# ─────────────────────────────────────────────────────────────────────────────
# Holding 数据类
# ─────────────────────────────────────────────────────────────────────────────
class TestHolding:
    def test_default_take_profit_pct(self):
        h = Holding(buy_price=20.0, take_profit_pct=0.07)
        assert h.effective_take_profit_pct == 0.07

    def test_custom_take_profit_pct_overrides_default(self):
        h = Holding(buy_price=20.0, take_profit_pct=0.07, custom_take_profit_pct=0.10)
        assert h.effective_take_profit_pct == 0.10

    def test_take_profit_price_calculation(self):
        h = Holding(buy_price=20.0, take_profit_pct=0.10)
        assert abs(h.take_profit_price - 22.0) < 1e-6

    def test_cost_value(self):
        h = Holding(buy_price=10.0, lot_size=500)
        assert h.cost_value == 5000.0

    def test_profit_if_sold_at(self):
        h = Holding(buy_price=10.0, lot_size=500)
        assert h.profit_if_sold_at(11.0) == pytest.approx(500.0)

    def test_profit_pct_if_sold_at(self):
        h = Holding(buy_price=20.0, lot_size=500)
        assert h.profit_pct_if_sold_at(21.0) == pytest.approx(0.05)

    def test_profit_pct_zero_buy_price(self):
        h = Holding(buy_price=0.0, lot_size=500)
        assert h.profit_pct_if_sold_at(10.0) == 0.0

    def test_to_dict_and_from_dict_roundtrip(self):
        h = Holding(
            grid_level=3,
            buy_price=25.0,
            lot_size=500,
            take_profit_pct=0.08,
            custom_take_profit_pct=0.12,
        )
        d = h.to_dict()
        restored = Holding.from_dict(d)
        assert restored.grid_level == h.grid_level
        assert restored.buy_price == h.buy_price
        assert restored.lot_size == h.lot_size
        assert restored.custom_take_profit_pct == h.custom_take_profit_pct
        assert restored.sold is False

    def test_from_dict_missing_fields_uses_defaults(self):
        h = Holding.from_dict({})
        assert h.buy_price == 0.0
        assert h.lot_size == 500
        assert h.take_profit_pct == 0.07
        assert h.sold is False


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — Step 计算
# ─────────────────────────────────────────────────────────────────────────────
class TestGridEngineStep:
    def test_auto_step_formula(self, engine_1336: GridEngine):
        expected = (28.5 - 14.0) / 20
        assert engine_1336.step == pytest.approx(expected, rel=1e-4)

    def test_manual_step_override(self, engine_1336: GridEngine):
        engine_1336.set_step(1.0)
        assert engine_1336.step == pytest.approx(1.0)

    def test_set_base_price_clears_manual_step(self, engine_1336: GridEngine):
        engine_1336.set_step(5.0)
        engine_1336.set_base_price(30.0)
        # step 应重新按公式计算
        expected = (30.0 - 14.0) / 20
        assert engine_1336.step == pytest.approx(expected, rel=1e-4)

    def test_zero_span_returns_minimum_step(self):
        engine = GridEngine(
            code="TEST", name="Test", base_price=10.0, hist_min=10.0, lot_size=100
        )
        assert engine.step == pytest.approx(0.01)

    def test_0525_auto_step(self, engine_0525: GridEngine):
        expected = (4.5 - 2.2) / 20
        assert engine_0525.step == pytest.approx(expected, rel=1e-4)


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 网格价格列表
# ─────────────────────────────────────────────────────────────────────────────
class TestGridPrices:
    def test_grid_prices_length(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        assert len(prices) == 20

    def test_grid_prices_descending(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        for i in range(len(prices) - 1):
            assert prices[i] > prices[i + 1]

    def test_first_grid_price(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        expected = engine_1336.base_price - 1 * engine_1336.step
        assert prices[0] == pytest.approx(expected, rel=1e-4)

    def test_last_grid_price_near_hist_min(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        # 第 20 格 ≈ base - 20*step = hist_min
        assert prices[-1] == pytest.approx(engine_1336.hist_min, rel=1e-3)

    def test_grid_prices_after_base_change(self, engine_1336: GridEngine):
        engine_1336.set_base_price(35.0)
        prices = engine_1336.grid_prices()
        assert prices[0] == pytest.approx(35.0 - engine_1336.step, rel=1e-4)


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 买入信号
# ─────────────────────────────────────────────────────────────────────────────
class TestBuySignal:
    def test_no_signal_above_all_grids(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        high_price = prices[0] + 1.0
        assert engine_1336.check_buy_signal(high_price) == []

    def test_signal_below_first_grid(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        triggered = engine_1336.check_buy_signal(prices[0] - 0.001)
        assert 0 in triggered

    def test_signal_at_exact_grid_price(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        triggered = engine_1336.check_buy_signal(prices[2])
        assert 2 in triggered

    def test_no_duplicate_signal_for_occupied_grid(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0)
        prices = engine_1336.grid_prices()
        triggered = engine_1336.check_buy_signal(prices[0] - 0.001)
        assert 0 not in triggered

    def test_multiple_levels_triggered(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        # 价格跌到第 5 格以下，格 0~5 均应触发
        triggered = engine_1336.check_buy_signal(prices[5] - 0.001)
        assert all(i in triggered for i in range(6))


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 确认买入
# ─────────────────────────────────────────────────────────────────────────────
class TestConfirmBuy:
    def test_confirm_buy_adds_holding(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(3)
        assert len(engine_1336.active_holdings()) == 1
        assert holding.grid_level == 3

    def test_confirm_buy_occupies_grid(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(3)
        assert "3" in engine_1336.grid_occupied

    def test_confirm_buy_price_matches_grid(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        holding = engine_1336.confirm_buy(1)
        assert holding.buy_price == pytest.approx(prices[1], rel=1e-6)

    def test_multiple_buys(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0)
        engine_1336.confirm_buy(2)
        engine_1336.confirm_buy(4)
        assert len(engine_1336.active_holdings()) == 3


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 卖出信号
# ─────────────────────────────────────────────────────────────────────────────
class TestSellSignal:
    def test_no_sell_signal_below_take_profit(self, engine_with_holdings: GridEngine):
        low_price = 1.0
        assert engine_with_holdings.check_sell_signals(low_price) == []

    def test_sell_signal_above_take_profit(self, engine_with_holdings: GridEngine):
        # 找出第一笔持仓的止盈价，确保触发
        first = engine_with_holdings.active_holdings()[0]
        to_sell = engine_with_holdings.check_sell_signals(first.take_profit_price + 0.01)
        holding_ids = [h.holding_id for h in to_sell]
        assert first.holding_id in holding_ids

    def test_all_holdings_triggered_at_high_price(self, engine_with_holdings: GridEngine):
        to_sell = engine_with_holdings.check_sell_signals(9999.0)
        assert len(to_sell) == 3

    def test_already_sold_not_triggered(self, engine_with_holdings: GridEngine):
        first = engine_with_holdings.active_holdings()[0]
        engine_with_holdings.confirm_sell(first.holding_id, 9999.0)
        to_sell = engine_with_holdings.check_sell_signals(9999.0)
        sold_ids = [h.holding_id for h in to_sell]
        assert first.holding_id not in sold_ids


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 确认卖出
# ─────────────────────────────────────────────────────────────────────────────
class TestConfirmSell:
    def test_confirm_sell_marks_holding_sold(self, engine_with_holdings: GridEngine):
        first = engine_with_holdings.active_holdings()[0]
        result = engine_with_holdings.confirm_sell(first.holding_id, 30.0)
        assert result is not None
        assert result.sold is True
        assert result.sell_price == pytest.approx(30.0)

    def test_confirm_sell_releases_grid(self, engine_with_holdings: GridEngine):
        first = engine_with_holdings.active_holdings()[0]
        level_key = str(first.grid_level)
        engine_with_holdings.confirm_sell(first.holding_id, 30.0)
        assert level_key not in engine_with_holdings.grid_occupied

    def test_confirm_sell_unknown_id_returns_none(self, engine_with_holdings: GridEngine):
        result = engine_with_holdings.confirm_sell("nonexistent_id", 30.0)
        assert result is None

    def test_confirm_sell_reduces_active_holdings(self, engine_with_holdings: GridEngine):
        first = engine_with_holdings.active_holdings()[0]
        engine_with_holdings.confirm_sell(first.holding_id, 30.0)
        assert len(engine_with_holdings.active_holdings()) == 2


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 手动补录
# ─────────────────────────────────────────────────────────────────────────────
class TestManualSupplement:
    def test_supplement_adds_holding(self, engine_1336: GridEngine):
        engine_1336.manual_supplement(7, 25.0)
        assert len(engine_1336.active_holdings()) == 1
        assert engine_1336.active_holdings()[0].buy_price == pytest.approx(25.0)

    def test_supplement_occupies_grid(self, engine_1336: GridEngine):
        engine_1336.manual_supplement(7, 25.0)
        assert "7" in engine_1336.grid_occupied


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 自定义止盈
# ─────────────────────────────────────────────────────────────────────────────
class TestCustomTakeProfit:
    def test_set_custom_take_profit(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(0)
        result = engine_1336.set_custom_take_profit(holding.holding_id, 0.15)
        assert result is True
        assert holding.custom_take_profit_pct == pytest.approx(0.15)
        assert holding.effective_take_profit_pct == pytest.approx(0.15)

    def test_set_custom_take_profit_unknown_id(self, engine_1336: GridEngine):
        result = engine_1336.set_custom_take_profit("no_such_id", 0.15)
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 压力测试（风险资金）
# ─────────────────────────────────────────────────────────────────────────────
class TestRiskCapital:
    def test_risk_capital_all_empty(self, engine_1336: GridEngine):
        risk = engine_1336.compute_risk_capital()
        prices = engine_1336.grid_prices()
        expected = sum(p * engine_1336.lot_size for p in prices)
        assert risk == pytest.approx(expected, rel=1e-4)

    def test_risk_capital_decreases_after_buy(self, engine_1336: GridEngine):
        full_risk = engine_1336.compute_risk_capital()
        engine_1336.confirm_buy(0)
        partial_risk = engine_1336.compute_risk_capital()
        assert partial_risk < full_risk

    def test_risk_capital_zero_when_all_occupied(self, engine_1336: GridEngine):
        for i in range(engine_1336.grid_levels):
            engine_1336.manual_supplement(i, engine_1336.grid_prices()[i])
        assert engine_1336.compute_risk_capital() == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# GridEngine — 状态序列化 / 反序列化
# ─────────────────────────────────────────────────────────────────────────────
class TestStateSerialization:
    def test_to_state_dict_structure(self, engine_with_holdings: GridEngine):
        d = engine_with_holdings.to_state_dict()
        assert "grid_occupied" in d
        assert "holdings" in d
        assert isinstance(d["holdings"], list)

    def test_sync_state_restores_holdings(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0)
        engine_1336.confirm_buy(3)
        state_dict = engine_1336.to_state_dict()

        new_engine = GridEngine(**dict(
            code="01336", name="新华保险", base_price=28.5,
            hist_min=14.0, lot_size=500, grid_levels=20, take_profit_pct=0.07,
        ))
        new_engine.sync_state(state_dict)
        assert len(new_engine.active_holdings()) == 2
        assert "0" in new_engine.grid_occupied
        assert "3" in new_engine.grid_occupied

    def test_sync_state_empty_is_safe(self, engine_1336: GridEngine):
        engine_1336.sync_state({})
        assert engine_1336.active_holdings() == []


# ─────────────────────────────────────────────────────────────────────────────
# 全局函数
# ─────────────────────────────────────────────────────────────────────────────
class TestGlobalFunctions:
    def test_compute_total_risk_capital(self, engine_1336: GridEngine, engine_0525: GridEngine):
        total = compute_total_risk_capital({"01336": engine_1336, "00525": engine_0525})
        assert total == pytest.approx(
            engine_1336.compute_risk_capital() + engine_0525.compute_risk_capital(),
            rel=1e-6,
        )

    def test_check_cash_warning_triggered(self, engine_1336: GridEngine):
        total_risk = engine_1336.compute_risk_capital()
        assert check_cash_warning(total_risk, total_risk - 1) is True

    def test_check_cash_warning_not_triggered(self, engine_1336: GridEngine):
        total_risk = engine_1336.compute_risk_capital()
        assert check_cash_warning(total_risk, total_risk + 1) is False

    def test_check_cash_warning_equal(self, engine_1336: GridEngine):
        total_risk = engine_1336.compute_risk_capital()
        # 等于时不触发
        assert check_cash_warning(total_risk, total_risk) is False


# ─────────────────────────────────────────────────────────────────────────────
# 当前网格索引
# ─────────────────────────────────────────────────────────────────────────────
class TestCurrentGridIndex:
    def test_above_all_grids(self, engine_1336: GridEngine):
        assert engine_1336.current_grid_index(999.0) == -1

    def test_below_all_grids(self, engine_1336: GridEngine):
        assert engine_1336.current_grid_index(0.01) == engine_1336.grid_levels

    def test_at_first_grid(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        idx = engine_1336.current_grid_index(prices[0])
        assert idx == 0 or idx == -1  # 等于第0格价格时处于第0格上方

    def test_within_middle_range(self, engine_1336: GridEngine):
        prices = engine_1336.grid_prices()
        mid_price = prices[9] + 0.001
        idx = engine_1336.current_grid_index(mid_price)
        assert idx < 10  # 应在第9格以上


# ─────────────────────────────────────────────────────────────────────────────
# v1.6+ Holding.is_core 字段
# ─────────────────────────────────────────────────────────────────────────────
class TestHoldingIsCore:
    """Holding.is_core 默认值、序列化与反序列化。"""

    def test_is_core_defaults_to_false(self):
        h = Holding(grid_level=0, buy_price=28.0, lot_size=500)
        assert h.is_core is False

    def test_is_core_serializes_to_dict(self):
        h = Holding(grid_level=1, buy_price=27.0, lot_size=500, is_core=True)
        d = h.to_dict()
        assert d["is_core"] is True

    def test_is_core_false_serializes_to_dict(self):
        h = Holding(grid_level=2, buy_price=26.0, lot_size=500, is_core=False)
        d = h.to_dict()
        assert d["is_core"] is False

    def test_is_core_restores_from_dict(self):
        original = Holding(grid_level=3, buy_price=25.0, lot_size=500, is_core=True)
        restored = Holding.from_dict(original.to_dict())
        assert restored.is_core is True

    def test_is_core_false_restores_from_dict(self):
        original = Holding(grid_level=4, buy_price=24.0, lot_size=500, is_core=False)
        restored = Holding.from_dict(original.to_dict())
        assert restored.is_core is False

    def test_from_dict_missing_is_core_defaults_to_false(self):
        """旧版 state.json 中没有 is_core 字段时，应向后兼容默认为 False。"""
        data = {"holding_id": "x", "grid_level": 0, "buy_price": 28.0, "lot_size": 500,
                "buy_time": "", "take_profit_pct": 0.07, "custom_take_profit_pct": None,
                "sold": False, "sell_price": None, "sell_time": None}
        h = Holding.from_dict(data)
        assert h.is_core is False


# ─────────────────────────────────────────────────────────────────────────────
# v1.6+ toggle_core 方法
# ─────────────────────────────────────────────────────────────────────────────
class TestToggleCore:
    """GridEngine.toggle_core 切换底仓标记。"""

    def test_toggle_core_on(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(0)
        result = engine_1336.toggle_core(holding.holding_id)
        assert result is True
        assert holding.is_core is True

    def test_toggle_core_off_again(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(1)
        engine_1336.toggle_core(holding.holding_id)
        engine_1336.toggle_core(holding.holding_id)
        assert holding.is_core is False

    def test_toggle_core_unknown_id_returns_false(self, engine_1336: GridEngine):
        result = engine_1336.toggle_core("nonexistent-id")
        assert result is False

    def test_toggle_core_does_not_affect_other_holdings(self, engine_1336: GridEngine):
        h0 = engine_1336.confirm_buy(0)
        h1 = engine_1336.confirm_buy(2)
        engine_1336.toggle_core(h0.holding_id)
        assert h0.is_core is True
        assert h1.is_core is False


# ─────────────────────────────────────────────────────────────────────────────
# v1.6+ confirm_buy(actual_price) 实际成交价覆盖
# ─────────────────────────────────────────────────────────────────────────────
class TestConfirmBuyActualPrice:
    """confirm_buy 的 actual_price 参数覆盖网格触发价。"""

    def test_actual_price_overrides_grid_price(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(3, actual_price=26.88)
        assert holding.buy_price == pytest.approx(26.88)

    def test_actual_price_none_uses_grid_price(self, engine_1336: GridEngine):
        grid_price = engine_1336.grid_prices()[3]
        holding = engine_1336.confirm_buy(3, actual_price=None)
        assert holding.buy_price == pytest.approx(grid_price)

    def test_actual_price_affects_take_profit(self, engine_1336: GridEngine):
        """actual_price 覆盖后，止盈价应基于实际成交价计算。"""
        holding = engine_1336.confirm_buy(2, actual_price=30.0)
        expected_tp = round(30.0 * 1.07, 4)
        assert holding.take_profit_price == pytest.approx(expected_tp)

    def test_actual_price_lower_than_grid_price(self, engine_1336: GridEngine):
        """实际成交价可以低于网格触发价（滑点优势）。"""
        grid_price = engine_1336.grid_prices()[0]
        holding = engine_1336.confirm_buy(0, actual_price=grid_price - 0.5)
        assert holding.buy_price < grid_price


# ─────────────────────────────────────────────────────────────────────────────
# v1.6+ check_sell_signals(min_holding_limit) 底仓保护
# ─────────────────────────────────────────────────────────────────────────────
class TestSellSignalMinHolding:
    """min_holding_limit 参数：总持股 ≤ 阈值时屏蔽全部卖出信号。"""

    def test_no_sell_when_shares_below_threshold(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(0)
        tp_price = holding.take_profit_price + 0.01
        # lot_size=500, min_holding_limit=500 → 正好等于阈值，应屏蔽
        result = engine_1336.check_sell_signals(tp_price, min_holding_limit=500)
        assert result == []

    def test_no_sell_when_shares_strictly_below_threshold(self, engine_1336: GridEngine):
        holding = engine_1336.confirm_buy(0)
        tp_price = holding.take_profit_price + 0.01
        # 持股 500，阈值 1000 → 屏蔽
        result = engine_1336.check_sell_signals(tp_price, min_holding_limit=1000)
        assert result == []

    def test_sell_allowed_when_shares_above_threshold(self, engine_1336: GridEngine):
        h0 = engine_1336.confirm_buy(0)
        h1 = engine_1336.confirm_buy(2)
        tp_price = max(h0.take_profit_price, h1.take_profit_price) + 0.01
        # 持股 1000（2×500），阈值 500 → 允许卖出
        result = engine_1336.check_sell_signals(tp_price, min_holding_limit=500)
        assert len(result) >= 1

    def test_zero_threshold_does_not_protect(self, engine_1336: GridEngine):
        """阈值为 0 时不激活底仓保护，正常触发卖出。"""
        holding = engine_1336.confirm_buy(1)
        tp_price = holding.take_profit_price + 0.01
        result = engine_1336.check_sell_signals(tp_price, min_holding_limit=0)
        assert holding in result

    def test_is_core_holding_excluded_from_sell(self, engine_1336: GridEngine):
        """is_core=True 的底仓不应出现在卖出信号中。"""
        holding = engine_1336.confirm_buy(0)
        holding.is_core = True
        tp_price = holding.take_profit_price + 0.01
        result = engine_1336.check_sell_signals(tp_price, min_holding_limit=0)
        assert holding not in result

    def test_is_core_excluded_even_with_sufficient_shares(self, engine_1336: GridEngine):
        """即使总持股超过阈值，is_core=True 的持仓也不触发卖出。"""
        h0 = engine_1336.confirm_buy(0)
        h0.is_core = True
        h1 = engine_1336.confirm_buy(2)
        tp_price = max(h0.take_profit_price, h1.take_profit_price) + 0.01
        result = engine_1336.check_sell_signals(tp_price, min_holding_limit=0)
        assert h0 not in result
        assert h1 in result


# ─────────────────────────────────────────────────────────────────────────────
# v1.6+ 资产统计方法
# ─────────────────────────────────────────────────────────────────────────────
class TestPortfolioStats:
    """total_market_value / core_position_value / realized_profit。"""

    def test_total_market_value_no_holdings(self, engine_1336: GridEngine):
        assert engine_1336.total_market_value(28.0) == pytest.approx(0.0)

    def test_total_market_value_single_holding(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0)
        # lot_size=500, price=30.0 → 15000
        assert engine_1336.total_market_value(30.0) == pytest.approx(15000.0)

    def test_total_market_value_multiple_holdings(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0)
        engine_1336.confirm_buy(2)
        # 2×500×25.0 = 25000
        assert engine_1336.total_market_value(25.0) == pytest.approx(25000.0)

    def test_total_market_value_excludes_sold(self, engine_1336: GridEngine):
        h = engine_1336.confirm_buy(0)
        engine_1336.confirm_buy(1)
        engine_1336.confirm_sell(h.holding_id, 30.0)
        # 只剩 1 手活跃，500×20 = 10000
        assert engine_1336.total_market_value(20.0) == pytest.approx(10000.0)

    def test_core_position_value_only_is_core(self, engine_1336: GridEngine):
        h0 = engine_1336.confirm_buy(0)
        h0.is_core = True
        engine_1336.confirm_buy(2)  # 非底仓
        price = 28.0
        # 只有 h0 算底仓市值：500×28 = 14000
        assert engine_1336.core_position_value(price) == pytest.approx(14000.0)

    def test_core_position_value_zero_when_no_core(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0)
        engine_1336.confirm_buy(2)
        assert engine_1336.core_position_value(28.0) == pytest.approx(0.0)

    def test_realized_profit_zero_when_no_sells(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0)
        assert engine_1336.realized_profit() == pytest.approx(0.0)

    def test_realized_profit_single_sell(self, engine_1336: GridEngine):
        h = engine_1336.confirm_buy(0, actual_price=25.0)
        engine_1336.confirm_sell(h.holding_id, 27.0)
        # (27.0-25.0)*500 = 1000
        assert engine_1336.realized_profit() == pytest.approx(1000.0)

    def test_realized_profit_multiple_sells(self, engine_1336: GridEngine):
        h0 = engine_1336.confirm_buy(0, actual_price=25.0)
        h1 = engine_1336.confirm_buy(2, actual_price=24.0)
        engine_1336.confirm_sell(h0.holding_id, 27.0)  # 2*500=1000
        engine_1336.confirm_sell(h1.holding_id, 26.0)  # 2*500=1000
        assert engine_1336.realized_profit() == pytest.approx(2000.0)

    def test_realized_profit_does_not_count_active_holdings(self, engine_1336: GridEngine):
        engine_1336.confirm_buy(0, actual_price=25.0)  # 未卖出，不计入
        assert engine_1336.realized_profit() == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# v2.4 新增：PositionSummary / WatcherTarget / v2 信号方法
# ─────────────────────────────────────────────────────────────────────────────
from engine import PositionSummary, WatcherTarget, compute_total_risk_capital_v2


class TestPositionSummary:
    def test_band_shares(self):
        ps = PositionSummary(total_shares=10000, avg_cost=28.0, core_shares=2000, total_budget=300000)
        assert ps.band_shares == 8000

    def test_band_shares_zero_when_all_core(self):
        ps = PositionSummary(total_shares=5000, avg_cost=28.0, core_shares=5000)
        assert ps.band_shares == 0

    def test_cost_value(self):
        ps = PositionSummary(total_shares=10000, avg_cost=28.5)
        assert ps.cost_value == pytest.approx(285000.0)

    def test_market_value(self):
        ps = PositionSummary(total_shares=10000, avg_cost=28.5)
        assert ps.market_value(30.0) == pytest.approx(300000.0)

    def test_unrealized_pnl_positive(self):
        ps = PositionSummary(total_shares=10000, avg_cost=28.0)
        assert ps.unrealized_pnl(30.0) == pytest.approx(20000.0)

    def test_unrealized_pnl_pct(self):
        ps = PositionSummary(total_shares=10000, avg_cost=28.0)
        assert ps.unrealized_pnl_pct(30.0) == pytest.approx(2.0 / 28.0)

    def test_risk_capital_needed(self):
        ps = PositionSummary(total_shares=5000, avg_cost=28.0, total_budget=300000)
        # 现价 30：市值=150000，风险需求=150000
        assert ps.risk_capital_needed(30.0) == pytest.approx(150000.0)

    def test_risk_capital_needed_zero_when_full(self):
        ps = PositionSummary(total_shares=10000, avg_cost=30.0, total_budget=200000)
        # 市值=300000 > 预算=200000，需求=0
        assert ps.risk_capital_needed(30.0) == pytest.approx(0.0)

    def test_budget_usage_pct(self):
        ps = PositionSummary(total_shares=5000, avg_cost=28.0, total_budget=300000)
        # cost_value=140000 / 300000 ≈ 0.467
        assert ps.budget_usage_pct(30.0) == pytest.approx(140000 / 300000)

    def test_budget_usage_pct_no_budget(self):
        ps = PositionSummary(total_shares=5000, avg_cost=28.0, total_budget=0)
        assert ps.budget_usage_pct(30.0) == 0.0

    def test_serialization_roundtrip(self):
        ps = PositionSummary(total_shares=8000, avg_cost=27.5, core_shares=2000, total_budget=250000)
        restored = PositionSummary.from_dict(ps.to_dict())
        assert restored.total_shares == 8000
        assert restored.core_shares == 2000
        assert restored.total_budget == pytest.approx(250000.0)


class TestWatcherTarget:
    def test_is_opportunity_true(self):
        w = WatcherTarget(code="02800", name="盈富", akshare_code="02800", base_price=80.0)
        assert w.is_opportunity(78.0) is True

    def test_is_opportunity_exact(self):
        w = WatcherTarget(code="02800", name="盈富", akshare_code="02800", base_price=80.0)
        assert w.is_opportunity(80.0) is True

    def test_is_opportunity_false(self):
        w = WatcherTarget(code="02800", name="盈富", akshare_code="02800", base_price=80.0)
        assert w.is_opportunity(82.0) is False

    def test_is_opportunity_disabled(self):
        w = WatcherTarget(code="02800", name="盈富", akshare_code="02800", base_price=80.0, enabled=False)
        assert w.is_opportunity(75.0) is False

    def test_serialization_roundtrip(self):
        w = WatcherTarget(code="02800", name="盈富", akshare_code="02800", base_price=80.0, total_budget=100000)
        restored = WatcherTarget.from_dict(w.to_dict())
        assert restored.base_price == pytest.approx(80.0)
        assert restored.total_budget == pytest.approx(100000.0)


class TestCheckSellSignalsV2:
    def test_band_shares_zero_suppresses_sell(self, engine_1336: GridEngine):
        engine_1336.position_summary = PositionSummary(
            total_shares=2000, avg_cost=28.0, core_shares=2000
        )
        engine_1336.confirm_buy(0, actual_price=28.0)
        result = engine_1336.check_sell_signals_v2(50.0)
        assert result == []

    def test_normal_sell_triggers_without_ps(self, engine_1336: GridEngine):
        engine_1336.position_summary = None
        h = engine_1336.confirm_buy(0, actual_price=28.0)
        result = engine_1336.check_sell_signals_v2(28.0 * 1.08)
        assert len(result) == 1
        assert result[0].holding_id == h.holding_id

    def test_dy_dampening_raises_threshold(self, engine_1336: GridEngine):
        engine_1336.position_summary = PositionSummary(total_shares=1000, avg_cost=28.0, core_shares=0)
        h = engine_1336.confirm_buy(0, actual_price=28.0)
        # 止盈 7%，钝化后要 12%，以 9% 价格不应触发
        price_at_9pct = 28.0 * 1.09
        result = engine_1336.check_sell_signals_v2(price_at_9pct, dy_percentile=85.0)
        assert result == []

    def test_core_holding_never_sells(self, engine_1336: GridEngine):
        engine_1336.position_summary = PositionSummary(total_shares=2000, avg_cost=28.0, core_shares=500)
        h = engine_1336.confirm_buy(0, actual_price=28.0)
        h.is_core = True
        result = engine_1336.check_sell_signals_v2(50.0)
        assert result == []


class TestCheckBuySignalV2:
    def test_pb_fuse_blocks_buy(self, engine_1336: GridEngine):
        result = engine_1336.check_buy_signal_v2(28.0, pb_percentile=85.0)
        assert result == []

    def test_pb_below_80_allows_buy(self, engine_1336: GridEngine):
        # 价格低于所有格子，正常应触发
        result = engine_1336.check_buy_signal_v2(0.01, pb_percentile=60.0)
        assert len(result) > 0


class TestComputeTotalRiskCapitalV2:
    def test_uses_budget_formula(self, engine_1336: GridEngine):
        engine_1336.position_summary = PositionSummary(
            total_shares=5000, avg_cost=28.0, total_budget=300000
        )
        prices = {"01336": 30.0}
        risk = compute_total_risk_capital_v2({"01336": engine_1336}, prices)
        # market_value=150000, budget=300000, need=150000
        assert risk == pytest.approx(150000.0)

    def test_falls_back_to_grid_when_no_budget(self, engine_1336: GridEngine):
        engine_1336.position_summary = PositionSummary(total_shares=5000, avg_cost=28.0, total_budget=0)
        prices = {"01336": 30.0}
        grid_risk = engine_1336.compute_risk_capital()
        v2_risk = compute_total_risk_capital_v2({"01336": engine_1336}, prices)
        assert v2_risk == pytest.approx(grid_risk)


    def test_unrealized_pnl_pct_zero_avg_cost(self):
        ps = PositionSummary(total_shares=5000, avg_cost=0.0)
        assert ps.unrealized_pnl_pct(30.0) == 0.0

    def test_risk_capital_needed_zero_price(self):
        ps = PositionSummary(total_shares=5000, avg_cost=28.0, total_budget=300000)
        assert ps.risk_capital_needed(0.0) == 0.0


class TestComputeTotalRiskCapital:
    """旧版 compute_total_risk_capital（向后兼容网格压力测试）。"""

    def test_sums_grid_risk_for_all_engines(self, engine_1336: GridEngine):
        from engine import compute_total_risk_capital

        grid_risk = engine_1336.compute_risk_capital()
        total = compute_total_risk_capital({"01336": engine_1336})
        assert total == pytest.approx(grid_risk)

    def test_returns_zero_for_empty_engines(self):
        from engine import compute_total_risk_capital

        assert compute_total_risk_capital({}) == 0.0



# 补充 to_state_dict 带 position_summary 的分支（覆盖 line 468）
class TestToStateDictWithPositionSummary:
    def test_includes_position_summary_in_dict(self, engine_1336: GridEngine):
        from engine import PositionSummary

        engine_1336.position_summary = PositionSummary(
            total_shares=5000, avg_cost=28.0, core_shares=500, total_budget=300000
        )
        result = engine_1336.to_state_dict()
        assert "position_summary" in result
        assert result["position_summary"]["total_shares"] == 5000
        assert result["position_summary"]["total_budget"] == 300000.0

    def test_no_position_summary_key_when_none(self, engine_1336: GridEngine):
        engine_1336.position_summary = None
        result = engine_1336.to_state_dict()
        assert "position_summary" not in result
