from typing import Optional, Any, Union
from pydantic import BaseModel, Field


class StandardVarDefinition(BaseModel):
    """
    标准的单个变量的定义（Feature 0008）

    用于 vars 列表中的变量定义。
    """
    name: str = Field(..., description="变量名")
    value: Any = Field(..., description="表达式")
    on: Optional[str] = Field(None, description="条件表达式（默认 True，条件满足时更新）")
    initial_value: Any = Field(None, description="初始值（条件从未满足时使用）")

VarDefinition = Union[StandardVarDefinition, tuple[str, Any], str]

def to_standard_var_definition(data: VarDefinition) -> StandardVarDefinition:
    """
    将简化格式转换为 VarDefinition 实例

    支持三种格式：
    1. VarDefinition 实例 - 直接返回
    2. tuple - (name, value)
    3. str - "name=value"
    """
    if isinstance(data, StandardVarDefinition):
        return data
    elif isinstance(data, tuple) and len(data) == 2:
        name, value = data
        return StandardVarDefinition(name=name, value=value)
    elif isinstance(data, str):
        name, value = data.split('=', 1)
        return StandardVarDefinition(name=name.strip(), value=value.strip())
    elif isinstance(data, dict):
        return StandardVarDefinition(**data)
    else:
        raise NotImplementedError(f"Unsupported var definition format: {data}")


VarsDefinition = Union[list[VarDefinition], dict[str, str]]
StandardVarsDefinition = list[StandardVarDefinition]


def to_standard_vars_definition(data: VarsDefinition) -> StandardVarsDefinition:
    """
    将 vars 的简化格式转换为标准格式
    支持三种格式：
    1. list[VarDefinition] - 标准格式（不转换）
    2. dict[str, str] - 简化格式：{name: value}（计算顺序不确定）
    3. list[str] - 简化格式：["name=value"]
    支持混合格式：list 中可以混合标准格式和简化格式
    """
    if data is None:
        return []
    elif isinstance(data, list):
        return [to_standard_var_definition(item) for item in data]
    elif isinstance(data, dict):
        result = []
        for name, value in data.items():
            result.append(StandardVarDefinition(name=name, value=value))
        return result
    else:
        raise NotImplementedError(f"Unsupported vars format: {data}")
