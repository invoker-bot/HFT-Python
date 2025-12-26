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

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from ..datasource.base import BaseDataSource
    from ..indicator.base import BaseIndicator
    from ..executor.base import BaseExecutor


class StrategyState(Enum):
    """策略状态"""
    STOPPED = "stopped"         # 已停止
    STARTING = "starting"       # 启动中
    RUNNING = "running"         # 运行中
    STOPPING = "stopping"       # 停止中（平仓等）


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

    def __init__(
        self,
        name: str,
        table: Optional[TradingPairsTable] = None,
        interval: float = 1.0,
    ):
        super().__init__(name=name, interval=interval)

        # 交易对表
        self._table = table or TradingPairsTable()

        # 策略状态
        self._strategy_state = StrategyState.STOPPED

        # 组件
        self._exchanges: dict[str, "BaseExchange"] = {}
        self._controllers: dict[str, BaseController] = {}
        self._datasources: dict[str, "BaseDataSource"] = {}
        self._indicators: dict[str, "BaseIndicator"] = {}
        self._executors: dict[str, "BaseExecutor"] = {}

    # ========== 属性 ==========

    @property
    def table(self) -> TradingPairsTable:
        return self._table

    @table.setter
    def table(self, value: TradingPairsTable) -> None:
        self._table = value
        # 更新所有 controller 的 table
        for controller in self._controllers.values():
            controller.table = value

    @property
    def strategy_state(self) -> StrategyState:
        return self._strategy_state

    @property
    def is_running(self) -> bool:
        return self._strategy_state == StrategyState.RUNNING

    @property
    def is_stopping(self) -> bool:
        return self._strategy_state == StrategyState.STOPPING

    @property
    def exchanges(self) -> dict[str, "BaseExchange"]:
        return self._exchanges

    @property
    def controllers(self) -> dict[str, BaseController]:
        return self._controllers

    @property
    def datasources(self) -> dict[str, "BaseDataSource"]:
        return self._datasources

    @property
    def indicators(self) -> dict[str, "BaseIndicator"]:
        return self._indicators

    @property
    def executors(self) -> dict[str, "BaseExecutor"]:
        return self._executors

    # ========== 组件管理 ==========

    def add_exchange(self, name: str, exchange: "BaseExchange") -> None:
        """添加交易所"""
        self._exchanges[name] = exchange

    def add_controller(self, name: str, controller: BaseController) -> None:
        """添加决策器"""
        self._controllers[name] = controller
        controller.table = self._table
        self.add_child(controller)

        # 监听决策器的命令
        controller.on("command", self._on_command)
        controller.on("watch_command", self._on_watch_command)

    def add_datasource(self, name: str, datasource: "BaseDataSource") -> None:
        """添加数据源"""
        self._datasources[name] = datasource
        self.add_child(datasource)

    def add_indicator(self, name: str, indicator: "BaseIndicator") -> None:
        """添加指标"""
        self._indicators[name] = indicator
        self.add_child(indicator)

    def add_executor(self, name: str, executor: "BaseExecutor") -> None:
        """添加执行器"""
        self._executors[name] = executor
        self.add_child(executor)

    def get_exchange(self, name: str) -> Optional["BaseExchange"]:
        return self._exchanges.get(name)

    def get_controller(self, name: str) -> Optional[BaseController]:
        return self._controllers.get(name)

    def get_datasource(self, name: str) -> Optional["BaseDataSource"]:
        return self._datasources.get(name)

    def get_indicator(self, name: str) -> Optional["BaseIndicator"]:
        return self._indicators.get(name)

    def get_executor(self, name: str) -> Optional["BaseExecutor"]:
        return self._executors.get(name)

    # ========== 命令处理 ==========

    def _on_command(self, command: Command) -> None:
        """处理交易命令"""
        if self.is_stopping:
            # 停止状态下只接受平仓命令
            if not command.is_close:
                command.mark_rejected("Strategy is stopping")
                return

        # 分发给对应的 executor
        pair = command.pair
        executor = self._get_executor_for_pair(pair)
        if executor:
            executor.submit_command(command)
        else:
            command.mark_rejected("No executor available")

    def _on_watch_command(self, cmd: WatchCommand) -> None:
        """处理数据源监控命令"""
        ds_key = f"{cmd.datasource_type}:{cmd.pair.symbol}"
        datasource = self._datasources.get(ds_key)
        if datasource:
            if cmd.watch:
                datasource.request_watch()
            # unwatch 由 datasource 的 auto-unwatch 机制处理

    def _get_executor_for_pair(self, pair) -> Optional["BaseExecutor"]:
        """获取处理特定交易对的执行器"""
        # 默认返回第一个 executor，子类可覆盖
        if self._executors:
            return next(iter(self._executors.values()))
        return None

    # ========== 生命周期 ==========

    async def on_start(self) -> None:
        """启动策略"""
        self._strategy_state = StrategyState.STARTING
        await self.setup()
        self._strategy_state = StrategyState.RUNNING
        self.emit("strategy_started", self.name)

    async def on_stop(self) -> None:
        """停止策略"""
        self._strategy_state = StrategyState.STOPPING
        await self.teardown()
        self._strategy_state = StrategyState.STOPPED
        self.emit("strategy_stopped", self.name)

    def request_stop(self) -> None:
        """请求停止策略（进入 stopping 状态）"""
        if self._strategy_state == StrategyState.RUNNING:
            self._strategy_state = StrategyState.STOPPING
            self.emit("strategy_stopping", self.name)

    # ========== 抽象方法 ==========

    @abstractmethod
    async def setup(self) -> None:
        """
        初始化策略

        子类实现：创建 controller, datasource, indicator, executor
        """
        ...

    @abstractmethod
    async def teardown(self) -> None:
        """
        清理策略

        子类实现：平仓、取消订单等
        """
        ...

    @abstractmethod
    async def update_table(self) -> None:
        """
        更新交易对表

        子类实现：根据市场数据更新 table 的 score
        """
        ...

    async def tick_callback(self) -> bool:
        """每 tick 更新"""
        if self.is_running:
            await self.update_table()
        return True
