"""
OHLCV 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import asyncio
from dataclasses import dataclass
from typing import Any
from ccxt.base.errors import UnsubscribeError
from ...core.duration import parse_duration
from ..base import BaseTradingPairClassDataIndicator


@dataclass
class CandleData:
    """K线数据"""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_ccxt(cls, data: list, contract_size: float) -> "CandleData":
        """从 ccxt [timestamp, open, high, low, close, volume]"""
        ts, o, h, l, c, v = data
        timestamp = ts / 1000.0
        return cls(
            timestamp=timestamp,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v * contract_size,
        )


class OHLCVDataSource(BaseTradingPairClassDataIndicator[CandleData]):
    """
    OHLCV K线数据源

    订阅交易对的 K 线数据。
    """
    DEFAULT_WINDOW = 24 * 3600.0 # 默认 24 小时的数据窗口
    DEFAULT_HEALTHY_WINDOW = 3600.0  # 最小健康窗口 1 小时

    @property
    def interval(self) -> float:
        return 1.0

    def initialize(self, **kwargs) -> None:
        super().initialize(**kwargs)
        self.timeframe: str = kwargs.get("timeframe", "1m")

    async def on_start(self):  # TODO: 需要在 AppCore.on_start() 后才能获取 timeframe，是否需要重构生命周期？
        await super().on_start()
        self.data.max_age = parse_duration(self.timeframe)

    async def update_by_fetch(self):
        ohlcvs: list = await self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe)
        datas = []
        contract_size = await self.exchange.get_contract_size_async(self.symbol)
        for ohlcv in ohlcvs:
            candle_data = CandleData.from_ccxt(ohlcv, contract_size=contract_size)
            datas.append((candle_data, candle_data.timestamp))
        await self.data.assign(datas)

    async def on_tick(self):
        if not self.exchange.ready:
            return
        try:
            if len(self.data) > 0:
                contract_size = await self.exchange.get_contract_size_async(self.symbol)
                ohlcvs: list = await asyncio.wait_for(self.exchange.watch_ohlcv(self.symbol, timeframe=self.timeframe),
                                                      timeout=self.data.max_age)
                if len(ohlcvs) > 0:
                    ohlcv = ohlcvs[-1]
                    candle_data = CandleData.from_ccxt(
                        ohlcv, contract_size=contract_size)
                    await self.data.update(candle_data, candle_data.timestamp)
            else:
                await self.update_by_fetch()
        except asyncio.TimeoutError:
            await self.update_by_fetch()

    def get_vars(self) -> dict[str, Any]:
        """返回 OHLCV 变量"""
        result = {}
        if self.is_array:
            result["ohlcv_history"] = self.data.data_list
        data = self.data.get_data()
        if data is not None:
            result.update({
                "ohlcv": data,
                "last_close_price": data.close,
                "last_open_price": data.open,
                "last_high_price": data.high,
                "last_low_price": data.low,
                "last_volume": data.volume,
            })
            return result
        else:
            raise ValueError("OHLCV data is not available")

    async def on_stop(self):
        await super().on_stop()
        try:
            await self.exchange.un_watch_ohlcv(self.symbol, timeframe=self.timeframe)
        except UnsubscribeError:
            pass
