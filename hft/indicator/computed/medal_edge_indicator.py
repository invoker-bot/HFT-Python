"""
MedalEdge 指标

Feature 0005: Executor 动态条件与变量注入机制

计算 taker 相对于 maker 的百分比优势。
原名 edge，重命名为 medal_edge 以更直观。
"""
import time
from typing import Any, Optional, TYPE_CHECKING

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.trades_datasource import TradesDataSource, TradeData


class MedalEdgeIndicator(BaseIndicator[float]):
    """
    Medal Edge 指标

    计算 taker 相对于 maker 的百分比优势。

    公式（量纲无关）：
    - 买入：edge = (p_final - vwap_buy) / p_final - taker_fee
    - 卖出：edge = (vwap_sell - p_final) / p_final - taker_fee

    正值表示 taker 有优势，如 0.001 表示 0.1%
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        trades: str = "trades",
        window: float = 60.0,
        taker_fee: float = 0.0005,
        ready_condition: Optional[str] = None,
        **kwargs,
    ):
        name = f"MedalEdge:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            interval=None,  # 事件驱动
            ready_condition=ready_condition,
            window=window,
            **kwargs,
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._trades_id = trades
        self._taker_fee = taker_fee

    def _get_trades_indicator(self) -> Optional["TradesDataSource"]:
        """获取 Trades 数据源"""
        if self.root is None:
            return None
        indicator_group = getattr(self.root, 'indicator_group', None)
        if indicator_group is None:
            return None
        return indicator_group.query_indicator(
            self._trades_id,
            self._exchange_class,
            self._symbol,
        )

    def _get_recent_trades(self) -> list["TradeData"]:
        """获取窗口内的 trades"""
        trades_indicator = self._get_trades_indicator()
        if trades_indicator is None:
            return []

        now = time.time()
        cutoff = now - self._window
        return [
            t for t in trades_indicator._data
            if t.timestamp >= cutoff
        ]

    def _calculate_edge(
        self,
        trades: list["TradeData"],
        is_buy: bool,
        current_price: float,
    ) -> float:
        """计算 taker 优势"""
        if not trades or current_price <= 0:
            return 0.0

        buy_qty = 0.0
        buy_notional = 0.0
        sell_qty = 0.0
        sell_notional = 0.0

        for trade in trades:
            if trade.side == "buy":
                buy_qty += trade.amount
                buy_notional += trade.cost
            else:
                sell_qty += trade.amount
                sell_notional += trade.cost

        if is_buy:
            if buy_qty <= 0:
                return 0.0
            vwap_buy = buy_notional / buy_qty
            edge = (current_price - vwap_buy) / current_price - self._taker_fee
        else:
            if sell_qty <= 0:
                return 0.0
            vwap_sell = sell_notional / sell_qty
            edge = (vwap_sell - current_price) / current_price - self._taker_fee

        return edge

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回 medal_edge 变量"""
        trades = self._get_recent_trades()
        if not trades:
            return {
                "medal_edge": 0.0,
                "medal_buy_edge": 0.0,
                "medal_sell_edge": 0.0,
            }

        # 使用最新成交价作为当前价格
        current_price = trades[-1].price if trades else 0.0

        buy_edge = self._calculate_edge(trades, True, current_price)
        sell_edge = self._calculate_edge(trades, False, current_price)

        # 根据 direction 返回对应方向的 edge
        edge = buy_edge if direction == 1 else sell_edge

        return {
            "medal_edge": edge,
            "medal_buy_edge": buy_edge,
            "medal_sell_edge": sell_edge,
        }
