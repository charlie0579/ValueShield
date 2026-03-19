"""
engine.py - 网格引擎模块
负责网格价格计算、触发判断、持仓管理、止盈逻辑、压力测试预警。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Holding:
    """单手持仓记录。"""
    holding_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    grid_level: int = 0
    buy_price: float = 0.0
    lot_size: int = 500
    buy_time: str = ""
    take_profit_pct: float = 0.07
    custom_take_profit_pct: Optional[float] = None
    is_core: bool = False  # 底仓标记：不触发止盈提醒，热力图显示绿松石色
    sold: bool = False
    sell_price: Optional[float] = None
    sell_time: Optional[str] = None

    @property
    def effective_take_profit_pct(self) -> float:
        return self.custom_take_profit_pct if self.custom_take_profit_pct is not None else self.take_profit_pct

    @property
    def take_profit_price(self) -> float:
        return round(self.buy_price * (1 + self.effective_take_profit_pct), 4)

    @property
    def cost_value(self) -> float:
        return self.buy_price * self.lot_size

    def profit_if_sold_at(self, price: float) -> float:
        return (price - self.buy_price) * self.lot_size

    def profit_pct_if_sold_at(self, price: float) -> float:
        if self.buy_price <= 0:
            return 0.0
        return (price - self.buy_price) / self.buy_price

    def to_dict(self) -> dict:
        return {
            "holding_id": self.holding_id,
            "grid_level": self.grid_level,
            "buy_price": self.buy_price,
            "lot_size": self.lot_size,
            "buy_time": self.buy_time,
            "take_profit_pct": self.take_profit_pct,
            "custom_take_profit_pct": self.custom_take_profit_pct,
            "is_core": self.is_core,
            "sold": self.sold,
            "sell_price": self.sell_price,
            "sell_time": self.sell_time,
        }

    @staticmethod
    def from_dict(data: dict) -> "Holding":
        return Holding(
            holding_id=data.get("holding_id", str(uuid.uuid4())[:8]),
            grid_level=data.get("grid_level", 0),
            buy_price=data.get("buy_price", 0.0),
            lot_size=data.get("lot_size", 500),
            buy_time=data.get("buy_time", ""),
            take_profit_pct=data.get("take_profit_pct", 0.07),
            custom_take_profit_pct=data.get("custom_take_profit_pct", None),
            is_core=data.get("is_core", False),
            sold=data.get("sold", False),
            sell_price=data.get("sell_price", None),
            sell_time=data.get("sell_time", None),
        )


# ─────────────────────────────────────────────────────────────────────────────
# v2.4 新增：总仓位摘要 & 观察者模式
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PositionSummary:
    """
    v2.4 总仓位摘要：以「总持仓 + 总预算」为核心的仓位管理。
    取代逐格记账，支持简化录入和波段/底仓分层逻辑。
    """
    total_shares: int = 0          # 总持有股数
    avg_cost: float = 0.0          # 平均成本价（HKD）
    core_shares: int = 0           # 底仓锁定股数，系统严禁触发卖出提醒
    total_budget: float = 0.0      # 计划总投入金额（HKD）

    @property
    def band_shares(self) -> int:
        """波段仓位 = 总持有股数 - 底仓锁定股数。波段为 0 时屏蔽全部卖出。"""
        return max(0, self.total_shares - self.core_shares)

    @property
    def cost_value(self) -> float:
        """已投入总成本。"""
        return self.total_shares * self.avg_cost

    def market_value(self, price: float) -> float:
        """当前持仓总市值。"""
        return self.total_shares * price

    def unrealized_pnl(self, price: float) -> float:
        """浮动盈亏金额（正=盈，负=亏）。"""
        return (price - self.avg_cost) * self.total_shares

    def unrealized_pnl_pct(self, price: float) -> float:
        """浮动盈亏百分比（0.068 = 6.8%）。"""
        if self.avg_cost <= 0:
            return 0.0
        return (price - self.avg_cost) / self.avg_cost

    def risk_capital_needed(self, price: float) -> float:
        """
        v2.4 风险资金需求 = max(0, 总预算 - 当前持仓市值)。
        总预算未设定（= 0）时退化为 0，由 GridEngine.compute_risk_capital() 兜底。
        """
        if self.total_budget <= 0 or price <= 0:
            return 0.0
        return max(0.0, self.total_budget - self.market_value(price))

    def budget_usage_pct(self, price: float) -> float:
        """预算使用率 = min(1, 已投成本 / 总预算）。"""
        if self.total_budget <= 0:
            return 0.0
        return min(1.0, self.cost_value / self.total_budget)

    def to_dict(self) -> dict:
        return {
            "total_shares": self.total_shares,
            "avg_cost": self.avg_cost,
            "core_shares": self.core_shares,
            "total_budget": self.total_budget,
        }

    @staticmethod
    def from_dict(data: dict) -> "PositionSummary":
        return PositionSummary(
            total_shares=int(data.get("total_shares", 0)),
            avg_cost=float(data.get("avg_cost", 0.0)),
            core_shares=int(data.get("core_shares", 0)),
            total_budget=float(data.get("total_budget", 0.0)),
        )


@dataclass
class WatcherTarget:
    """
    观察者模式：零持仓监控标的。
    当现价 <= base_price 时推送 Bark「建仓机会」提醒。
    一旦用户补录首笔买入，从观察名单移入持仓名单。
    """
    code: str
    name: str
    akshare_code: str
    base_price: float              # 理想建仓价（安全边际价）
    total_budget: float = 0.0     # 计划投入金额
    enabled: bool = True

    def is_opportunity(self, price: float) -> bool:
        """判断当前价是否达到安全边际建仓买点。"""
        return self.enabled and price > 0 and price <= self.base_price

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "akshare_code": self.akshare_code,
            "base_price": self.base_price,
            "total_budget": self.total_budget,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(data: dict) -> "WatcherTarget":
        return WatcherTarget(
            code=data["code"],
            name=data["name"],
            akshare_code=data.get("akshare_code", data["code"]),
            base_price=float(data.get("base_price", 0.0)),
            total_budget=float(data.get("total_budget", 0.0)),
            enabled=bool(data.get("enabled", True)),
        )


class GridEngine:
    """
    灵活网格算法引擎。
    支持手动设置 base_price 与 step，重新计算所有格子价格。
    支持独立止盈追踪。
    """

    def __init__(
        self,
        code: str,
        name: str,
        base_price: float,
        hist_min: float,
        lot_size: int,
        grid_levels: int = 20,
        step: Optional[float] = None,
        take_profit_pct: float = 0.07,
        position_summary: Optional[PositionSummary] = None,
    ):
        self.code = code
        self.name = name
        self.base_price = base_price
        self.hist_min = hist_min
        self.lot_size = lot_size
        self.grid_levels = grid_levels
        self.take_profit_pct = take_profit_pct
        self._step = step
        self.position_summary = position_summary   # v2.4 总仓位摘要（可选）

        self.holdings: list[Holding] = []
        self.grid_occupied: dict[int, str] = {}

    @property
    def step(self) -> float:
        if self._step is not None and self._step > 0:
            return self._step
        span = self.base_price - self.hist_min
        if span <= 0:
            return 0.01
        return round(span / self.grid_levels, 4)

    def set_step(self, step: float) -> None:
        self._step = step
        logger.info("[%s] 手动设置 Step=%.4f", self.code, step)

    def set_base_price(self, base_price: float) -> None:
        self.base_price = base_price
        self._step = None
        logger.info("[%s] 重置 Base_Price=%.4f，Step 将重新计算为 %.4f", self.code, base_price, self.step)

    def grid_prices(self) -> list[float]:
        """
        返回从 base_price 向下的 grid_levels 个触发价列表。
        grid[0] = base_price - 1*step（第1格），grid[n-1] 最低档。
        """
        s = self.step
        return [round(self.base_price - (i + 1) * s, 4) for i in range(self.grid_levels)]

    def current_grid_index(self, current_price: float) -> int:
        """
        返回当前价格处于第几格（0-indexed）。
        若价格高于所有格子，返回 -1；低于所有格子，返回 grid_levels。
        """
        prices = self.grid_prices()
        for i, price in enumerate(prices):
            if current_price >= price:
                return i - 1
        return self.grid_levels

    def check_buy_signal(self, current_price: float) -> list[int]:
        """
        检查当前价是否触发了新的买入格子（价格下穿某格触发价）。
        返回触发的格子索引列表（0-indexed）。
        """
        prices = self.grid_prices()
        triggered = []
        for i, price in enumerate(prices):
            if current_price <= price and str(i) not in self.grid_occupied:
                triggered.append(i)
        return triggered

    def check_sell_signals(self, current_price: float, min_holding_limit: int = 0) -> list[Holding]:
        """
        检查当前价是否触发了已持仓的止盈卖出。
        min_holding_limit > 0 且总持股数 ≤ 阈値时，屏蔽全部卖出（底仓保护）。
        """
        if min_holding_limit > 0:
            total_shares = sum(h.lot_size for h in self.active_holdings())
            if total_shares <= min_holding_limit:
                return []  # 底仓保护：持股总数未超过阈値，屏蔽全部卖出
        to_sell = []
        for holding in self.holdings:
            if not holding.sold and not holding.is_core and current_price >= holding.take_profit_price:
                to_sell.append(holding)
        return to_sell

    def check_sell_signals_v2(
        self,
        current_price: float,
        dy_percentile: float = -1.0,
        min_holding_limit: int = 0,
    ) -> list[Holding]:
        """
        v2.4 止盈信号检查：
        - band_shares == 0 时屏蔽全部卖出（波段仓位耗尽，系统禁止触发底仓）
        - min_holding_limit > 0 且总持股 ≤ 阈值时屏蔽（v1 底仓保护向后兼容）
        - dy_percentile > 80 时「钝化」（止盈阈值 +5%，倾向收息持股）
        """
        ps = self.position_summary
        if ps is not None and ps.band_shares == 0:
            logger.debug("[%s] 波段仓位为 0，屏蔽全部卖出提醒", self.code)
            return []
        if min_holding_limit > 0:
            total_shares = sum(h.lot_size for h in self.active_holdings())
            if total_shares <= min_holding_limit:
                return []
        dampened = dy_percentile >= 0 and dy_percentile > 80.0
        to_sell = []
        for holding in self.holdings:
            if holding.sold or holding.is_core:
                continue
            effective_pct = holding.effective_take_profit_pct
            if dampened:
                effective_pct += 0.05  # 高息钝化：让利更多再卖
            if current_price >= holding.buy_price * (1 + effective_pct):
                to_sell.append(holding)
        return to_sell

    def check_buy_signal_v2(
        self,
        current_price: float,
        pb_percentile: float = -1.0,
    ) -> list[int]:
        """
        v2.4 买入信号检查：
        - pb_percentile > 80 时「熔断」（PB 历史高位，屏蔽买入防高位接盘）
        """
        if pb_percentile >= 0 and pb_percentile > 80.0:
            logger.warning(
                "[%s] PB 高估值熔断：历史分位=%.0f%%，屏蔽本次买入信号",
                self.code, pb_percentile,
            )
            return []
        return self.check_buy_signal(current_price)

    def confirm_buy(self, grid_level: int, actual_price: Optional[float] = None) -> Holding:
        """
        用户确认某格买入，记录持仓并标记格子占用。
        actual_price: 实际成交价，为 None 时使用网格触发价。
        """
        prices = self.grid_prices()
        buy_price = actual_price if actual_price is not None else prices[grid_level]
        holding = Holding(
            grid_level=grid_level,
            buy_price=buy_price,
            lot_size=self.lot_size,
            buy_time=datetime.now().isoformat(),
            take_profit_pct=self.take_profit_pct,
        )
        self.holdings.append(holding)
        self.grid_occupied[str(grid_level)] = holding.holding_id
        logger.info("[%s] 确认买入 第%d格 价格=%.4f", self.code, grid_level + 1, buy_price)
        return holding

    def confirm_sell(self, holding_id: str, sell_price: float) -> Optional[Holding]:
        """
        用户确认某持仓已卖出，更新状态。
        """
        for holding in self.holdings:
            if holding.holding_id == holding_id and not holding.sold:
                holding.sold = True
                holding.sell_price = sell_price
                holding.sell_time = datetime.now().isoformat()
                level_key = str(holding.grid_level)
                if level_key in self.grid_occupied:
                    del self.grid_occupied[level_key]
                logger.info(
                    "[%s] 确认卖出 holding_id=%s 卖价=%.4f 盈利=%.2f%%",
                    self.code, holding_id, sell_price,
                    holding.profit_pct_if_sold_at(sell_price) * 100
                )
                return holding
        logger.warning("[%s] 未找到 holding_id=%s", self.code, holding_id)
        return None

    def manual_supplement(self, grid_level: int, buy_price: float) -> Holding:
        """影子网格补录：手动补记某格的买入记录。"""
        holding = Holding(
            grid_level=grid_level,
            buy_price=buy_price,
            lot_size=self.lot_size,
            buy_time=datetime.now().isoformat(),
            take_profit_pct=self.take_profit_pct,
        )
        self.holdings.append(holding)
        self.grid_occupied[str(grid_level)] = holding.holding_id
        logger.info("[%s] 手动补录 第%d格 价格=%.4f", self.code, grid_level + 1, buy_price)
        return holding

    def compute_risk_capital(self) -> float:
        """
        压力测试：计算未占用的下方网格所需的全部资金（最坏情况）。
        = Σ (未占用格子的触发价 * 每手股数)
        """
        prices = self.grid_prices()
        total_risk = 0.0
        for i, price in enumerate(prices):
            if str(i) not in self.grid_occupied:
                total_risk += price * self.lot_size
        return round(total_risk, 2)

    def compute_risk_capital_v2(self, current_price: float) -> float:
        """
        v2.4 预算风控：max(0, 总预算 - 当前持仓市值)。
        未设置 position_summary 或 total_budget 时，退化为旧网格压力测试逻辑。
        """
        ps = self.position_summary
        if ps is not None and ps.total_budget > 0:
            return ps.risk_capital_needed(current_price)
        return self.compute_risk_capital()

    def toggle_core(self, holding_id: str) -> bool:
        """切换底仓标记。底仓不触发止盈提醒，热力图显示绿松石色（#26A69A）。"""
        for holding in self.holdings:
            if holding.holding_id == holding_id:
                holding.is_core = not holding.is_core
                return True
        return False

    def total_market_value(self, current_price: float) -> float:
        """当前持仓总市値（按现价计算）。"""
        return sum(h.lot_size * current_price for h in self.active_holdings())

    def core_position_value(self, current_price: float) -> float:
        """底仓市値（仅 is_core=True 的持仓）。"""
        return sum(h.lot_size * current_price for h in self.active_holdings() if h.is_core)

    def realized_profit(self) -> float:
        """累计已卖出的网格收益总额。"""
        return sum(
            (h.sell_price - h.buy_price) * h.lot_size
            for h in self.holdings
            if h.sold and h.sell_price is not None
        )

    def active_holdings(self) -> list[Holding]:
        return [h for h in self.holdings if not h.sold]

    def set_custom_take_profit(self, holding_id: str, custom_pct: float) -> bool:
        """针对特定持仓设置自定义止盈比例。"""
        for holding in self.holdings:
            if holding.holding_id == holding_id:
                holding.custom_take_profit_pct = custom_pct
                return True
        return False

    def sync_state(self, state_data: dict) -> None:
        """从 state.json 恢复持仓、格子占用状态及 v2.4 总仓摘要。"""
        raw_holdings = state_data.get("holdings", [])
        self.holdings = [Holding.from_dict(h) for h in raw_holdings]
        self.grid_occupied = state_data.get("grid_occupied", {})
        ps_data = state_data.get("position_summary")
        if ps_data:
            self.position_summary = PositionSummary.from_dict(ps_data)

    def to_state_dict(self) -> dict:
        """序列化为可写入 state.json 的字典（含 v2.4 总仓摘要）。"""
        result: dict = {
            "grid_occupied": self.grid_occupied,
            "holdings": [h.to_dict() for h in self.holdings],
        }
        if self.position_summary is not None:
            result["position_summary"] = self.position_summary.to_dict()
        return result


def compute_total_risk_capital(engines: dict[str, GridEngine]) -> float:
    """计算所有股票的总风险资金需求（旧网格压力测试，向后兼容）。"""
    return sum(engine.compute_risk_capital() for engine in engines.values())


def compute_total_risk_capital_v2(
    engines: dict[str, GridEngine],
    current_prices: dict[str, float],
) -> float:
    """
    v2.4 总风险预警 = Σ max(0, 各标的总预算 - 当前持仓市值)。
    未设置 total_budget 的标的退化为旧网格压力测试逻辑。
    """
    return sum(
        engine.compute_risk_capital_v2(current_prices.get(code, 0.0))
        for code, engine in engines.items()
    )


def check_cash_warning(total_risk: float, cash_reserve: float) -> bool:
    """若总风险资金超过现金预留，返回 True（触发预警）。"""
    return total_risk > cash_reserve
