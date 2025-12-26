"""
Ticker 数据源
"""
from typing import Optional, Any, TYPE_CHECKING
from .base import BaseDataSource

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class TickerData(dict):
    """Ticker 数据（兼容 ccxt 格式）"""

    @property
    def symbol(self) -> str:
        return self.get("symbol", "")

    @property
    def timestamp(self) -> int:
        return self.get("timestamp", 0)

    @property
    def last(self) -> float:
        return self.get("last", 0.0)

    @property
    def bid(self) -> float:
        return self.get("bid", 0.0)

    @property
    def ask(self) -> float:
        return self.get("ask", 0.0)

    @property
    def high(self) -> float:
        return self.get("high", 0.0)

    @property
    def low(self) -> float:
        return self.get("low", 0.0)

    @property
    def volume(self) -> float:
        return self.get("baseVolume", 0.0)

    @property
    def quote_volume(self) -> float:
        return self.get("quoteVolume", 0.0)


class TickerDataSource(BaseDataSource[TickerData]):
    """
    Ticker 数据源

    订阅交易对的实时价格信息
    """

    def __init__(
        self,
        exchange: "BaseExchange",
        symbol: str,
        **kwargs,
    ):
        name = f"ticker:{symbol}"
        super().__init__(name=name, exchange=exchange, symbol=symbol, **kwargs)

    async def _watch(self) -> Optional[TickerData]:
        """WebSocket 订阅 ticker"""
        data = await self._exchange.exchange.watch_ticker(self._symbol)
        return TickerData(data) if data else None

    async def _fetch(self) -> Optional[TickerData]:
        """REST API 获取 ticker"""
        data = await self._exchange.exchange.fetch_ticker(self._symbol)
        return TickerData(data) if data else None

    def _get_data_id(self, data: TickerData) -> Any:
        """使用 timestamp 去重"""
        return data.timestamp

    def _process_data(self, data: TickerData) -> Optional[TickerData]:
        """直接返回"""
        return data
