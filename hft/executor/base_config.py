"""
Executor 配置基类模块

BaseExecutorConfig 提供执行器通用配置字段：
- interval: tick 间隔（秒）
- always: 是否总是执行（忽略 delta 阈值检查）
"""
from typing import ClassVar, Type

from pydantic import Field

from ..config.base import BaseConfig
from .base import BaseExecutor


class BaseExecutorConfig(BaseConfig["BaseExecutor"]):
    """
    执行器配置基类

    Attributes:
        interval: Tick 间隔（秒）
        always: 是否总是执行（忽略 delta 阈值检查）
            - False: 只有当 |delta| >= per_order_usd 时才执行（rebalancing 模式）
            - True: 无论 delta 多大都执行（通常对应于，market making 模式）
    """

    class_dir: ClassVar[str] = "conf/executor"

    interval: float = Field(1.0, description="最小执行 间隔（秒）")
    always: bool = Field(False, description="是否总是执行（忽略 delta 阈值检查）")

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        return BaseExecutor

