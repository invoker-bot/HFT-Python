"""
Trades 数据源

Feature 0006: Indicator 与 DataSource 统一架构
"""
import asyncio
from dataclasses import dataclass
from typing import Any
from ccxt.base.types import Trade
from ccxt.base.errors import UnsubscribeError
from ...exchange.utils import sign
from .base import BaseTradingPairClassDataSource


@dataclass
class TradeData:
    """单笔成交数据"""
    id: str
    timestamp: float  # 秒
    # side: str  # "buy" or "sell"
    price: float
    amount: float  # 已经标准化后的数量, > 0 表示买入, < 0 表示卖出

    @classmethod
    def from_ccxt(cls, data: Trade, contract_size: float) -> "TradeData":
        """从 ccxt 格式创建"""
        timestamp = float(data['timestamp']) / 1000.0
        price = data["price"]
        amount = data["amount"] * contract_size
        side = data["side"]
        if side == "sell":
            direction = -1
        else:
            direction = 1
        amount = direction * abs(amount)
        return cls(
            id=str(data["id"]),
            timestamp=timestamp,
            price=price,
            amount=amount,
        )


def is_duplicate_trade(trade1: TradeData, trade2: TradeData) -> bool:
    """判断两笔成交是否重复"""
    return trade1.id == trade2.id


class TradesDataSource(BaseTradingPairClassDataSource[TradeData]):
    """
    Trades 数据源

    订阅交易对的实时成交记录。

    注意：Trades 是事件类数据，同一时间戳可能有多条记录，
    因此使用 is_duplicate_fn=lambda x, y: False 关闭去重。
    """
    DEFAULT_DUPLICATE_TIMESTAMP_DELTA = 1e-12

    async def on_tick(self):
        await super().on_tick()
        if not self.exchange.ready:
            return
        try:
            trades: list[Trade] = await asyncio.wait_for(
                self.exchange.watch_trades(self.symbol)
                , timeout=5.0)
        except asyncio.TimeoutError:
            trades: list[Trade] = await self.exchange.fetch_trades(self.symbol)
        for trade in trades:
            data = TradeData.from_ccxt(trade, contract_size=self.exchange.get_contract_size(self.symbol))
            await self.data.append(data, data.timestamp, is_duplicate_trade)

    def get_vars(self) -> dict[str, Any]:
        """返回 trades 变量"""
        result = {
            "trades_history": self.data.data_list,
        }
        data = self.data.get_data()
        if data is not None:
            result.update({
                "trades": data,
                "last_trade_time": data.timestamp,
                "last_trade_price": data.price,
                "last_trade_direction": sign(data.amount),
                "last_trade_amount": abs(data.amount),
            })

    async def on_stop(self):
        await super().on_stop()
        try:
            self.exchange.un_watch_trades(self.symbol)
        except UnsubscribeError:
            pass
