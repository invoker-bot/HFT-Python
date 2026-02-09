"""FairPriceIndicator - 公平价格指标

用于 MarketNeutralPositions 策略，返回交易对的公平价格（mid_price）。

Feature 0013: MarketNeutralPositions 策略
"""
from typing import Any, Optional

from ..base import BaseIndicator
from ..datasource.ticker_datasource import TickerDataSource
from ...core.scope.scopes import TradingPairClassScope


class FairPriceIndicator(BaseIndicator):
    """
    公平价格指标

    从 TickerDataSource 获取 mid_price 作为公平价格。
    注入到 TradingPairClassScope 层级。
    """
    supported_scope = TradingPairClassScope

    def get_vars(self) -> dict[str, Any]:
        """返回公平价格变量"""
        ticker_ds = self.root.query_indicator(TickerDataSource, self.scope)
        if ticker_ds is None or not ticker_ds.ready:
            return {"trading_pair_std_price": None}

        data = ticker_ds.data.get_data()
        if data is None:
            return {"trading_pair_std_price": None}

        mid = data.mid_price
        if mid is None or mid <= 0:
            return {"trading_pair_std_price": None}

        return {"trading_pair_std_price": mid}
