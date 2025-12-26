"""
Trades 成交数据源
"""
from typing import Optional, Any, TYPE_CHECKING
from dataclasses import dataclass
from .base import BaseDataSource

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


@dataclass
class TradeData:
    """单笔成交数据"""
    id: str
    symbol: str
    timestamp: int
    side: str           # 'buy' or 'sell'
    price: float
    amount: float
    cost: float         # price * amount

    @classmethod
    def from_ccxt(cls, data: dict) -> "TradeData":
        return cls(
            id=str(data.get("id", "")),
            symbol=data.get("symbol", ""),
            timestamp=data.get("timestamp", 0),
            side=data.get("side", ""),
            price=float(data.get("price", 0)),
            amount=float(data.get("amount", 0)),
            cost=float(data.get("cost", 0)),
        )


class TradesDataSource(BaseDataSource[TradeData]):
    """
    成交数据源

    订阅交易对的实时成交记录
    """

    def __init__(
        self,
        exchange: "BaseExchange",
        symbol: str,
        **kwargs,
    ):
        name = f"trades:{symbol}"
        super().__init__(name=name, exchange=exchange, symbol=symbol, **kwargs)
        self._seen_ids: set[str] = set()  # 用于去重

    async def _watch(self) -> Optional[TradeData]:
        """WebSocket 订阅 trades"""
        trades = await self._exchange.exchange.watch_trades(self._symbol)
        if trades:
            # 返回最新一笔
            return TradeData.from_ccxt(trades[-1])
        return None

    async def _fetch(self) -> Optional[TradeData]:
        """REST API 获取 trades"""
        trades = await self._exchange.exchange.fetch_trades(self._symbol, limit=1)
        if trades:
            return TradeData.from_ccxt(trades[-1])
        return None

    def _get_data_id(self, data: TradeData) -> Any:
        """使用 trade id 去重"""
        return data.id

    def _process_data(self, data: TradeData) -> Optional[TradeData]:
        """去重处理"""
        if data.id in self._seen_ids:
            return None
        self._seen_ids.add(data.id)
        # 限制 seen_ids 大小
        if len(self._seen_ids) > 10000:
            # 保留最后 5000 个
            self._seen_ids = set(list(self._seen_ids)[-5000:])
        return data
