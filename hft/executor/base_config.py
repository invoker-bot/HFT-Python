"""
Executor 配置基类模块

BaseExecutorConfig 提供执行器通用配置字段：
- interval: tick 间隔（秒）
- always: 是否总是执行（忽略 delta 阈值检查）
- requires: 依赖的 indicator ID 列表
- condition: 执行条件表达式
- vars: 变量列表（Feature 0010 Phase 1）
- conditional_vars: 条件变量字典（Feature 0010 Phase 2）
"""
from typing import ClassVar, Type, Optional, Any, Union

from pydantic import Field, BaseModel

from ..config.base import BaseConfig
from .base import BaseExecutor


class ExecutorVarDefinition(BaseModel):
    """
    Executor 变量定义（Feature 0010 Phase 1）

    用于 vars 列表中的变量定义。
    """
    name: str = Field(..., description="变量名")
    value: str = Field(..., description="表达式")


class ExecutorConditionalVarDefinition(BaseModel):
    """
    Executor 条件变量定义（Feature 0010 Phase 2）

    用于 conditional_vars 中的条件变量定义。
    """
    value: str = Field(..., description="更新表达式")
    on: str = Field(..., description="触发条件表达式")
    default: Any = Field(None, description="默认值（条件从未满足时使用）")


class BaseExecutorConfig(BaseConfig["BaseExecutor"]):
    """
    执行器配置基类

    Attributes:
        interval: Tick 间隔（秒）
        always: 是否总是执行（忽略 delta 阈值检查）
            - False: 只有当 |delta| >= per_order_usd 时才执行（rebalancing 模式）
            - True: 无论 delta 多大都执行（通常对应于，market making 模式）
        requires: 依赖的 indicator ID 列表（Feature 0005）
        condition: 执行条件表达式，None 表示始终执行（Feature 0005）
        vars: 变量列表（Feature 0010 Phase 1）
            - 按顺序计算，后面可引用前面
            - 每次 tick 重新计算
        conditional_vars: 条件变量字典（Feature 0010 Phase 2）
            - 仅当 on 条件满足时更新 value
            - 条件不满足时保持上次值
            - 支持 duration 变量（距上次更新的秒数）
    """

    class_dir: ClassVar[str] = "conf/executor"

    interval: float = Field(1.0, description="最小执行间隔（秒）")
    always: bool = Field(False, description="是否总是执行（忽略 delta 阈值检查）")
    requires: list[str] = Field(default_factory=list, description="依赖的 indicator ID 列表")
    condition: Optional[str] = Field(None, description="执行条件表达式")

    # Feature 0010: vars / conditional_vars
    vars: Union[dict[str, str], list[ExecutorVarDefinition]] = Field(
        default_factory=dict,
        description="变量定义（支持 dict 和 list 两种格式）"
    )
    conditional_vars: dict[str, ExecutorConditionalVarDefinition] = Field(
        default_factory=dict,
        description="条件变量字典（条件满足时更新）"
    )

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        return BaseExecutor

