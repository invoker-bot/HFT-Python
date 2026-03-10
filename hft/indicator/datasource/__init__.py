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
- GlobalExchangeTickerVolumeIndicator: 全局交易量指标
- TickerVolumeIndicator: 交易对级交易量指标
"""
from .equation_datasource import *
from .funding_rate_datasource import *
from .market_info_datasource import *
from .medal_amount_datasource import *
from .ohlcv_datasource import *
from .orderbook_datasource import *
from .ticker_datasource import *
from .ticker_volume_datasource import *
from .trades_datasource import *

# __all__ = [
#     "TickerDataSource",
#     "TradesDataSource",
#     "OrderBookDataSource",
#     "OHLCVDataSource",
#     "MedalEquationDataSource",
#     "MedalAmountDataSource",
# ]
