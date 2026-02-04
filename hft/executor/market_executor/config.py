"""
MarketExecutor 配置模块

Feature 0005: 支持动态参数（表达式或字面量）
"""
from typing import TYPE_CHECKING, ClassVar, Type, Union
from pydantic import Field
from ..config import BaseExecutorConfig
from .executor import MarketExecutor


class MarketExecutorConfig(BaseExecutorConfig):
    """
    市价单执行器配置

    Feature 0005: 支持动态参数
    - per_order_usd 可以是数值或表达式

    Attributes:
        per_order_usd: 单笔订单大小（USD）或表达式
    """
    class_name: ClassVar[str] = "market"

    per_order_usd: Union[float, str] = Field(100.0, description="单笔订单大小（USD）")

    @classmethod
    def get_class_type(cls) -> Type["MarketExecutor"]:
        return MarketExecutor


__all__ = [
    "MarketExecutorConfig",
]
