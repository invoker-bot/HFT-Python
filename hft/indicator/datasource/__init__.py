"""
DataSource 数据源模块

Feature 0006: Indicator 与 DataSource 统一架构
Feature 0007: 移除 DataSourceGroup
Feature 0008: Strategy 数据驱动增强

DataSource 是从 exchange 获取数据的特殊 Indicator。

核心类：
- TickerDataSource: Ticker 数据源
- TradesDataSource: Trades 成交数据源
- OrderBookDataSource: OrderBook 订单簿数据源
- OHLCVDataSource: OHLCV K线数据源
- GlobalFundingRateIndicator: 全局资金费率指标
- FundingRateIndicator: 交易对级资金费率指标
- MedalEquationDataSource: 账户权益数据源（ExchangePath 级别）
- MedalAmountDataSource: 账户余额数据源（ExchangePath 级别）
"""
from .ticker_datasource import TickerDataSource, TickerData
from .trades_datasource import TradesDataSource, TradeData
from .orderbook_datasource import OrderBookDataSource, OrderBookData, OrderBookLevel
from .ohlcv_datasource import OHLCVDataSource, CandleData
from .funding_rate_datasource import GlobalFundingRateIndicator, FundingRateIndicator
from .equation_datasource import MedalEquationDataSource, EquationData
from .medal_amount_datasource import MedalAmountDataSource, AmountData

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
    "MedalEquationDataSource",
    "EquationData",
    "MedalAmountDataSource",
    "AmountData",
]
