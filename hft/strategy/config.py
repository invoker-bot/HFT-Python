"""
策略配置基类

Feature 0008: Strategy 数据驱动增强
- 支持 requires 依赖声明
- 支持 vars / conditional_vars 变量计算
"""
from typing import Any, ClassVar, Optional, Type, Union
from pydantic import BaseModel, Field, model_validator
from .base import BaseStrategy
from ..config.base import BaseConfig, BaseConfigPath
from ..config.scope import ScopeFlowConfig


# class TargetDefinition(BaseModel):
#     """
#     目标定义（Feature 0012）
#
#     用于 targets 列表中的目标定义，支持表达式和多 exchange 匹配。
#
#     新格式（Feature 0012）：
#     - exchange_id: exchange 匹配模式（默认 "*"）
#     - symbol: symbol 匹配模式（默认 "*"）
#     - condition: 条件表达式（可选，默认 True）
#     - vars: 变量列表（VarDefinition 格式）
#
#     向后兼容旧格式：
#     - exchange: 等价于 exchange_id
#     - exchange_class: 用于匹配 exchange class
#     - position_usd/position_amount/max_position_usd/speed: 直接字段（不推荐，建议用 vars）
#     """
#     # 新格式字段
#     exchange_id: str = Field(
#         "*",
#         description="Exchange 匹配模式，'*' 表示所有，或具体路径如 'okx/main'"
#     )
#     symbol: str = Field("*", description="Symbol 匹配模式，'*' 表示所有")
#
#     # 向后兼容字段
#     exchange: str = Field(
#         "*",
#         description="（向后兼容）Exchange 匹配模式，等价于 exchange_id"
#     )
#     exchange_class: str = Field(
#         "*",
#         description="Exchange class 匹配模式，'*' 表示所有，或具体类名如 'okx'"
#     )
#
#     # 条件表达式
#     condition: Optional[str] = Field(
#         None,
#         description="条件表达式（默认 null=True；False/异常时忽略该 target）"
#     )
#
#     # 新格式：vars 列表
#     vars: VarsDefinations = Field(
#         default_factory=list,
#         description="变量列表（支持三种格式：1. list[VarDefinition] 标准格式，2. dict[str, str] 简化格式，3. list[str] 'name=value' 格式）"
#     )
#
#     # 向后兼容：直接字段（不推荐，建议用 vars）
#     position_usd: Optional[str] = Field(None, description="（向后兼容）目标仓位（USD 表达式）")
#     position_amount: Optional[str] = Field(None, description="（向后兼容）目标仓位（数量表达式）")
#     max_position_usd: Optional[str] = Field(None, description="（向后兼容）最大仓位（USD 表达式）")
#     speed: Optional[float] = Field(0.5, description="（向后兼容）执行紧急度")
#
#     # 额外字段（通过 model_extra 访问）
#     model_config = {"extra": "allow"}


class BaseStrategyConfig(BaseConfig["BaseStrategy"]):
    """
    策略配置基类

    Feature 0008: 支持 requires
    Feature 0011: 支持全局 condition
    Feature 0012: 支持 Scope 系统

    提供：
    - 策略基本配置
    - 交易所引用
    - 交易对配置
    - Scope 系统配置
    - 全局 condition 门控
    """
    class_dir: ClassVar[str] = "conf/strategy"

    # 基本配置
    # name: str = Field(description="Strategy name")
    debug: bool = Field(False, description="Enable debug mode (no real orders)")
    interval: float = Field(1.0, description="loop interval (seconds)")
    # exchange_path: str = Field(description="Exchange config path (e.g., 'binance/main')")
    # 交易对配置, filter 写法
    # trading_pairs: list[str] = Field(default_factory=list, description="Trading symbols (e.g., '*', 'BTC/USDT:USDT', '!ETH/USDT')")
    # max_trading_pairs: int = Field(12, description="Maximum number of trading pairs to trade simultaneously")

    # market_type: str = Field("linear", description="Market type: spot, linear, inverse")

    # Feature 0012: Scope 系统
    flow: ScopeFlowConfig = Field(
        default_factory=list,
        description="Scope 计算链路配置（Feature 0012）"
    )

    @classmethod
    def get_class_type(cls) -> Type["BaseStrategy"]:
        return BaseStrategy


class StrategyConfigPath(BaseConfigPath):
    """Strategy 配置路径"""
    class_dir: ClassVar[str] = "conf/strategy/"
