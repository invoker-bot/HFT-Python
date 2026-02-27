"""
执行器配置模块

提供执行器的配置类：
- BaseExecutorConfig: 执行器配置基类
- ExecutorVarDefinition: 变量定义（Feature 0010，含条件支持）
- OrderDefinition: 统一订单配置（Feature 0010 Phase 4）
"""
import copy
from typing import ClassVar, Optional, Type, Union, Any
from functools import cached_property
from pydantic import BaseModel, Field
from ..config.var import VarsDefinition, StandardVarsDefinition, to_standard_vars_definition
from ..config.base import BaseConfig, BaseConfigPath
from .base import BaseExecutor

__all__ = [
    "BaseExecutorConfig",
    "ExecutorConfigPath",
    "OrderDefinition",
]


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
    vars: VarsDefinition = Field(
        default_factory=list,
        description="订单级变量列表"  # 可使用TempScope来记录变量
    )

    @cached_property
    def standard_vars_definition(self) -> StandardVarsDefinition:
        """获取规范化后的变量定义列表"""
        return to_standard_vars_definition(self.vars)

    # 挂单条件 用于判断是否执行
    condition: Any = Field(
        None,
        description="挂单条件表达式，为 False 时不挂单, None 表示始终挂单"
    )

    # 价格  否则用
    price: Optional[Union[float, str]] = Field(
        None,
        description="绝对价格表达式"
    )
    spread: Optional[Union[float, str]] = Field(
        None,
        description="绝对值价差表达式（买单: bid - spread, 卖单: ask + spread）"
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

    # 订单管理，最大挂单事件 timeout
    timeout: Union[float, str] = Field(
        60.0,
        description="订单超时时间（秒）"
    )
    # 刷新容忍度，二选一，默认是根据spread来计算
    refresh_tolerance: Union[float, str] = Field(
        0.5,
        description="刷新容忍度"
    )
    refresh_tolerance_usd: Optional[Union[float, str]] = Field(
        None,
        description="刷新容忍度（绝对值，USD）"
    )
    level: Optional[int] = Field(
        None,
        description="订单层级（从 1 开始），用于多层订单时记录当前层级"
    )
    post_only: Union[bool, str] = Field(
        False,
        description="是否仅挂单，不吃单"
    )


class BaseExecutorConfig(BaseConfig["BaseExecutor"]):
    """
    执行器配置基类

    Attributes:
        interval: Tick 间隔（秒）
        clean: 退出时是否取消所有活跃订单
        requires: 依赖的 indicator ID 列表（Feature 0005）
        condition: 执行条件表达式，None 表示始终执行（Feature 0005）
        vars: 变量列表（Feature 0010）
            - 按顺序计算，后面可引用前面
            - 每次 tick 重新计算
            - 支持条件变量（通过 on 和 initial_value 字段）
    """
    class_dir: ClassVar[str] = "conf/executor"

    interval: float = Field(5.0, description="最小执行间隔（秒）")
    # always: bool = Field(False, description="是否总是执行（忽略 delta 阈值检查）")
    clean: bool = Field(False, description="退出时是否清理所有活跃订单（取消）")
    requires: list[str] = Field(default_factory=list, description="依赖的 indicator ID 列表")
    condition: Optional[Union[bool, str]] = Field(None, description="是否执行的条件表达式")
    default_timeout: float = Field(60.0, description="默认订单的超时时间（秒）")
    # Feature 0010: vars
    vars: VarsDefinition = Field(
        default_factory=list,
        description="变量定义（支持三种格式：1. list[ExecutorVarDefinition] 标准格式，2. dict[str, str] 简化格式（计算顺序不确定），3. list[str] 'name=value' 格式）"
    )

    @cached_property
    def standard_vars_definition(self) -> StandardVarsDefinition:
        """获取规范化后的变量定义列表"""
        return to_standard_vars_definition(self.vars)

    orders: list[OrderDefinition] = Field(
        default_factory=list,
        description="订单列表配置"
    )
    order: Optional[OrderDefinition] = Field(
        None,
        description="单订单配置"
    )
    order_levels: Optional[int] = Field(
        None,
        description="多层订单数量"
    )

    @cached_property
    def total_order_definitions(self) -> list[OrderDefinition]:
        """获取总的订单定义列表，合并 orders 和 order/order_levels"""
        orders = copy.copy(self.orders)
        if self.order_levels is not None:
            for level in range(1, self.order_levels + 1):
                for direction in (-1, 1):
                    order = copy.copy(self.order)
                    order.level = level * direction
                    orders.append(order)
        return orders

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        return BaseExecutor


class ExecutorConfigPath(BaseConfigPath):
    """执行器配置路径"""
    class_dir: ClassVar[str] = "conf/executor"
