"""
Executor 配置基类模块

BaseExecutorConfig 提供执行器通用配置字段：
- interval: tick 间隔（秒）
- always: 是否总是执行（忽略 delta 阈值检查）
- requires: 依赖的 indicator ID 列表
- condition: 执行条件表达式
- vars: 变量列表（Feature 0010，支持条件变量）
"""
from typing import Any, ClassVar, Optional, Type, Union

from pydantic import BaseModel, Field, model_validator

from ..config.base import BaseConfig
from .base import BaseExecutor


class ExecutorVarDefinition(BaseModel):
    """
    Executor 变量定义（Feature 0010）

    用于 vars 列表中的变量定义。
    支持条件变量（通过 on 和 initial_value 字段）。
    """
    name: str = Field(..., description="变量名")
    value: str = Field(..., description="表达式")
    on: Optional[str] = Field(None, description="条件表达式（默认 True，条件满足时更新）")
    initial_value: Any = Field(None, description="初始值（条件从未满足时使用）")


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
        scope: 关联的 Scope class ID（Feature 0012）
        vars: 变量列表（Feature 0010）
            - 按顺序计算，后面可引用前面
            - 每次 tick 重新计算
            - 支持条件变量（通过 on 和 initial_value 字段）
    """

    class_dir: ClassVar[str] = "conf/executor"

    interval: float = Field(1.0, description="最小执行间隔（秒）")
    always: bool = Field(False, description="是否总是执行（忽略 delta 阈值检查）")
    requires: list[str] = Field(default_factory=list, description="依赖的 indicator ID 列表")
    condition: Optional[str] = Field(None, description="执行条件表达式")

    # Feature 0010: vars
    # Feature 0012: scope
    scope: Optional[str] = Field(None, description="关联的 Scope class ID")
    vars: Union[list[ExecutorVarDefinition], dict[str, str], list[str]] = Field(
        default_factory=list,
        description="变量定义（支持三种格式：1. list[ExecutorVarDefinition] 标准格式，2. dict[str, str] 简化格式（计算顺序不确定），3. list[str] 'name=value' 格式）"
    )

    @model_validator(mode='before')
    @classmethod
    def normalize_vars(cls, data: Any) -> Any:
        """
        将 vars 的简化格式转换为标准格式

        支持三种格式：
        1. list[ExecutorVarDefinition] - 标准格式（不转换）
        2. dict[str, str] - 简化格式：{name: value}（计算顺序不确定）
        3. list[str] - 简化格式：["name=value"]

        支持混合格式：list 中可以混合标准格式和简化格式
        """
        if not isinstance(data, dict):
            return data

        vars_value = data.get('vars')
        if vars_value is None:
            return data

        # 格式 2: dict[str, str] - {name: value}
        if isinstance(vars_value, dict):
            normalized = []
            for name, value in vars_value.items():
                normalized.append({
                    'name': name,
                    'value': str(value)
                })
            data['vars'] = normalized
            return data

        # 格式 1 和 3: list 格式（可能混合）
        if isinstance(vars_value, list) and len(vars_value) > 0:
            normalized = []
            for item in vars_value:
                if isinstance(item, dict):
                    # 标准格式：已经是 dict，直接保留
                    normalized.append(item)
                elif isinstance(item, ExecutorVarDefinition):
                    # ExecutorVarDefinition 实例，转换为 dict
                    normalized.append(item.model_dump())
                elif isinstance(item, str):
                    # 简化格式：字符串 "name=value"
                    if '=' in item:
                        name, value = item.split('=', 1)
                        normalized.append({
                            'name': name.strip(),
                            'value': value.strip()
                        })
                    else:
                        # 格式错误，跳过
                        continue
                else:
                    # 未知格式，跳过
                    continue
            data['vars'] = normalized
            return data

        return data

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        return BaseExecutor

