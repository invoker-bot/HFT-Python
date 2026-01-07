from typing import TYPE_CHECKING
from ..core.listener import Listener, ListenerState
from .base import BaseStrategy
from .config import BaseStrategyConfig

if TYPE_CHECKING:
    from ..core.app import AppCore


class StrategyGroups(Listener):

    def __init__(self):
        super().__init__("StrategyGroups", interval=60.0)

    async def add_strategy(self, strategy: BaseStrategy):
        self.add_child(strategy)
        if self.state in (ListenerState.STARTING, ListenerState.RUNNING):
            await strategy.start()

    async def remove_strategy(self, strategy: BaseStrategy):
        await strategy.stop()
        self.remove_child(strategy.name)

    async def on_start(self):
        # Placeholder for periodic tasks related to exchange groups
        app: 'AppCore' = self.root
        for strategy in list(self.children.values()):
            if strategy.name not in app.config.strategies:
                await self.remove_strategy(strategy)
        for strategy_name in app.config.strategies:
            if strategy_name not in self.children:
                strategy_config = BaseStrategyConfig.load(strategy_name)
                strategy_instance: BaseStrategy = strategy_config.instance
                await self.add_strategy(strategy_instance)

    async def is_finished(self) -> bool:
        return len(self.children) == 0
