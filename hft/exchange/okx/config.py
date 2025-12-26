"""
OKX 交易所配置
"""
from typing import ClassVar, Type
from pydantic import Field
from ccxt.pro import okx
from ..config import BaseExchangeConfig
from ...config.crypto import SecretStrAnnotated
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

    def ccxt_config_dict(self) -> dict:
        """生成 OKX ccxt 配置"""
        config = super().ccxt_config_dict()

        def get_value(field):
            if hasattr(field, 'get_secret_value'):
                return field.get_secret_value()
            return field

        config.update({
            'apiKey': get_value(self.api_key),
            'secret': get_value(self.api_secret),
            'password': get_value(self.passphrase),
        })
        return config
