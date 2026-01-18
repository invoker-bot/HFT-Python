"""
策略配置基类

Feature 0008: Strategy 数据驱动增强
- 支持 requires 依赖声明
- 支持 vars / conditional_vars 变量计算
"""
from typing import ClassVar, Type, Any, Optional, Union, TYPE_CHECKING
from pydantic import BaseModel, Field, model_validator
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
    on: Optional[str] = Field(None, description="条件表达式（默认 True，条件满足时更新）")
    initial_value: Any = Field(None, description="初始值（条件从未满足时使用）")


class ScopeVarDefinition(BaseModel):
    """
    Scope 变量定义（Feature 0012）

    用于 scopes 配置中的变量定义。
    """
    name: str = Field(..., description="变量名")
    value: str = Field(..., description="表达式")
    on: Optional[str] = Field(None, description="条件表达式（默认 True，条件满足时更新）")
    initial_value: Any = Field(None, description="初始值（条件从未满足时使用）")


class ScopeConfig(BaseModel):
    """
    Scope 配置（Feature 0012）

    用于定义单个 Scope 层级的配置。
    """
    class_name: str = Field("BaseScope", description="Scope 类名（如 GlobalScope, ExchangeScope）")
    instance_id: Optional[str] = Field(None, description="Scope 实例 ID（如 'global'，可选）")
    vars: Union[list[ScopeVarDefinition], dict[str, str], list[str]] = Field(
        default_factory=list,
        description="变量列表（支持三种格式：1. list[ScopeVarDefinition] 标准格式，2. dict[str, str] 简化格式（计算顺序不确定），3. list[str] 'name=value' 格式）"
    )

    @model_validator(mode='before')
    @classmethod
    def normalize_vars(cls, data: Any) -> Any:
        """
        将 vars 的简化格式转换为标准格式

        支持三种格式：
        1. list[ScopeVarDefinition] - 标准格式（不转换）
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
    targets: list[TargetDefinition] = Field(
        default_factory=list,
        description="目标定义列表（Feature 0008 Phase 4）"
    )

    # Feature 0011: 全局 condition
    condition: Optional[str] = Field(
        None,
        description="全局条件表达式（默认 null=True；False/异常时忽略所有 targets）"
    )

    # Feature 0012: Scope 系统
    links: list[list[str]] = Field(
        default_factory=list,
        description="Scope 链路列表，如 [['global', 'exchange', 'trading_pair']]"
    )
    scopes: dict[str, ScopeConfig] = Field(
        default_factory=dict,
        description="Scope 配置字典，key 为 scope_class_id"
    )
    target_scope: Optional[str] = Field(
        None,
        description="目标 Scope 层级（Strategy 输出的层级）"
    )
    include_symbols: list[str] = Field(
        default_factory=lambda: ["*"],
        description="包含的交易对列表，支持通配符"
    )
    exclude_symbols: list[str] = Field(
        default_factory=list,
        description="排除的交易对列表"
    )

    @classmethod
    def get_class_type(cls) -> Type["BaseStrategy"]:
        from .base import BaseStrategy
        return BaseStrategy
