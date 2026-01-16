"""
OrderBook 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import time
from dataclasses import dataclass, field
from typing import Any

from ..base import BaseDataSource


@dataclass
class OrderBookLevel:
    """订单簿单层"""
    price: float
    amount: float


@dataclass
class OrderBookData:
    """订单簿数据"""
    symbol: str
    timestamp: float  # 秒
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)

    @classmethod
    def from_ccxt(cls, data: dict) -> "OrderBookData":
        """从 ccxt 格式创建"""
        ts = data.get("timestamp") or 0
        if ts and ts > 1e12:
            ts = ts / 1000.0  # 毫秒转秒
        if not ts:
            ts = time.time()  # 无时间戳时使用当前时间

        bids = [
            OrderBookLevel(price=b[0], amount=b[1])
            for b in data.get("bids", [])
            if len(b) >= 2
        ]
        asks = [
            OrderBookLevel(price=a[0], amount=a[1])
            for a in data.get("asks", [])
            if len(a) >= 2
        ]

        return cls(
            symbol=data.get("symbol", ""),
            timestamp=ts,
            bids=bids,
            asks=asks,
        )

    @property
    def best_bid(self) -> float:
        """最优买价"""
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        """最优卖价"""
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        """中间价"""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return self.best_bid or self.best_ask or 0.0


class OrderBookDataSource(BaseDataSource[OrderBookData]):
    """
    OrderBook 数据源

    订阅交易对的实时订单簿。
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        depth: int = 20,
        ready_condition: str = "timeout < 5",
        **kwargs,
    ):
        name = f"OrderBook:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            exchange_class=exchange_class,
            symbol=symbol,
            ready_condition=ready_condition,
            window=0,  # OrderBook 不需要历史窗口
            **kwargs,
        )
        self._depth = depth

    async def _watch(self) -> None:
        """WebSocket 订阅 order book"""
        exchange = self.exchange
        if exchange is None:
            raise RuntimeError("exchange not available")

        while True:
            data = await exchange.watch_order_book(self._symbol, self._depth)
            if data:
                ob = OrderBookData.from_ccxt(data)
                self._data.append(ob.timestamp, ob)
                self._emit_update(ob.timestamp, ob)

    async def _fetch(self) -> None:
        """REST API 获取 order book"""
        exchange = self.exchange
        if exchange is None:
            return

        data = await exchange.fetch_order_book(self._symbol, self._depth)
        if data:
            ob = OrderBookData.from_ccxt(data)
            self._data.append(ob.timestamp, ob)
            self._emit_update(ob.timestamp, ob)

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回 order book 变量"""
        if not self._data:
            return {
                "order_book": None,
                "best_bid": None,
                "best_ask": None,
                "mid_price": None,
                "bid_depth": 0.0,
                "ask_depth": 0.0,
            }

        ob = self._data.latest
        return {
            "order_book": ob,
            "best_bid": ob.best_bid,
            "best_ask": ob.best_ask,
            "mid_price": ob.mid_price,
            "bid_depth": sum(b.amount for b in ob.bids),
            "ask_depth": sum(a.amount for a in ob.asks),
        }
