"""
交易所配置
"""
import os
from functools import cached_property
from typing import ClassVar, Literal, Optional, Type, Union, Any, TYPE_CHECKING

from ccxt.pro import Exchange as CCXTExchange
from pydantic import AnyUrl, BaseModel, Field, field_validator, GetCoreSchemaHandler
from pydantic_core import core_schema
from ..config.base import BaseConfig, BaseConfigPath
from .base import BaseExchange, TradeType
if TYPE_CHECKING:
    from ..core.app.factory import AppFactory


class WhiteDepositAddress(BaseModel):
    """白名单充值地址配置"""
    network: str = Field(..., description="Network name (e.g., 'TRC20', 'ERC20', or '*' for all)")
    address: str = Field(..., description="Deposit address")


class BaseExchangeConfig(BaseConfig["BaseExchange"]):
    """
    交易所配置基类

    提供：
    - ccxt 实例创建
    - 代理配置
    - 测试网配置
    - 杠杆和保证金模式配置
    """
    class_dir: ClassVar[str] = "conf/exchange"
    ccxt_exchange_class: ClassVar[Type[CCXTExchange]] = CCXTExchange

    # 基本配置
    proxy: Optional[AnyUrl] = Field(None, description="Proxy URL for exchange API requests")
    proxy_env: Optional[str] = Field(None, description="Environment variable name for proxy URL")
    test: bool = Field(False, description="Use test/demo trading mode")
    debug: bool = Field(False, description="Enable debug mode (no real orders)")

    # auto cancel orders
    auto_cancel_orders_after: Union[float, str] = Field(
        3600, description="Time after which orders are automatically canceled (in seconds or as a string duration)")
    auto_tracking_orders_after: Union[float, str] = Field(
        300, description="Time after which orders are no longer tracked (in seconds or as a string duration)")
    auto_tracking_orders_before: Union[float, str] = Field(
        7200, description="Maximum age of orders to be tracked (in seconds or as a string duration)")
    # fee config
    swap_maker_fee: float = Field(0.0002, description="Default maker fee for swap trading")
    swap_taker_fee: float = Field(0.0005, description="Default taker fee for swap trading")
    spot_maker_fee: float = Field(0.0008, description="Default maker fee for spot trading")
    spot_taker_fee: float = Field(0.0010, description="Default taker fee for spot trading")

    # 期货配置
    leverage: Optional[int] = Field(None, description="Default leverage for futures trading")
    support_types: Optional[list[str]] = Field(
        None, validate_default=True, description="Supported market types: 'spot', 'swap'")

    # 下单相关配置
    amount_refactor: float = Field(1.0, description="Refactor factor for order amounts")
    max_position_per_pair_usd: Optional[float] = Field(
        None, description="Maximum position size per trading pair in USD")
    max_position_per_order_usd: Optional[float] = Field(None, description="Maximum position size per order in USD")

    # 充值地址白名单（用于自动提币）
    white_deposit_addresses: list[WhiteDepositAddress] = Field(
        default_factory=list,
        description="Whitelist of deposit addresses for auto-deposit. Use network='*' to match all networks."
    )

    # SmartExecutor 路由：按交易对指定执行器（覆盖自动选择）
    # key: symbol (e.g., "BTC/USDT:USDT"), value: executor key (e.g., "pca", "limit")
    executor_map: dict[str, str] = Field(
        default_factory=dict,
        description="Per-symbol executor override for SmartExecutor routing"
    )

    @field_validator("support_types", mode="before")
    @classmethod
    def parse_support_types(cls, v):
        if v:  # list
            for item in v:
                if item not in ('spot', 'swap'):  # only support spot and swap for now
                    raise ValueError(f"Unsupported market type: {item}")
            return v
        return ['swap']

    @classmethod
    def get_class_type(cls) -> Type["BaseExchange"]:
        return BaseExchange

    @property
    def ccxt_instance_key(self) -> str:
        """获取默认 ccxt 实例 key"""
        if "spot" in self.support_types:
            return "spot"
        return next(iter(self.ccxt_instances.keys()))

    @cached_property
    def ccxt_instance(self) -> CCXTExchange:
        """创建 ccxt 交易所实例，优先返回现货对"""
        return self.ccxt_instances[self.ccxt_instance_key]

    @cached_property
    def ccxt_instances(self) -> dict[str, CCXTExchange]:
        """创建多个 ccxt 交易所实例，用于多实例场景"""
        instances = {}
        for type_, config in self.ccxt_config_dicts().items():
            instance = self.ccxt_exchange_class(config)
            instances[type_] = instance
        return instances

    @classmethod
    def to_ccxt_instance_key(cls, trade_type: Union[TradeType, str]) -> str:
        match str(trade_type).lower():
            case "spot":
                return "spot"
            case "swap" | "future":
                return "swap"
            case _:
                raise ValueError(f"Unsupported trade type: {trade_type}")

    def ccxt_config_dicts(self) -> dict[str, dict]:
        """生成 ccxt 配置字典，也可用于多实例"""
        result = {}
        for support_type in self.support_types:
            config = {
                'sandbox': self.test,
                'enableRateLimit': True,
                'options': {
                    'defaultType': support_type,
                    'adjustForTimeDifference': True,
                    'recvWindow': 60000,
                },
            }
            config.update(self.ccxt_proxy_dict())
            config.update(self.ccxt_config_dict_overrides(support_type))
            result[support_type] = config
        return result

    def ccxt_proxy_dict(self) -> dict:
        """生成代理配置"""
        proxy_env = self.proxy_env
        if proxy_env is not None:
            proxy = os.getenv(str(proxy_env))
        else:
            proxy = None
        if proxy is None:
            if self.proxy is None:
                proxy = ""
            else:
                proxy = str(self.proxy)
        if not proxy:
            return {}
        return {
            'aiohttp_proxy': proxy,
            'ws_proxy': proxy,
            'proxies': {
                'http': proxy,
                'https': proxy
            }
        }

    def ccxt_config_dict_overrides(self, exchange_type: Literal["spot", "swap"]) -> dict:
        """生成 ccxt 配置覆盖项，子类可覆盖以添加更多配置"""
        return {}

    def post_init_ccxt_instance(self, instance: CCXTExchange) -> None:
        """ccxt 实例创建后的后处理钩子，子类可覆盖以进行额外配置（如 Demo Trading）"""


