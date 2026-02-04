from typing import Optional, Union
from functools import cached_property
from pydantic import BaseModel, Field
from .var import VarsDefinition, to_standard_vars_definition, StandardVarsDefinition


class ScopeConfig(BaseModel):
    """
    Scope 配置，位于strategies文件中
    用于定义 Scope 层级的配置。
    """
    class_name: str = Field("BaseScope", description="Scope 类名（如 GlobalScope, ExchangeScope）")
    filter:  Optional[Union[bool, str]] = Field(
        default=None,
        description="条件表达式列表，在之前计算"
    )  # 可选的过滤表达式列表
    requires: list[str] = Field(
        default_factory=list,
        description="依赖注入的 indicator ID 列表"
    )  # 可选的依赖 indicator 列表
    vars: VarsDefinition = Field(
        default_factory=list,
        description="变量列表（支持三种格式：1. list[ScopeVarDefinition] 标准格式，2. dict[str, str] 简化格式（计算顺序不确定），3. list[str] 'name=value' 格式）"
    )  # scope的变量列表
    @cached_property
    def standard_vars_definition(self) -> StandardVarsDefinition:
        """获取规范化后的变量定义列表"""
        return to_standard_vars_definition(self.vars)
    condition: Optional[Union[bool, str]] = Field(
        None,
        description="条件表达式，在之后计算"
    )  # 可选的条件表达式，决定是否继续执行下去


ScopeFlowConfig = list[ScopeConfig]
