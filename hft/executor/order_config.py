"""
Order 统一配置模块（Feature 0010 Phase 4）

提供统一的订单配置格式，支持：
- vars（订单级变量，支持条件变量）
- price / spread（二选一）
- order_usd / order_amount（二选一）
- condition（挂单条件）
"""
from typing import Any, Optional, Union

from pydantic import BaseModel, Field


class OrderVarDefinition(BaseModel):
    """
    订单级变量定义

    用于 order.vars 列表中的变量定义。
    """
    name: str = Field(..., description="变量名")
    value: str = Field(..., description="表达式")


class OrderConditionalVarDefinition(BaseModel):
    """
    订单级条件变量定义（DEPRECATED - 使用 OrderVarDefinition 的 on 字段替代）

    用于 order.vars 中的条件变量定义。
    """
    value: str = Field(..., description="更新表达式")
    on: str = Field(..., description="触发条件表达式")
    default: Any = Field(None, description="默认值（条件从未满足时使用）")


class OrderDefinition(BaseModel):
    """
    统一订单配置（Feature 0010 Phase 4）

    支持所有 Executor 的订单配置。

    价格计算（二选一）：
    - price: 绝对价格表达式
    - spread: 价差表达式（相对 mid_price）

    数量计算（二选一）：
    - order_amount: 数量表达式（正=买，负=卖）
    - order_usd: 金额表达式

    Example:
        order:
          vars:
            - name: spread_value
              value: '0.001 * level'
          condition: 'level <= max_level'
          spread: 'spread_value * mid_price'
          order_usd: '100 * abs(level)'
          timeout: 60
          refresh_tolerance: 0.5
    """

    # 订单级变量
    vars: list[OrderVarDefinition] = Field(
        default_factory=list,
        description="订单级变量列表"
    )
    # DEPRECATED: 使用 vars 中的 on 字段替代
    conditional_vars: dict[str, OrderConditionalVarDefinition] = Field(
        default_factory=dict,
        description="订单级条件变量（已废弃）"
    )

    # 挂单条件
    condition: Optional[str] = Field(
        None,
        description="挂单条件表达式，为 False 时不挂单"
    )

    # 价格（二选一）
    price: Optional[Union[float, str]] = Field(
        None,
        description="绝对价格表达式"
    )
    spread: Optional[Union[float, str]] = Field(
        None,
        description="价差表达式（买单: bid - spread, 卖单: ask + spread）"
    )

    # 数量（二选一）
    order_amount: Optional[Union[float, str]] = Field(
        None,
        description="订单数量表达式（正=买，负=卖）"
    )
    order_usd: Optional[Union[float, str]] = Field(
        None,
        description="订单金额表达式"
    )

    # 订单管理
    timeout: Union[float, str] = Field(
        60.0,
        description="订单超时时间（秒）"
    )
    refresh_tolerance: Union[float, str] = Field(
        0.5,
        description="刷新容忍度"
    )
    refresh_tolerance_usd: Optional[Union[float, str]] = Field(
        None,
        description="刷新容忍度（绝对值，USD）"
    )


class EntryExitOrderDefinition(OrderDefinition):
    """
    Entry/Exit 订单配置

    继承自 OrderDefinition，用于支持 entry_order / exit_order 场景。
    """
    pass


__all__ = [
    "OrderVarDefinition",
    "OrderConditionalVarDefinition",
    "OrderDefinition",
    "EntryExitOrderDefinition",
]
