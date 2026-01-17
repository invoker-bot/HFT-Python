"""
PCA (Position Cost Averaging) Executor 配置模块

Feature 0010 Phase 5: 使用统一的 Order 配置格式

PCAExecutor 特点：
- entry_order: 入场订单（趋近目标）
- exit_order: 出场订单（偏离目标，止盈/止损）
- 支持 entry_level, exit_level 追踪
- 支持 reset 条件（重置统计）
"""
from functools import cached_property
from typing import ClassVar, Type, Optional, Union

from pydantic import Field

from ..base_config import BaseExecutorConfig
from ..base import BaseExecutor
from ..order_config import OrderDefinition


class PCAExecutorConfig(BaseExecutorConfig):
    """
    PCA (Position Cost Averaging) 执行器配置

    Feature 0010 Phase 5: 新配置格式

    新格式支持：
    - entry_order / entry_orders: 入场订单配置
    - exit_order / exit_orders: 出场订单配置
    - entry_order_levels: 入场档位数量
    - exit_order_levels: 出场档位数量
    - reset: 重置条件表达式

    内置变量（可在 order 表达式中使用）：
    - entry_level: 当前入场档位（0-based）
    - exit_level: 当前出场档位（0-based）
    - total_entry_amount: 累计入场数量
    - total_entry_usd: 累计入场金额
    - average_entry_price: 平均入场价格
    - delta_position_amount: 当前仓位与目标的数量差
    - delta_position_usd: 当前仓位与目标的 USD 差

    Example (新格式):
        class_name: pca
        reset: 'abs(delta_position_usd) < 50'

        entry_order_levels: 10
        entry_order:
          condition: 'direction != 0'
          order_amount: '0.01 * (entry_level + 1) * direction'
          spread: '0.001 * (entry_level + 1) * mid_price'
          timeout: 86400  # 7 days

        exit_order_levels: 1
        exit_order:
          condition: 'abs(delta_position_usd) > 50'
          order_amount: '-delta_position_amount'
          price: 'average_entry_price * (1 + 0.01 * direction)'
          timeout: 3600

    Example (旧格式，向后兼容):
        class_name: pca
        base_order_usd: 100
        spread_open: 0.01
        spread_close: 0.02
        amount_multiplier: 1.5
        spread_multiplier: 1.2
        max_additions: 5
    """

    class_name: ClassVar[str] = "pca"

    # ============================================================
    # 新格式字段（Feature 0010 Phase 5）
    # ============================================================

    # 重置条件
    reset: Optional[str] = Field(
        None,
        description="重置条件表达式，满足时重置所有统计"
    )

    # 入场订单配置
    entry_order_levels: int = Field(
        1,
        description="入场档位数量"
    )
    entry_order: Optional[OrderDefinition] = Field(
        None,
        description="入场订单配置（单个）"
    )
    entry_orders: list[OrderDefinition] = Field(
        default_factory=list,
        description="入场订单配置列表（多个）"
    )

    # 出场订单配置
    exit_order_levels: int = Field(
        1,
        description="出场档位数量"
    )
    exit_order: Optional[OrderDefinition] = Field(
        None,
        description="出场订单配置（单个）"
    )
    exit_orders: list[OrderDefinition] = Field(
        default_factory=list,
        description="出场订单配置列表（多个）"
    )

    # ============================================================
    # 旧格式字段（向后兼容）
    # ============================================================

    base_order_usd: float = Field(100.0, description="基础订单金额（USD）")
    spread_open: float = Field(0.01, description="基础开仓距离（相对当前价格）")
    spread_close: float = Field(0.02, description="平仓距离（相对成本价）")
    amount_multiplier: float = Field(1.5, description="加仓金额扩大系数")
    spread_multiplier: float = Field(1.2, description="加仓距离扩大系数")
    max_additions: int = Field(5, description="最大加仓次数")
    timeout: float = Field(3600.0, description="订单超时时间（秒）")
    refresh_tolerance: float = Field(0.5, description="价格偏离容忍度")

    @property
    def use_new_format(self) -> bool:
        """是否使用新格式配置"""
        return (
            self.entry_order is not None or
            len(self.entry_orders) > 0 or
            self.exit_order is not None or
            len(self.exit_orders) > 0
        )

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


