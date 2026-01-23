"""
OKX 交易所配置
"""
from typing import ClassVar, Type

from ccxt.pro import okx
from pydantic import Field

from ...config.crypto import SecretStrAnnotated
from ..config import BaseExchangeConfig
from .base import OKXExchange


class OKXExchangeConfig(BaseExchangeConfig):
    """
    OKX 交易所配置

    需要 API Key, Secret 和 Passphrase
    """
    class_name: ClassVar[str] = "okx"
    ccxt_exchange_class: ClassVar[type] = okx

    api_key: SecretStrAnnotated = Field(description="API Key for OKX Exchange")
    api_secret: SecretStrAnnotated = Field(description="API Secret for OKX Exchange")
    passphrase: SecretStrAnnotated = Field(description="Passphrase for OKX Exchange")

    @classmethod
    def get_class_type(cls) -> Type[OKXExchange]:
        return OKXExchange

    def ccxt_config_dict_overrides(self, exchange_type) -> dict:
        """生成 OKX ccxt 配置"""
        config = {
            'apiKey': self.get_str_value(self.api_key),
            'secret': self.get_str_value(self.api_secret),
            'password': self.get_str_value(self.passphrase),
        }
        return config
