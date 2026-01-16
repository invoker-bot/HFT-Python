"""
LimitExecutor 配置模块

Feature 0005: 支持动态参数（表达式或字面量）
"""
from typing import TYPE_CHECKING, ClassVar, Type, Union

from pydantic import BaseModel, Field

from ..base_config import BaseExecutorConfig

if TYPE_CHECKING:
    from .executor import LimitExecutor


class LimitOrderLevel(BaseModel):
    """
    单层限价单配置

    Feature 0005: 支持动态参数
    - 字符串类型参数会作为表达式求值
    - 数值类型参数直接使用

    Attributes:
        reverse: 是否反向订单（用于对冲）
        spread: 距离当前价格的绝对价差或表达式
        refresh_tolerance: 刷新容忍度
        timeout: 订单超时时间（秒）
        per_order_usd: 该层订单的 USD 价值
    """

    reverse: Union[bool, str] = Field(False, description="是否反向订单")
    spread: Union[float, str] = Field(description="绝对价差或表达式")
    refresh_tolerance: Union[float, str] = Field(0.5, description="刷新容忍度")
    timeout: Union[float, str] = Field(60.0, description="订单超时（秒）")
    per_order_usd: Union[float, str] = Field(100.0, description="单笔订单 USD")


class LimitExecutorConfig(BaseExecutorConfig):
    """
    限价单执行器配置

    支持多层订单，每层有独立的 spread, timeout, per_order_usd 配置。

    Example config:
        class_name: limit
        interval: 0.5
        orders:
          - spread: 0.001
            refresh_tolerance: 0.5
            timeout: 30
            per_order_usd: 50
          - spread: 0.003
            refresh_tolerance: 0.5
            timeout: 60
            per_order_usd: 100

    Attributes:
        orders: 多层订单配置列表
    """

    class_name: ClassVar[str] = "limit"

    orders: list[LimitOrderLevel] = Field(
        default_factory=list,
        description="多层订单配置",
    )

    @classmethod
    def get_class_type(cls) -> Type["LimitExecutor"]:
        from .executor import LimitExecutor
        return LimitExecutor


__all__ = [
    "LimitOrderLevel",
    "LimitExecutorConfig",
]

