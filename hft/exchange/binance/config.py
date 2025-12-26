"""
Binance 交易所配置
"""
from functools import cached_property
from typing import ClassVar, Type
from pydantic import Field
from ccxt.pro import binance, Exchange
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
    def ccxt_instance(self) -> Exchange:
        """创建 ccxt 交易所实例，手动配置 testnet URL"""
        config = self.ccxt_config_dict()
        instance = self.ccxt_exchange_class(config)

        # 手动配置期货 testnet URL（绕过 ccxt sandbox 限制）
        if self.testnet and self.default_type in ('swap', 'future'):
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

        return instance

    def ccxt_config_dict(self) -> dict:
        """生成 Binance ccxt 配置"""
        config = super().ccxt_config_dict()
        config.update({
            'apiKey': self.api_key.get_secret_value() if hasattr(self.api_key, 'get_secret_value') else self.api_key,
            'secret': self.api_secret.get_secret_value() if hasattr(self.api_secret, 'get_secret_value') else self.api_secret,
        })

        # 不使用 sandbox，手动配置 URL
        config['sandbox'] = False
        config['options'] = config.get('options', {})
        config['options']['fetchCurrencies'] = False

        return config
