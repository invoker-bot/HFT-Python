"""
Indicator 工厂类

可 pickle 的 indicator 工厂，用于配置驱动创建指标。

Feature 0008: 支持 exchange_path 级别的 Indicator
Issue 0015: 支持 window duration 字符串
"""
# pylint: disable=import-outside-toplevel
import logging
from typing import Any, Optional

from .base import BaseIndicator
from ..core.duration import parse_duration

logger = logging.getLogger(__name__)


class IndicatorFactory:
    """
    可 pickle 的 Indicator 工厂

    通过类名字符串和参数创建 indicator 实例。
    """

    # 内置 indicator 类映射（延迟加载）
    _builtin_classes: dict[str, type[BaseIndicator]] = {}

    def __init__(self, class_name: str, params: dict[str, Any], ready_condition: Optional[str] = None):
        """
        Args:
            class_name: indicator 类名
            params: 创建参数（不包括 ready_condition）
            ready_condition: ready 条件表达式（单独注入）
        """
        self._class_name = class_name
        # Issue 0015: 解析 window duration 字符串
        self._params = self._normalize_params(params)
        self._ready_condition = ready_condition

    @staticmethod
    def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
        """
        归一化参数，处理 window 和 debug_log_interval duration 字符串

        Args:
            params: 原始参数

        Returns:
            归一化后的参数
        """
        normalized = params.copy()
        # 处理 window duration 字符串
        if 'window' in normalized:
            try:
                normalized['window'] = parse_duration(normalized['window'])
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse window duration: %s", e)
                # 保留原值，让后续报错

        # 处理 debug_log_interval duration 字符串
        if 'debug_log_interval' in normalized:
            try:
                normalized['debug_log_interval'] = parse_duration(normalized['debug_log_interval'])
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse debug_log_interval duration: %s", e)
                # 保留原值，让后续报错

        return normalized

    @classmethod
    def _get_builtin_classes(cls) -> dict[str, type[BaseIndicator]]:
        """延迟加载内置类映射"""
        if not cls._builtin_classes:
            from .datasource import (
                TickerDataSource,
                TradesDataSource,
                OrderBookDataSource,
                OHLCVDataSource,
                GlobalFundingRateIndicator,
                FundingRateIndicator,
                MedalEquationDataSource,
            )
            from .computed import (
                MidPriceIndicator,
                MedalEdgeIndicator,
                VolumeIndicator,
                RSIIndicator,
            )
            # Feature 0013: MarketNeutralPositions 相关
            from .fair_price_indicator import FairPriceIndicator
            from ..datasource.medal_amount_datasource import MedalAmountDataSource

            cls._builtin_classes = {
                # DataSource 类
                "TickerDataSource": TickerDataSource,
                "TradesDataSource": TradesDataSource,
                "OrderBookDataSource": OrderBookDataSource,
                "OHLCVDataSource": OHLCVDataSource,
                # FundingRate 类（Feature 0007）
                "GlobalFundingRateIndicator": GlobalFundingRateIndicator,
                "FundingRateIndicator": FundingRateIndicator,
                # ExchangePath 级别（Feature 0008）
                "MedalEquationDataSource": MedalEquationDataSource,
                # Computed Indicator 类（Feature 0005）
                "MidPriceIndicator": MidPriceIndicator,
                "MedalEdgeIndicator": MedalEdgeIndicator,
                "VolumeIndicator": VolumeIndicator,
                "RSIIndicator": RSIIndicator,
                # Feature 0013: MarketNeutralPositions 相关
                "FairPriceIndicator": FairPriceIndicator,
                "MedalAmountDataSource": MedalAmountDataSource,
            }
        return cls._builtin_classes

    def __call__(
        self,
        exchange_class: Optional[str],
        symbol: Optional[str],
        exchange_path: Optional[str] = None,
    ) -> Optional[BaseIndicator]:
        """
        创建 indicator 实例

        Args:
            exchange_class: 交易所类名
            symbol: 交易对
            exchange_path: 交易所实例路径（Feature 0008）

        Returns:
            BaseIndicator 实例，创建失败返回 None
        """
        builtin_classes = type(self)._get_builtin_classes()

        if self._class_name not in builtin_classes:
            logger.warning("Unknown indicator class: %s", self._class_name)
            return None

        indicator_class = builtin_classes[self._class_name]

        try:
            # 根据 indicator 类型构建参数
            if exchange_path is not None:
                # ExchangePath 级别的 indicator
                indicator = indicator_class(
                    exchange_path=exchange_path,
                    exchange_class=exchange_class,
                    symbol=symbol,
                    **self._params
                )
            else:
                # 其他级别的 indicator
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