class ExchangeConfigPath(BaseConfigPath):
    """Exchange 配置路径"""
    class_dir: ClassVar[str] = "conf/exchange/"


class ExchangeConfigPathGroup:
    """
    Exchange 配置路径组

    特性：
    - 支持 selector 语义（*、!、通配）
    - 扫描并展开全部 exchange config id
    - 支持运行时过滤和分组
    - 支持 Pydantic 验证
    """

    def __init__(self, exchanges: list[str]):
        """
        初始化配置路径组

        Args:
            exchanges: exchange 配置 ID 列表
        """
        self.exchanges = exchanges

    @cached_property
    def exchange_configs(self) -> dict[str, ExchangeConfigPath]:
        return {id_: ExchangeConfigPath(id_) for id_ in self.exchanges}

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        """
        Pydantic v2 验证器

        支持从字符串列表创建 ExchangeConfigPathGroup 实例
        """
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.list_schema(core_schema.str_schema()),
        )

    @classmethod
    def _validate(cls, value: Any) -> 'ExchangeConfigPathGroup':
        """
        验证并转换输入值

        Args:
            value: 输入值（字符串列表）

        Returns:
            ExchangeConfigPathGroup 实例
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, list):
            return cls(exchanges=value)
        raise ValueError(f"Cannot convert {type(value)} to {cls.__name__}")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(exchanges={self.exchanges!r})"
