"""
Indicator 工厂类

可 pickle 的 indicator 工厂，用于配置驱动创建指标。
"""
import logging
from typing import Any, Optional

from .base import BaseIndicator

logger = logging.getLogger(__name__)


class IndicatorFactory:
    """
    可 pickle 的 Indicator 工厂

    通过类名字符串和参数创建 indicator 实例。
    """

    # 内置 indicator 类映射（延迟加载）
    _builtin_classes: dict[str, type] | None = None

    def __init__(self, class_name: str, params: dict[str, Any], ready_condition: Optional[str] = None):
        """
        Args:
            class_name: indicator 类名
            params: 创建参数（不包括 ready_condition）
            ready_condition: ready 条件表达式（单独注入）
        """
        self._class_name = class_name
        self._params = params
        self._ready_condition = ready_condition

    @classmethod
    def _get_builtin_classes(cls) -> dict[str, type]:
        """延迟加载内置类映射"""
        if cls._builtin_classes is None:
            from .datasource import (
                TickerDataSource,
                TradesDataSource,
                OrderBookDataSource,
                OHLCVDataSource,
                GlobalFundingRateIndicator,
                FundingRateIndicator,
            )
            from .computed import (
                MidPriceIndicator,
                MedalEdgeIndicator,
                VolumeIndicator,
                RSIIndicator,
            )
            cls._builtin_classes = {
                # DataSource 类
                "TickerDataSource": TickerDataSource,
                "TradesDataSource": TradesDataSource,
                "OrderBookDataSource": OrderBookDataSource,
                "OHLCVDataSource": OHLCVDataSource,
                # FundingRate 类（Feature 0007）
                "GlobalFundingRateIndicator": GlobalFundingRateIndicator,
                "FundingRateIndicator": FundingRateIndicator,
                # Computed Indicator 类（Feature 0005）
                "MidPriceIndicator": MidPriceIndicator,
                "MedalEdgeIndicator": MedalEdgeIndicator,
                "VolumeIndicator": VolumeIndicator,
                "RSIIndicator": RSIIndicator,
            }
        return cls._builtin_classes

    def __call__(
        self,
        exchange_class: Optional[str],
        symbol: Optional[str],
    ) -> Optional[BaseIndicator]:
        """
        创建 indicator 实例

        Args:
            exchange_class: 交易所类名
            symbol: 交易对

        Returns:
            BaseIndicator 实例，创建失败返回 None
        """
        builtin_classes = self._get_builtin_classes()

        if self._class_name not in builtin_classes:
            logger.warning("Unknown indicator class: %s", self._class_name)
            return None

        indicator_class = builtin_classes[self._class_name]

        try:
            indicator = indicator_class(
                exchange_class=exchange_class,
                symbol=symbol,
                **self._params
            )

            # 如果有 ready_condition，单独注入（Feature 0005）
            if self._ready_condition is not None and hasattr(indicator, 'set_ready_condition'):
                indicator.set_ready_condition(self._ready_condition)

            return indicator
        except Exception as e:
            logger.exception(
                "Failed to create indicator %s: %s",
                self._class_name, e
            )
            return None

    def __repr__(self) -> str:
        return f"IndicatorFactory({self._class_name!r}, {self._params!r})"
