"""
OHLCV K线数据源

.. deprecated::
    此模块已被 hft.indicator.datasource.ohlcv_datasource 替代。
    新代码请使用 hft.indicator.datasource.OHLCVDataSource。
    将在 Phase 3 清理时移除。
"""
from typing import Optional, Any, TYPE_CHECKING
from dataclasses import dataclass
from .base import BaseDataSource

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


@dataclass
class OHLCVData:
    """单根 K 线数据"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_ccxt(cls, data: list) -> "OHLCVData":
        """从 ccxt 格式转换 [timestamp, open, high, low, close, volume]"""
        return cls(
            timestamp=int(data[0]),
            open=float(data[1]),
            high=float(data[2]),
            low=float(data[3]),
            close=float(data[4]),
            volume=float(data[5]) if len(data) > 5 else 0.0,
        )

    @property
    def hl2(self) -> float:
        """(high + low) / 2"""
        return (self.high + self.low) / 2

    @property
    def hlc3(self) -> float:
        """(high + low + close) / 3"""
        return (self.high + self.low + self.close) / 3

    @property
    def ohlc4(self) -> float:
        """(open + high + low + close) / 4"""
        return (self.open + self.high + self.low + self.close) / 4


class OHLCVDataSource(BaseDataSource[OHLCVData]):
    """
    OHLCV K线数据源

    支持初始化时获取历史数据
    """

    def __init__(
        self,
        exchange: "BaseExchange",
        symbol: str,
        timeframe: str = "1m",
        initial_limit: int = 100,
        **kwargs,
    ):
        name = f"ohlcv:{symbol}:{timeframe}"
        super().__init__(name=name, exchange=exchange, symbol=symbol, **kwargs)
        self._timeframe = timeframe
        self._initial_limit = initial_limit
        self._initialized = False

    @property
    def timeframe(self) -> str:
        return self._timeframe

    async def _watch(self) -> Optional[OHLCVData]:
        """WebSocket 订阅 OHLCV"""
        ohlcv_list = await self._exchange.watch_ohlcv(
            self._symbol,
            self._timeframe
        )
        if ohlcv_list:
            return OHLCVData.from_ccxt(ohlcv_list[-1])
        return None

    async def _fetch(self) -> Optional[OHLCVData]:
        """REST API 获取 OHLCV"""
        ohlcv_list = await self._exchange.fetch_ohlcv(
            self._symbol,
            self._timeframe,
            limit=1
        )
        if ohlcv_list:
            return OHLCVData.from_ccxt(ohlcv_list[-1])
        return None

    def _get_data_id(self, data: OHLCVData) -> Any:
        """使用 timestamp 去重"""
        return data.timestamp

    def _process_data(self, data: OHLCVData) -> Optional[OHLCVData]:
        """直接返回"""
        return data

    async def fetch_initial(self, limit: int = 0) -> list[OHLCVData]:
        """
        初始化时获取历史 K 线

        如果 limit 为 0，使用构造时的 initial_limit
        """
        if limit == 0:
            limit = self._initial_limit

        ohlcv_list = await self._exchange.fetch_ohlcv(
            self._symbol,
            self._timeframe,
            limit=limit
        )

        result = []
        for ohlcv in ohlcv_list:
            data = OHLCVData.from_ccxt(ohlcv)
            if self._add_to_cache(data):
                result.append(data)

        self._initialized = True
        return result

    async def on_start(self) -> None:
        """启动时获取初始数据"""
        if not self._initialized:
            await self.fetch_initial()
