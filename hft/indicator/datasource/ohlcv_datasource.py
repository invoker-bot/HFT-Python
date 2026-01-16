"""
OHLCV 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import time
from dataclasses import dataclass
from typing import Any

from ..base import BaseDataSource


@dataclass
class CandleData:
    """K线数据"""
    timestamp: float  # 秒
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_ccxt(cls, data: list) -> "CandleData":
        """从 ccxt 格式创建 [timestamp, open, high, low, close, volume]"""
        ts = data[0] if data[0] else 0
        if ts and ts > 1e12:
            ts = ts / 1000.0  # 毫秒转秒
        if not ts:
            ts = time.time()  # 无时间戳时使用当前时间
        return cls(
            timestamp=ts,
            open=data[1] or 0.0,
            high=data[2] or 0.0,
            low=data[3] or 0.0,
            close=data[4] or 0.0,
            volume=data[5] or 0.0,
        )


class OHLCVDataSource(BaseDataSource[CandleData]):
    """
    OHLCV K线数据源

    订阅交易对的 K 线数据。
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        timeframe: str = "1m",
        window: float = 86400.0,  # 默认 1 天
        ready_condition: str = "timeout < 60 and cv < 0.8 and range > 0.6",
        **kwargs,
    ):
        name = f"OHLCV:{exchange_class}:{symbol}:{timeframe}"
        super().__init__(
            name=name,
            exchange_class=exchange_class,
            symbol=symbol,
            ready_condition=ready_condition,
            window=window,
            **kwargs,
        )
        self._timeframe = timeframe

    async def _watch(self) -> None:
        """WebSocket 订阅 OHLCV"""
        exchange = self.exchange
        if exchange is None:
            raise RuntimeError("exchange not available")

        while True:
            ohlcv_list = await exchange.watch_ohlcv(self._symbol, self._timeframe)
            if ohlcv_list:
                for data in ohlcv_list:
                    candle = CandleData.from_ccxt(data)
                    self._data.append(candle.timestamp, candle)
                    self._emit_update(candle.timestamp, candle)

    async def _fetch(self) -> None:
        """REST API 获取 OHLCV"""
        exchange = self.exchange
        if exchange is None:
            return

        ohlcv_list = await exchange.fetch_ohlcv(self._symbol, self._timeframe)
        if ohlcv_list:
            for data in ohlcv_list:
                candle = CandleData.from_ccxt(data)
                self._data.append(candle.timestamp, candle)
                self._emit_update(candle.timestamp, candle)

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回 OHLCV 变量"""
        candles = list(self._data)
        if not candles:
            return {
                "ohlcv": [],
                "candle_count": 0,
                "last_close": None,
                "last_volume": None,
            }

        latest = self._data.latest
        return {
            "ohlcv": candles,
            "candle_count": len(candles),
            "last_close": latest.close if latest else None,
            "last_volume": latest.volume if latest else None,
        }
