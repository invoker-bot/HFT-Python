"""
MarketExecutor 配置模块

Feature 0005: 支持动态参数（表达式或字面量）
"""
from typing import ClassVar, Optional, Type
from ..config import BaseExecutorConfig
from .executor import DefaultExecutor


class DefaultExecutorConfig(BaseExecutorConfig):
    """
    市价单执行器配置

    Feature 0005: 支持动态参数
    - per_order_usd 可以是数值或表达式

    Attributes:
        per_order_usd: 单笔订单大小（USD）或表达式
        max_order_usd: 单笔订单金额上限（USD），超限裁剪
        max_position_usd: 单交易对仓位上限（USD），超限跳过下单
    """
    class_name: ClassVar[str] = "default"

    max_order_usd: Optional[float] = None
    max_position_usd: Optional[float] = None

    # TODO: max_imbalance_usd - 跨交易所仓位不平衡保护（需在 TradingPairClassScope 层汇总）

    @classmethod
    def get_class_type(cls) -> Type["DefaultExecutor"]:
        return DefaultExecutor

