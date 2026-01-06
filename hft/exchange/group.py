from typing import TYPE_CHECKING, Optional
from collections import defaultdict
from cryptography.fernet import InvalidToken
from ..core.listener import Listener, ListenerState
from .base import BaseExchange
from .config import BaseExchangeConfig

if TYPE_CHECKING:
    from ..core.app import AppCore


class ExchangeGroups(Listener):

    def __init__(self):
        super().__init__("ExchangeGroups", interval=60.0)
        self.exchanges_map = defaultdict(list)

    async def add_exchange(self, exchange: BaseExchange):
        self.exchanges_map[exchange.class_name].append(exchange.name)
        self.add_child(exchange)
        if self.state in (ListenerState.STARTING, ListenerState.RUNNING):
            await exchange.start()

    async def remove_exchange(self, exchange: BaseExchange):
        await exchange.stop()
        self.exchanges_map[exchange.class_name].remove(exchange.name)
        self.remove_child(exchange.name)

    async def on_tick(self):
        # Placeholder for periodic tasks related to exchange groups
        app: 'AppCore' = self.root
        for exchange in list(self.children.values()):
            if exchange.name not in app.config.exchanges:
                await self.remove_exchange(exchange)
        for exchange_name in app.config.exchanges:
            if exchange_name not in self.children:
                try:
                    exchange_config = BaseExchangeConfig.load(exchange_name)
                    exchange_instance: BaseExchange = exchange_config.instance
                except InvalidToken:
                    self.logger.error("Failed to decrypt exchange config file for %s, you should check password or config file.", exchange_name)
                    return True  # 配置文件解密失败，跳过加载该交易所
                await self.add_exchange(exchange_instance)

    def get_exchange_classes(self):
        classes = []
        for k, v in list(self.exchanges_map.items()):
            if len(v) > 0:
                classes.append(k)
        return classes

    def get_exchange_by_class(self, class_name: str) -> Optional[BaseExchange]:
        exchange_names = self.exchanges_map[class_name]
        for exchange_name in exchange_names:
            exchange = self.children.get(exchange_name, None)
            if exchange is not None:
                return exchange
        return None
    
    def get_exchanges_by_class(self, class_name: str) -> list[BaseExchange]:
        result = []
        exchange_names = self.exchanges_map[class_name]
        for exchange_name in exchange_names:
            exchange = self.children.get(exchange_name, None)
            if exchange is not None:
                result.append(exchange)
        return result
