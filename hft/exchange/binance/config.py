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

    testnet 模式说明：
    - 现货 testnet: testnet.binance.vision
    - 期货 testnet: testnet.binancefuture.com
    - Paper Trading (demo.binance.com): 使用主网 API，需设置 testnet=False

    注意：demo.binance.com 的 API Key 需要在期货 testnet 注册
    """
    class_name: ClassVar[str] = "binance"
    ccxt_exchange_class: ClassVar[type] = binance

    api_key: SecretStrAnnotated = Field(description="API Key for Binance Exchange")
    api_secret: SecretStrAnnotated = Field(description="API Secret for Binance Exchange")

    @classmethod
    def get_class_type(cls) -> Type[BinanceExchange]:
        return BinanceExchange

    @cached_property
    def ccxt_instances(self) -> dict[str, CCXTExchange]:
        instances = {}
        for type_, config in self.ccxt_config_dicts().items():
            instance = self.ccxt_exchange_class(config)
            default_type = config['options']['defaultType']
            if self.testnet and default_type in ('swap', 'future'):
                # 所有 fapi 端点都指向 testnet
                testnet_base = 'https://testnet.binancefuture.com'
                for key in list(instance.urls['api'].keys()):
                    if key.startswith('fapi'):
                        # fapi, fapiPublic, fapiPrivate, fapiPrivateV2, fapiPrivateV3, fapiData 等
                        original = instance.urls['api'][key]
                        if 'fapi.binance.com' in original:
                            instance.urls['api'][key] = original.replace(
                                'https://fapi.binance.com', testnet_base
                            )
                # 只加载期货市场
                instance.options['defaultType'] = 'future'
                instance.options['fetchMarkets'] = ['linear']
            instances[type_] = instance
        return instances

    def ccxt_config_dict_overrides(self, exchange_type) -> dict:
        """生成 Binance ccxt 配置"""
        config = {
            'apiKey':  self.get_str_value(self.api_key),
            'secret': self.get_str_value(self.api_secret),
        }
        # 不使用 sandbox，手动配置 URL
        # config['sandbox'] = False
        # config['options'] = config.get('options', {})
        # config['options']['fetchCurrencies'] = False
        return config
