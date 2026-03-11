"""MarketNeutralPositionsStrategy - 市场中性对冲策略

基于 flow + vars 表达式实现的市场中性策略。
所有计算逻辑在 YAML flow 配置中完成，策略类仅提供骨架。

Feature 0013: MarketNeutralPositions 策略
"""
from typing import ClassVar, Type

from .base import BaseStrategy
from .config import BaseStrategyConfig


class MarketNeutralPositionsConfig(BaseStrategyConfig):
    """
    MarketNeutralPositions 策略配置

    所有参数通过 flow vars 配置，不需要额外的 Python 字段。
    """
    class_name: ClassVar[str] = "market_neutral_positions"
    class_dir: ClassVar[str] = "conf/strategy/market_neutral_positions"

    @classmethod
    def get_class_type(cls) -> Type["MarketNeutralPositionsStrategy"]:
        return MarketNeutralPositionsStrategy


class MarketNeutralPositionsStrategy(BaseStrategy):
    """
    市场中性对冲策略

    所有计算通过 flow 配置完成：
    - GlobalScope: risk_ratio, max_position_usd
    - TradingPairClassScope: fair_price → direction → ratio → position_usd
    - TradingPairScope: amount（当前仓位）

    Executor 读取 flow 变量，通过 trade_intensity 计算 spread 并挂单。
    """

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.config: MarketNeutralPositionsConfig = kwargs['config']

    async def on_tick(self) -> bool:
        return False
