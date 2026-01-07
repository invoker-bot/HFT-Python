"""
Strategy 策略基类

整合 Controller, DataSource, Indicator, Executor 的顶层组件
"""
from abc import abstractmethod
from enum import Enum
from typing import Optional, TYPE_CHECKING
from ..core.listener import Listener
from .pairs import TradingPairsTable
from .controller import BaseController
from .command import Command, WatchCommand
from .config import BaseStrategyConfig
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from ..datasource.base import BaseDataSource
    from ..indicator.base import BaseIndicator
    from ..executor.base import BaseExecutor


class BaseStrategy(Listener):
    """
    策略基类

    职责：
    1. 管理 Controller（决策层）
    2. 管理 DataSource（数据层）
    3. 管理 Indicator（指标层）
    4. 管理 Executor（执行层）
    5. 处理策略状态（running/stopping）
    """

    def __init__(self, config: 'BaseStrategyConfig'):
        super().__init__(name=config.path, interval=config.interval)
        # 策略状态
        # self._strategy_state = StrategyState.STOPPED

        # 组件
        # self._exchanges: dict[str, "BaseExchange"] = {}
        # self._controllers: dict[str, BaseController] = {}
        # self._datasources: dict[str, "BaseDataSource"] = {}
        # self._indicators: dict[str, "BaseIndicator"] = {}
        # self._executors: dict[str, "BaseExecutor"] = {}
