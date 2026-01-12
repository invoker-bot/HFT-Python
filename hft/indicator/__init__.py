"""
Indicator 指标模块

两种指标模式：
1. 事件驱动 (base.py): 监听 DataSource 的 update 事件
2. Lazy Start (lazy.py): 挂载到 TradingPairDataSource，轮询计算
"""
from .base import (
    IndicatorResult,
    BaseIndicator,
    SimpleIndicator,
    ChainedIndicator,
)
from .lazy_indicator import (
    LazyIndicator,
    VWAPIndicator,
    SpreadIndicator,
    MidPriceIndicator,
)
from .intensity_indicator import (
    IntensityResult,
    TradeIntensityCalculator,
    TradeIntensityIndicator,
)

__all__ = [
    # Event-driven indicators
    "IndicatorResult",
    "BaseIndicator",
    "SimpleIndicator",
    "ChainedIndicator",
    # Lazy start indicators
    "LazyIndicator",
    "VWAPIndicator",
    "SpreadIndicator",
    "MidPriceIndicator",
    # Trade intensity
    "IntensityResult",
    "TradeIntensityCalculator",
    "TradeIntensityIndicator",
]
