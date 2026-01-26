"""
Trades 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import time
from dataclasses import dataclass
from typing import Any

from hft.core.healthy_data import never_duplicate

from ..base import BaseDataSource


@dataclass
class TradeData:
    """单笔成交数据"""
    id: str
    symbol: str
    timestamp: float  # 秒
    side: str  # "buy" or "sell"
    price: float
    amount: float  # 已经标准化后的数量
    cost: float  # price * amount

    @classmethod
    def from_ccxt(cls, data: dict) -> "TradeData":
        """从 ccxt 格式创建"""
        ts = data.get("timestamp") or 0
        if ts and ts > 1e12:
            ts = ts / 1000.0  # 毫秒转秒
        if not ts:
            ts = time.time()  # 无时间戳时使用当前时间
        price = data.get("price", 0.0) or 0.0
        amount = data.get("amount", 0.0) or 0.0
        return cls(
            id=data.get("id", ""),
            symbol=data.get("symbol", ""),
            timestamp=ts,
            side=data.get("side", ""),
            price=price,
            amount=amount,
            cost=data.get("cost", 0.0) or (price * amount),
        )


class TradesDataSource(BaseDataSource[TradeData]):
    """
    Trades 数据源

    订阅交易对的实时成交记录。

    注意：Trades 是事件类数据，同一时间戳可能有多条记录，
    因此使用 is_duplicate_fn=lambda x, y: False 关闭去重。
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        window: float = 300.0,
        ready_condition: str = "timeout < 60 and cv < 0.8 and range > 0.6",
        **kwargs,
    ):
        name = f"Trades:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            exchange_class=exchange_class,
            symbol=symbol,
            ready_condition=ready_condition,
            window=window,
            **kwargs,
        )
        # Trades 是事件类数据，关闭去重（使用可 pickle 的函数）
        self._data._is_duplicate_fn = _never_duplicate

    async def _watch(self) -> None:
        """WebSocket 订阅 trades"""
        exchange = self.exchange
        if exchange is None:
            raise RuntimeError("exchange not available")

        while True:
            trades = await exchange.watch_trades(self._symbol)
            if trades:
                for data in trades:
                    trade = TradeData.from_ccxt(data)
                    self._data.append(trade.timestamp, trade)
                    self._emit_update(trade.timestamp, trade)

    async def _fetch(self) -> None:
        """REST API 获取 trades"""
        exchange = self.exchange
        if exchange is None:
            return

        trades = await exchange.fetch_trades(self._symbol)
        if trades:
            for data in trades:
                trade = TradeData.from_ccxt(data)
                self._data.append(trade.timestamp, trade)
                self._emit_update(trade.timestamp, trade)

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回 trades 变量"""
        trades = list(self._data)
        if not trades:
            return {
                "trades": [],
                "trade_count": 0,
                "last_trade_price": None,
                "last_trade_side": None,
            }

        latest = self._data.latest
        return {
            "trades": trades,
            "trade_count": len(trades),
            "last_trade_price": latest.price if latest else None,
            "last_trade_side": latest.side if latest else None,
        }
