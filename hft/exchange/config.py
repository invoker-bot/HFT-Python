"""
交易所配置
"""
from functools import cached_property
from typing import Optional, ClassVar, Type, Union, Literal
from pydantic import Field, AnyUrl, field_validator
from ccxt.pro import Exchange as CCXTExchange
from ..config.base import BaseConfig
from .base import BaseExchange, TradeType


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
    testnet: bool = Field(False, description="Use testnet or not")
    debug: bool = Field(False, description="Enable debug mode (no real orders)")

    # fee config
    swap_maker_fee: float = Field(0.0002, description="Default maker fee for swap trading")
    swap_taker_fee: float = Field(0.0005, description="Default taker fee for swap trading")
    spot_maker_fee: float = Field(0.0008, description="Default maker fee for spot trading")
    spot_taker_fee: float = Field(0.0010, description="Default taker fee for spot trading")

    # 期货配置
    leverage: Optional[int] = Field(None, description="Default leverage for futures trading")
    support_types: Optional[list[str]] = Field(None, validate_default=True, description="Supported market types: 'spot', 'swap'")

    #  下单相关配置
    amount_refactor: float = Field(1.0, description="Refactor factor for order amounts")
    max_position_per_pair_usd: Optional[float] = Field(None, description="Maximum position size per trading pair in USD")
    max_position_per_order_usd: Optional[float] = Field(None, description="Maximum position size per order in USD")

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

    @cached_property
    def ccxt_instance(self) -> CCXTExchange:
        """创建 ccxt 交易所实例"""
        return next(iter(self.ccxt_instances.values()))

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
                'sandbox': self.testnet,
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
