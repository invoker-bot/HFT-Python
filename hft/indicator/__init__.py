"""
Indicator 指标模块

Feature 0006: Indicator 与 DataSource 统一架构
Feature 0005: Executor 动态条件与变量注入机制

核心类：
- BaseIndicator: 所有指标的基类
- GlobalIndicator: 全局唯一的指标
- BaseDataSource: 从 exchange 获取数据的特殊 Indicator

数据源类（Feature 0006）：
- TickerDataSource, TradesDataSource, OrderBookDataSource, OHLCVDataSource

计算类指标（Feature 0005）：
- MidPriceIndicator, MedalEdgeIndicator, VolumeIndicator, RSIIndicator

持久化类：
- DataListener: 数据采集监听器基类
- ExchangeFundingRateBillListener: 资金费率账单采集
- ExchangeBalanceUsdListener: 账户余额快照采集
- FundingRatePersistListener: 资金费率快照持久化

兼容类（待迁移）：
- LazyIndicator: 挂载到 TradingPairDataSource，轮询计算
"""
from .base import (DEFAULT_EXPIRE_SECONDS, GLOBAL_EXPIRE_SECONDS,
                   BaseDataSource, BaseIndicator, GlobalIndicator)
# Feature 0005: Computed indicators
from .computed import MedalEdgeIndicator
from .computed import MidPriceIndicator as ComputedMidPriceIndicator
from .computed import RSIIndicator, VolumeIndicator
# Feature 0006: DataSource classes
from .datasource import (CandleData, OHLCVDataSource, OrderBookData,
                         OrderBookDataSource, OrderBookLevel, TickerData,
                         TickerDataSource, TradeData, TradesDataSource)
from .group import GlobalIndicators, IndicatorGroup, TradingPairIndicators
from .intensity_indicator import (IntensityResult, TradeIntensityCalculator,
                                  TradeIntensityIndicator)
# Legacy lazy indicators
from .lazy_indicator import MidPriceIndicator  # Legacy version
from .lazy_indicator import LazyIndicator, SpreadIndicator, VWAPIndicator
from .persist import (DataListener, ExchangeBalanceUsdListener,
                      ExchangeFundingRateBillListener,
                      FundingRatePersistListener)

__all__ = [
    # Feature 0006 core classes
    "BaseIndicator",
    "GlobalIndicator",
    "BaseDataSource",
    "DEFAULT_EXPIRE_SECONDS",
    "GLOBAL_EXPIRE_SECONDS",
    # Indicator management
    "IndicatorGroup",
    "TradingPairIndicators",
    "GlobalIndicators",
    # Feature 0006: DataSource classes
    "TickerDataSource",
    "TickerData",
    "TradesDataSource",
    "TradeData",
    "OrderBookDataSource",
    "OrderBookData",
    "OrderBookLevel",
    "OHLCVDataSource",
    "CandleData",
    # Feature 0005: Computed indicators
    "ComputedMidPriceIndicator",
    "MedalEdgeIndicator",
    "VolumeIndicator",
    "RSIIndicator",
    # Lazy start indicators (legacy)
    "LazyIndicator",
    "VWAPIndicator",
    "SpreadIndicator",
    "MidPriceIndicator",
    # Trade intensity
    "IntensityResult",
    "TradeIntensityCalculator",
    "TradeIntensityIndicator",
    # Data persistence
    "DataListener",
    "ExchangeFundingRateBillListener",
    "ExchangeBalanceUsdListener",
    "FundingRatePersistListener",
]
