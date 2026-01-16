"""
DataSource 数据源模块

Feature 0006: Indicator 与 DataSource 统一架构
Feature 0007: 移除 DataSourceGroup

DataSource 是从 exchange 获取数据的特殊 Indicator。

核心类：
- TickerDataSource: Ticker 数据源
- TradesDataSource: Trades 成交数据源
- OrderBookDataSource: OrderBook 订单簿数据源
- OHLCVDataSource: OHLCV K线数据源
- GlobalFundingRateIndicator: 全局资金费率指标
- FundingRateIndicator: 交易对级资金费率指标
"""
from .ticker_datasource import TickerDataSource, TickerData
from .trades_datasource import TradesDataSource, TradeData
from .orderbook_datasource import OrderBookDataSource, OrderBookData, OrderBookLevel
from .ohlcv_datasource import OHLCVDataSource, CandleData
from .funding_rate_datasource import GlobalFundingRateIndicator, FundingRateIndicator

__all__ = [
    "TickerDataSource",
    "TickerData",
    "TradesDataSource",
    "TradeData",
    "OrderBookDataSource",
    "OrderBookData",
    "OrderBookLevel",
    "OHLCVDataSource",
    "CandleData",
    "GlobalFundingRateIndicator",
    "FundingRateIndicator",
]
