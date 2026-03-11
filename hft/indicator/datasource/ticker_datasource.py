"""
Ticker 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import asyncio
from dataclasses import dataclass
from typing import Any
from ccxt.base.types import Ticker
from ccxt.base.errors import UnsubscribeError
from ..base import BaseTradingPairClassDataIndicator


@dataclass
class TickerData:
    """Ticker 数据"""
    timestamp: float  # 秒
    last: float
    bid: float
    ask: float
    amount: float = 0.0  # 24h base volume
    quote_amount: float = 0.0  # 24h quote volume

    @classmethod
    def from_ccxt(cls, data: Ticker, contract_size: float) -> "TickerData":
        """从 ccxt 格式创建"""
        timestamp = float(data['timestamp']) / 1000.0
        return cls(timestamp=timestamp,
            last=data["last"],
            bid=data["bid"],
            ask=data["ask"],
            amount=(data.get("baseVolume", 0.0) or 0.0) * contract_size,
            quote_amount=data.get("quoteVolume", 0.0) or 0.0,
        )

    @property
    def mid_price(self) -> float:
        """中间价"""
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        if self.last:
            return self.last
        # bid=0 且 last=0，但 ask>0 时使用 ask 作为 fallback
        if self.ask:
            return self.ask
        return 0


class TickerDataSource(BaseTradingPairClassDataIndicator[TickerData]):
    """
    Ticker 数据源

    订阅交易对的实时价格信息。
    """
    DEFAULT_HEALTHY_WINDOW = 60.0  # 最小健康窗口 1 分钟

    @property
    def interval(self) -> float:
        return 0.001

    async def on_tick(self):
        if not self.exchange.ready:
            return
        try:
            ticker: Ticker = await asyncio.wait_for(
                self.exchange.watch_ticker(self.symbol)
                , timeout=5.0)
        except asyncio.TimeoutError:
            ticker: Ticker = await self.exchange.fetch_ticker(self.symbol)
        # print("ticker:", self.symbol, ticker)
        contract_size = await self.exchange.get_contract_size_async(self.symbol)
        data = TickerData.from_ccxt(ticker, contract_size=contract_size)
        await self.data.update(data, data.timestamp)

    def get_vars(self) -> dict[str, Any]:
        """返回 ticker 变量"""
        result = {}
        if self.is_array:
            result["ticker_history"] = self.data.data_list
        data = self.data.get_data()
        if data is not None:
            result.update({
                "ticker": data,
                "last_price": data.last,
                "bid_price": data.bid,
                "ask_price": data.ask,
                "amount_1d": data.amount,
                "quote_amount_1d": data.quote_amount,
                "mid_price": data.mid_price,
            })
            return result
        else:
            raise ValueError("Ticker data is not available")

    async def on_stop(self):
        await super().on_stop()
        try:
            await self.exchange.un_watch_ticker(self.symbol)
        except UnsubscribeError:
            pass
