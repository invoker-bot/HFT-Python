"""
DataSource 数据源模块

.. deprecated::
    DataSourceGroup, TradingPairDataSource, DataType, DataArray 已废弃。
    请使用 hft.indicator 模块中的 IndicatorGroup 和 BaseIndicator。
"""
from .base import BaseDataSource, DataSourceState
from .ticker_datasource import TickerDataSource
from .trades_datasource import TradesDataSource
from .ohlcv_datasource import OHLCVDataSource
from .orderbook_datasource import OrderBookDataSource
from .funding_rate_datasource import FundingRateDataSource
from .funding_rate_fetcher import GlobalFundingRateFetcher

__all__ = [
    # Base
    "BaseDataSource",
    "DataSourceState",
    # DataSources
    "TickerDataSource",
    "TradesDataSource",
    "OHLCVDataSource",
    "OrderBookDataSource",
    # Funding Rate
    "FundingRateDataSource",
    "GlobalFundingRateFetcher",
]
