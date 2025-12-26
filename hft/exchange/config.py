"""
交易所配置
"""
from functools import cached_property
from typing import Optional, ClassVar, Type, TYPE_CHECKING
from pydantic import Field, AnyUrl
from ccxt.pro import Exchange
from ..config.base import BaseConfig

if TYPE_CHECKING:
    from .base import BaseExchange


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
    ccxt_exchange_class: ClassVar[Type[Exchange]] = Exchange

    # 基本配置
    proxy: Optional[str] = Field(None, description="Proxy URL for exchange API requests")
    testnet: bool = Field(False, description="Use testnet or not")
    debug: bool = Field(False, description="Enable debug mode (no real orders)")

    # 期货配置
    leverage: Optional[int] = Field(None, description="Default leverage for futures trading")
    margin_mode: Optional[str] = Field(
        "cross",
        description="Margin mode: 'cross' or 'isolated'"
    )
    default_type: str = Field("swap", description="Default market type: 'spot', 'swap', 'future'")

    @classmethod
    def get_class_type(cls) -> Type["BaseExchange"]:
        from .base import BaseExchange
        return BaseExchange

    @cached_property
    def ccxt_instance(self) -> Exchange:
        """创建 ccxt 交易所实例"""
        return self.ccxt_exchange_class(self.ccxt_config_dict())

    def ccxt_config_dict(self) -> dict:
        """生成 ccxt 配置字典"""
        config = {
            'sandbox': self.testnet,
            'enableRateLimit': True,
            'options': {
                'defaultType': self.default_type,
                'adjustForTimeDifference': True,
                'recvWindow': 60000,
            },
        }
        config.update(self.proxy_dict())
        return config

    def proxy_dict(self) -> dict:
        """生成代理配置"""
        if not self.proxy:
            return {}
        return {
            'aiohttp_proxy': self.proxy,
            'ws_proxy': self.proxy,
            'proxies': {
                'http': self.proxy,
                'https': self.proxy
            }
        }
