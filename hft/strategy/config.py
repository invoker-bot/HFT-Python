"""
策略配置基类

Feature 0008: Strategy 数据驱动增强
- 支持 requires 依赖声明
- 支持 vars / conditional_vars 变量计算
"""
from typing import ClassVar, Type, Any, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field
from ..config.base import BaseConfig

if TYPE_CHECKING:
    from .base import BaseStrategy


class VarDefinition(BaseModel):
    """
    变量定义（Feature 0008）

    用于 vars 列表中的变量定义。
    """
    name: str = Field(..., description="变量名")
    value: str = Field(..., description="表达式")


class ConditionalVarDefinition(BaseModel):
    """
    条件变量定义（Feature 0008）

    用于 conditional_vars 中的条件变量定义。
    """
    value: str = Field(..., description="更新表达式")
    on: str = Field(..., description="触发条件表达式")
    default: Any = Field(None, description="默认值（条件从未满足时使用）")


class TargetDefinition(BaseModel):
    """
    目标定义（Feature 0008 Phase 4）

    用于 targets 列表中的目标定义，支持表达式和多 exchange 匹配。

    Feature 0011: 添加 condition 支持
    - condition: 条件表达式，默认 null（等价 True）
    - 若 condition 求值为 False 或异常，该 target 被忽略
    """
    exchange: str = Field(
        "*",
        description="Exchange 匹配模式，'*' 表示所有，或具体路径如 'okx/main'"
    )
    exchange_class: str = Field(
        "*",
        description="Exchange class 匹配模式，'*' 表示所有，或具体类名如 'okx'"
    )
    symbol: str = Field(..., description="交易对")

    # Feature 0011: target 级 condition
    condition: Optional[str] = Field(
        None,
        description="条件表达式（默认 null=True；False/异常时忽略该 target）"
    )

    # 支持任意字段，值可以是表达式或字面量
    # 以下是常用字段，其他字段通过 extra_fields 传递
    position_usd: Optional[str] = Field(None, description="目标仓位（USD 表达式）")
    position_amount: Optional[str] = Field(None, description="目标仓位（数量表达式）")
    max_position_usd: Optional[str] = Field(None, description="最大仓位（USD 表达式）")
    speed: Optional[float] = Field(0.5, description="执行紧急度")

    # 额外字段（通过 model_extra 访问）
    model_config = {"extra": "allow"}


class BaseStrategyConfig(BaseConfig["BaseStrategy"]):
    """
    策略配置基类

    Feature 0008: 支持 requires、vars、conditional_vars
    Feature 0011: 支持全局 condition

    提供：
    - 策略基本配置
    - 交易所引用
    - 交易对配置
    - 变量计算机制
    - 全局 condition 门控
    """
    class_dir: ClassVar[str] = "conf/strategy"

    # 基本配置
    name: str = Field(description="Strategy name")
    debug: bool = Field(False, description="Enable debug mode (no real orders)")
    interval: float = Field(1.0, description="Main loop interval (seconds)")
    # exchange_path: str = Field(description="Exchange config path (e.g., 'binance/main')")

    # 交易对配置
    trading_pairs: list[str] = Field(default_factory=list, description="Trading symbols (e.g., '*', 'BTC/USDT:USDT', '!ETH/USDT')")
    max_trading_pairs: int = Field(12, description="Maximum number of trading pairs to trade simultaneously")

    # market_type: str = Field("linear", description="Market type: spot, linear, inverse")

    # 仓位目标
    # targets: dict[str, float] = Field(default_factory=dict, description="Position targets {symbol: amount}")

    # Feature 0008: 数据驱动增强
    requires: list[str] = Field(
        default_factory=list,
        description="依赖的 Indicator ID 列表"
    )
    vars: list[VarDefinition] = Field(
        default_factory=list,
        description="变量列表（按顺序计算，后面可引用前面）"
    )
    conditional_vars: dict[str, ConditionalVarDefinition] = Field(
        default_factory=dict,
        description="条件变量字典（条件满足时更新）"
    )

    # Feature 0011: 全局 condition
    condition: Optional[str] = Field(
        None,
        description="全局条件表达式（默认 null=True；False/异常时忽略所有 targets）"
    )

    @classmethod
    def get_class_type(cls) -> Type["BaseStrategy"]:
        from .base import BaseStrategy
        return BaseStrategy
