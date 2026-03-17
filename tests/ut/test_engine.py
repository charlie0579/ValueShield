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
