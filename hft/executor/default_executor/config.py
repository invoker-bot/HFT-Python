"""
MarketExecutor 配置模块

Feature 0005: 支持动态参数（表达式或字面量）
"""
from typing import ClassVar, Type
from ..config import BaseExecutorConfig
from .executor import DefaultExecutor


class DefaultExecutorConfig(BaseExecutorConfig):
    """
    市价单执行器配置

    Feature 0005: 支持动态参数
    - per_order_usd 可以是数值或表达式

    Attributes:
        per_order_usd: 单笔订单大小（USD）或表达式
    """
    class_name: ClassVar[str] = "default"

    # TODO: max amount 以及 安全性裁剪

    @classmethod
    def get_class_type(cls) -> Type["DefaultExecutor"]:
        return DefaultExecutor

