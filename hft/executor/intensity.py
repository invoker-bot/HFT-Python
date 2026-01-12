"""
TradeIntensityCalculator - 交易强度计算器

已移动到 hft.indicator.intensity 模块。
此文件保留用于向后兼容。

新代码请使用：
    from hft.indicator import TradeIntensityCalculator, TradeIntensityIndicator, IntensityResult

或者使用指标接口：
    intensity = trading_pair.query_indicator(TradeIntensityIndicator)
"""

# 向后兼容：从 indicator 模块重新导出
from ..indicator.intensity_indicator import (
    IntensityResult,
    TradeIntensityCalculator,
    TradeIntensityIndicator,
    _get_trade_attr,
)

__all__ = [
    "IntensityResult",
    "TradeIntensityCalculator",
    "TradeIntensityIndicator",
    "_get_trade_attr",
]
