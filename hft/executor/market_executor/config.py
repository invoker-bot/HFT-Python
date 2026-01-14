from typing import TYPE_CHECKING, ClassVar, Type

from pydantic import Field

from ..base_config import BaseExecutorConfig

if TYPE_CHECKING:
    from .executor import MarketExecutor


class MarketExecutorConfig(BaseExecutorConfig):
    """
    市价单执行器配置

    Attributes:
        per_order_usd: 单笔订单大小（USD）
    """
    class_name: ClassVar[str] = "market"

    per_order_usd: float = Field(100.0, description="单笔订单大小（USD）")

    @classmethod
    def get_class_type(cls) -> Type["MarketExecutor"]:
        from .executor import MarketExecutor
        return MarketExecutor


__all__ = [
    "MarketExecutorConfig",
]
