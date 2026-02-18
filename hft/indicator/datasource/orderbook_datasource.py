"""
OrderBook 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional
from ccxt.base.types import OrderBook
from ccxt.base.errors import UnsubscribeError
from ..base import BaseTradingPairClassDataIndicator


@dataclass
class OrderBookLevel:
    """订单簿单层"""
    price: float
    amount: float


@dataclass
class OrderBookData:
    """订单簿数据"""
    timestamp: float  # 秒
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)  # price, amount

    @classmethod
    def from_ccxt(cls, data: OrderBook, contract_size: float) -> "OrderBookData":
        """从 ccxt 格式创建"""
        ts = float(data["timestamp"]) / 1000.0
        bids = [OrderBookLevel(price=price, amount=amount * contract_size) for price, amount in data["bids"]]
        asks = [OrderBookLevel(price=price, amount=amount * contract_size) for price, amount in data["asks"]]

        return cls(
            timestamp=ts,
            bids=bids,
            asks=asks,
        )

    @property
    def best_bid(self) -> Optional[float]:
        """最优买价"""
        return self.bids[0].price if self.bids else None
    @property
    def best_ask(self) -> Optional[float]:
        """最优卖价"""
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        """中间价"""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return self.best_bid or self.best_ask or None


class OrderBookDataSource(BaseTradingPairClassDataIndicator[OrderBookData]):
    """
    OrderBook 数据源

    订阅交易对的实时订单簿。
    """
    DEFAULT_HEALTHY_WINDOW = 60.0  # 最小健康窗口 1 分钟

    @property
    def interval(self) -> float:
        return 0.01

    async def on_tick(self):
        if not self.exchange.ready:
            return
        try:
            ob: OrderBook = await asyncio.wait_for(
                self.exchange.watch_order_book(self.symbol, limit=400)  # using much order levels
                , timeout=5.0)
        except asyncio.TimeoutError:
            ob: OrderBook = await self.exchange.fetch_order_book(self.symbol, limit=400)

        contract_size = await self.exchange.get_contract_size_async(self.symbol)
        data = OrderBookData.from_ccxt(ob, contract_size=contract_size)
        await self.data.update(data, data.timestamp)

    def get_vars(self) -> dict[str, Any]:
        """返回 order book 变量"""
        result = {}
        if self.is_array:
            result["order_book_history"] = self.data.data_list
        data = self.data.get_data()
        if data is not None:
            result.update({
                "order_book": data,
                "best_bid_price": data.best_bid,
                "best_ask_price": data.best_ask,
                "mid_price": data.mid_price,
                "bid_depth": sum(b.amount for b in data.bids),
                "ask_depth": sum(a.amount for a in data.asks),
            })
            return result
        raise ValueError("Order book data is not available")

    async def on_stop(self):
        await super().on_stop()
        try:
            await self.exchange.un_watch_order_book(self.symbol)
        except UnsubscribeError:
            pass
