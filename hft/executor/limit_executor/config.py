"""
LimitExecutor 配置模块
"""
from typing import TYPE_CHECKING, ClassVar, Type

from pydantic import BaseModel, Field

from ..base_config import BaseExecutorConfig

if TYPE_CHECKING:
    from .executor import LimitExecutor


class LimitOrderLevel(BaseModel):
    """
    单层限价单配置

    Attributes:
        reverse: 是否反向订单（用于对冲）
        spread: 距离当前价格的百分比（如 0.01 = 1%）
        refresh_tolerance: 刷新容忍度，超过此值才更新订单价格
            - 计算: |new_price - old_price| > refresh_tolerance * spread * old_price
            - 值为 1.0 时类似网格交易
        timeout: 订单超时时间（秒），超时后取消订单
        per_order_usd: 该层订单的 USD 价值
    """

    reverse: bool = Field(False, description="是否反向订单")
    spread: float = Field(description="与当前价格的距离比")
    refresh_tolerance: float = Field(0.5, description="刷新容忍度")
    timeout: float = Field(60.0, description="订单超时（秒）")
    per_order_usd: float = Field(100.0, description="单笔订单 USD")


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

