from typing import Any, Optional, Union
from functools import cached_property
from pydantic import BaseModel, Field
from ..core.duration import parse_duration


class IndicatorDefinition(BaseModel):
    """
    Indicator 定义模块

    用于在配置文件中定义 Indicator 的参数。
    """
    class_name: str = Field(..., description="Indicator 类名")
    params: dict[str, Any] = Field(default_factory=dict, description="Indicator 参数字典")
    namespace: Optional[str] = Field(None, description="Indicator 命名空间（可选）")
    auto_disable_after: Union[float, str] = Field(600, description="自动禁用时间（秒或时间字符串）")

    @cached_property
    def auto_disable_after_seconds(self) -> float:
        return parse_duration(self.auto_disable_after)
