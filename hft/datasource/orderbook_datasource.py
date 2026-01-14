"""
OrderBook 订单簿数据源
"""
from typing import Optional, Any, TYPE_CHECKING
from dataclasses import dataclass, field
from .base import BaseDataSource

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


@dataclass
class OrderBookData:
    """订单簿数据"""
    symbol: str
    timestamp: int
    bids: list[list[float]] = field(default_factory=list)  # [[price, amount], ...]
    asks: list[list[float]] = field(default_factory=list)  # [[price, amount], ...]
    nonce: Optional[int] = None

    @classmethod
    def from_ccxt(cls, data: dict) -> "OrderBookData":
        return cls(
            symbol=data.get("symbol", ""),
            timestamp=data.get("timestamp", 0),
            bids=data.get("bids", []),
            asks=data.get("asks", []),
            nonce=data.get("nonce"),
        )

    @property
    def best_bid(self) -> Optional[float]:
        """最高买价"""
        if self.bids:
            return self.bids[0][0]
        return None

    @property
    def best_ask(self) -> Optional[float]:
        """最低卖价"""
        if self.asks:
            return self.asks[0][0]
        return None

    @property
    def best_bid_amount(self) -> Optional[float]:
        """最高买价的数量"""
        if self.bids:
            return self.bids[0][1]
        return None

    @property
    def best_ask_amount(self) -> Optional[float]:
        """最低卖价的数量"""
        if self.asks:
            return self.asks[0][1]
        return None

    @property
    def spread(self) -> Optional[float]:
        """买卖价差"""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_percent(self) -> Optional[float]:
        """买卖价差百分比"""
        if self.best_bid and self.best_ask and self.best_bid > 0:
            return (self.best_ask - self.best_bid) / self.best_bid * 100
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """中间价"""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    def bid_depth(self, levels: int = 5) -> float:
        """买单深度（前 N 档总量）"""
        return sum(bid[1] for bid in self.bids[:levels])

    def ask_depth(self, levels: int = 5) -> float:
        """卖单深度（前 N 档总量）"""
        return sum(ask[1] for ask in self.asks[:levels])

    def imbalance(self, levels: int = 5) -> float:
        """
        订单簿不平衡度

        返回 -1 到 1 之间的值
        正值表示买方力量强，负值表示卖方力量强
        """
        bid_depth = self.bid_depth(levels)
        ask_depth = self.ask_depth(levels)
        total = bid_depth + ask_depth
        if total == 0:
            return 0.0
        return (bid_depth - ask_depth) / total


class OrderBookDataSource(BaseDataSource[OrderBookData]):
    """
    订单簿数据源

    订阅交易对的实时订单簿
    """

    def __init__(
        self,
        exchange: "BaseExchange",
        symbol: str,
        limit: int = 20,
        **kwargs,
    ):
        name = f"orderbook:{symbol}"
        super().__init__(name=name, exchange=exchange, symbol=symbol, **kwargs)
        self._limit = limit

    async def _watch(self) -> Optional[OrderBookData]:
        """WebSocket 订阅订单簿"""
        data = await self._exchange.watch_order_book(
            self._symbol,
            limit=self._limit
        )
        return OrderBookData.from_ccxt(data) if data else None

    async def _fetch(self) -> Optional[OrderBookData]:
        """REST API 获取订单簿"""
        data = await self._exchange.fetch_order_book(
            self._symbol,
            limit=self._limit
        )
        return OrderBookData.from_ccxt(data) if data else None

    def _get_data_id(self, data: OrderBookData) -> Any:
        """使用 timestamp + nonce 去重"""
        return (data.timestamp, data.nonce)

    def _process_data(self, data: OrderBookData) -> Optional[OrderBookData]:
        """直接返回"""
        return data
