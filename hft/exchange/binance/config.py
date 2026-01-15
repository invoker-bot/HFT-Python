"""
Binance 交易所配置
"""
from functools import cached_property
from typing import ClassVar, Type
from pydantic import Field
from ccxt.pro import binance, Exchange as CCXTExchange
from ..config import BaseExchangeConfig
from ...config.crypto import SecretStrAnnotated
from .base import BinanceExchange


class BinanceExchangeConfig(BaseExchangeConfig):
    """
    Binance 交易所配置

    test 模式说明：
    - 当 test=True 时，使用 Demo Trading 模式（主网 API + demo header）
    - API Key 需要从 demo.binance.com 创建
    - 参考: https://www.binance.com/en/support/faq/detail/9be58f73e5e14338809e3b705b9687dd

    注意：旧的 testnet.binancefuture.com 已废弃，ccxt 不再支持
    """
    class_name: ClassVar[str] = "binance"
    ccxt_exchange_class: ClassVar[type] = binance

    api_key: SecretStrAnnotated = Field(description="API Key for Binance Exchange")
    api_secret: SecretStrAnnotated = Field(description="API Secret for Binance Exchange")

    @classmethod
    def get_class_type(cls) -> Type[BinanceExchange]:
        return BinanceExchange

    def post_init_ccxt_instance(self, instance: CCXTExchange) -> None:
        """Binance test 模式启用 Demo Trading"""
        if self.test:
            instance.set_sandbox_mode(False)  # Demo Trading 使用主网 URL
            instance.enable_demo_trading(True)

    @cached_property
    def ccxt_instances(self) -> dict[str, CCXTExchange]:
        instances = {}
        for type_, config in self.ccxt_config_dicts().items():
            instance = self.ccxt_exchange_class(config)
            self.post_init_ccxt_instance(instance)
            instances[type_] = instance
        return instances

    def ccxt_config_dict_overrides(self, exchange_type) -> dict:
        """生成 Binance ccxt 配置"""
        return {
            'apiKey': self.get_str_value(self.api_key),
            'secret': self.get_str_value(self.api_secret),
        }
