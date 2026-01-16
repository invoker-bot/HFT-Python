"""
Volume 成交量指标

Feature 0005: Executor 动态条件与变量注入机制
"""
import time
from typing import Any, Optional, TYPE_CHECKING

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.trades_datasource import TradesDataSource, TradeData


class VolumeIndicator(BaseIndicator[float]):
    """
    成交量指标

    从 Trades 计算窗口内的成交量。
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        trades: str = "trades",
        window: float = 300.0,
        ready_condition: Optional[str] = None,
        **kwargs,
    ):
        name = f"Volume:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            interval=None,
            ready_condition=ready_condition,
            window=window,
            **kwargs,
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._trades_id = trades

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
        return [t for t in trades_indicator._data if t.timestamp >= cutoff]

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回成交量变量"""
        trades = self._get_recent_trades()
        if not trades:
            return {
                "volume": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "notional": 0.0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
            }

        buy_volume = sum(t.amount for t in trades if t.side == "buy")
        sell_volume = sum(t.amount for t in trades if t.side == "sell")
        buy_notional = sum(t.cost for t in trades if t.side == "buy")
        sell_notional = sum(t.cost for t in trades if t.side == "sell")

        return {
            "volume": buy_volume + sell_volume,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "notional": buy_notional + sell_notional,
            "buy_notional": buy_notional,
            "sell_notional": sell_notional,
        }
