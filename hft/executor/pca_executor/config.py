"""
PCA (Position Cost Averaging) Executor 配置模块
"""
from functools import cached_property
from typing import ClassVar, Type

from pydantic import Field

from ..base_config import BaseExecutorConfig
from ..base import BaseExecutor


class PCAExecutorConfig(BaseExecutorConfig):
    """
    PCA (Position Cost Averaging) 执行器配置

    马丁格尔/DCA 风格的执行器：
    - 开仓单：在更优价格等待加仓
    - 平仓单：在盈利价格等待止盈
    - 订单挂出后不频繁变更

    加仓逻辑：
    - 第 n 次加仓金额 = base_order_usd * (amount_multiplier ^ n)
    - 第 n 次加仓距离 = spread_open * (spread_multiplier ^ n)

    Example:
        base_order_usd=100, amount_multiplier=1.5, spread_multiplier=1.2
        第0次: 100 USD @ 1.0%
        第1次: 150 USD @ 1.2%
        第2次: 225 USD @ 1.44%
    """

    class_name: ClassVar[str] = "pca"

    # 基础参数
    base_order_usd: float = Field(100.0, description="基础订单金额（USD）")
    spread_open: float = Field(0.01, description="基础开仓距离（相对当前价格）")
    spread_close: float = Field(0.02, description="平仓距离（相对成本价）")

    # 扩大系数
    amount_multiplier: float = Field(1.5, description="加仓金额扩大系数")
    spread_multiplier: float = Field(1.2, description="加仓距离扩大系数")
    max_additions: int = Field(5, description="最大加仓次数")

    # 订单管理
    timeout: float = Field(3600.0, description="订单超时时间（秒）")
    refresh_tolerance: float = Field(0.5, description="价格偏离容忍度（不频繁刷新）")

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        from .executor import PCAExecutor
        return PCAExecutor

    @cached_property
    def instance(self) -> "BaseExecutor":
        from .executor import PCAExecutor
        return PCAExecutor(config=self)


__all__ = [
    "PCAExecutorConfig",
]

