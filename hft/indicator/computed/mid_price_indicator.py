"""
MidPrice 中间价指标

Feature 0005: Executor 动态条件与变量注入机制
"""
from typing import Any, Optional, TYPE_CHECKING

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.orderbook_datasource import OrderBookDataSource


class MidPriceIndicator(BaseIndicator[float]):
    """
    中间价格指标

    从 OrderBook 计算中间价。
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        order_book: str = "order_book",
        ready_condition: Optional[str] = None,
        **kwargs,
    ):
        name = f"MidPrice:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            interval=None,  # 事件驱动
            ready_condition=ready_condition,
            window=0,
            **kwargs,
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._order_book_id = order_book

    def _get_order_book_indicator(self) -> Optional["OrderBookDataSource"]:
        """获取 OrderBook 数据源"""
        if self.root is None:
            return None
        indicator_group = getattr(self.root, 'indicator_group', None)
        if indicator_group is None:
            return None
        return indicator_group.query_indicator(
            self._order_book_id,
            self._exchange_class,
            self._symbol,
        )

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回中间价变量"""
        ob_indicator = self._get_order_book_indicator()
        if ob_indicator is None or not ob_indicator.is_ready():
            return {"mid_price": None}

        ob = ob_indicator._data.latest
        if ob is None:
            return {"mid_price": None}

        return {"mid_price": ob.mid_price}
