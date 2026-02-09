"""
Indicator 指标模块

Feature 0006: Indicator 与 DataSource 统一架构
Feature 0005: Executor 动态条件与变量注入机制

核心类：
- BaseIndicator: 所有指标的基类，通过 scope 绑定层级
- BaseDataSource: 从 exchange 获取数据的特殊 Indicator

数据源类（Feature 0006）：
- TickerDataSource, TradesDataSource, OrderBookDataSource, OHLCVDataSource
- GlobalFundingRateIndicator: ExchangeClass 级别批量获取资金费率
- FundingRateIndicator: TradingPairClass 级别，事件驱动从全局指标获取
- MedalEquationDataSource, MedalAmountDataSource: 账户数据源

计算类指标（Feature 0005）：
- MidPriceIndicator, MedalEdgeIndicator, VolumeIndicator, RSIIndicator
- TradeIntensityIndicator, FairPriceIndicator

持久化类：
- DataListener: 数据采集监听器基类
- ExchangeFundingRateBillListener: 资金费率账单采集
- ExchangeBalanceUsdListener: 账户余额快照采集
"""

from .datasource import *
