"""
Ticker 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..base import BaseDataSource


@dataclass
class TickerData:
    """Ticker 数据"""
    symbol: str
    timestamp: float  # 秒
    last: float
    bid: float
    ask: float
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    quote_volume: float = 0.0

    @classmethod
    def from_ccxt(cls, data: dict) -> "TickerData":
        """从 ccxt 格式创建"""
        ts = data.get("timestamp") or 0
        if ts and ts > 1e12:
            ts = ts / 1000.0  # 毫秒转秒
        if not ts:
            ts = time.time()  # 无则用当前时间
        return cls(
            symbol=data.get("symbol", ""),
            timestamp=ts,
            last=data.get("last", 0.0) or 0.0,
            bid=data.get("bid", 0.0) or 0.0,
            ask=data.get("ask", 0.0) or 0.0,
            high=data.get("high", 0.0) or 0.0,
            low=data.get("low", 0.0) or 0.0,
            volume=data.get("baseVolume", 0.0) or 0.0,
            quote_volume=data.get("quoteVolume", 0.0) or 0.0,
        )


class TickerDataSource(BaseDataSource[TickerData]):
    """
    Ticker 数据源

    订阅交易对的实时价格信息。
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        ready_condition: str = "timeout < 10",
        **kwargs,
    ):
        name = f"Ticker:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            exchange_class=exchange_class,
            symbol=symbol,
            ready_condition=ready_condition,
            window=0,  # Ticker 不需要历史窗口
            **kwargs,
        )

    async def _watch(self) -> None:
        """WebSocket 订阅 ticker"""
        exchange = self.exchange
        if exchange is None:
            raise RuntimeError("exchange not available")

        while True:
            data = await exchange.watch_ticker(self._symbol)
            if data:
                ticker = TickerData.from_ccxt(data)
                self._data.append(ticker.timestamp, ticker)
                self._emit_update(ticker.timestamp, ticker)

    async def _fetch(self) -> None:
        """REST API 获取 ticker"""
        exchange = self.exchange
        if exchange is None:
            return

        data = await exchange.fetch_ticker(self._symbol)
        if data:
            ticker = TickerData.from_ccxt(data)
            self._data.append(ticker.timestamp, ticker)
            self._emit_update(ticker.timestamp, ticker)

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回 ticker 变量"""
        if not self._data:
            return {}

        ticker = self._data.latest
        return {
            "last": ticker.last,
            "bid": ticker.bid,
            "ask": ticker.ask,
            "mid": (ticker.bid + ticker.ask) / 2 if ticker.bid and ticker.ask else ticker.last,
            "spread": (ticker.ask - ticker.bid) / ticker.bid if ticker.bid else 0.0,
        }
