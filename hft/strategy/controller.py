"""
Controller 决策层（已弃用）

.. deprecated::
    本模块属于旧的 Controller/Command 架构，已被新的 Strategy/Executor 架构取代。
    新架构中 Strategy 直接返回目标仓位，Executor 轮询执行。
    请使用 hft.strategy.base.BaseStrategy 替代。

Controller: 宏观决策类，负责发出交易指令
InfiniteController: 无限时间决策，一直执行
FiniteController: 有限时间决策，执行完后关闭
"""
from abc import abstractmethod
from enum import Enum
from typing import Optional, TYPE_CHECKING
from ..core.listener import Listener
from .pairs_strategy import TradingPairsTable, TradingPairs
from .command import Command, WatchCommand

if TYPE_CHECKING:
    from .base import BaseStrategy


class ControllerState(Enum):
    """Controller 状态"""
    IDLE = "idle"               # 空闲
    DECIDING = "deciding"       # 决策中
    WAITING = "waiting"         # 等待执行结果
    COMPLETED = "completed"     # 已完成（仅 FiniteController）


class BaseController(Listener):
    """
    Controller 基类

    职责：
    1. 根据 TradingPairsTable 选择交易对
    2. 根据 strategy config 和 exchange 仓位发出交易指令
    3. 发出 WatchCommand 控制 DataSource 的开启/关闭
    """

    def __init__(
        self,
        name: str,
        table: Optional[TradingPairsTable] = None,
        interval: float = 1.0,
    ):
        super().__init__(name=name, interval=interval)
        self._table = table or TradingPairsTable()
        self._controller_state = ControllerState.IDLE
        self._pending_commands: list[Command] = []
        self._pending_watch_commands: list[WatchCommand] = []

    @property
    def table(self) -> TradingPairsTable:
        return self._table

    @table.setter
    def table(self, value: TradingPairsTable) -> None:
        self._table = value

    @property
    def controller_state(self) -> ControllerState:
        return self._controller_state

    @property
    def strategy(self) -> Optional["BaseStrategy"]:
        """获取所属 Strategy（如果有）"""
        parent = self.parent
        from .base import BaseStrategy
        if isinstance(parent, BaseStrategy):
            return parent
        return None

    def emit_command(self, command: Command) -> None:
        """发出交易指令"""
        command.source = self.name
        self._pending_commands.append(command)
        self.emit("command", command)

    def emit_watch(self, pair: TradingPairs, datasource_type: str, watch: bool = True) -> None:
        """发出数据源监控指令"""
        cmd = WatchCommand(
            pair=pair,
            datasource_type=datasource_type,
            watch=watch,
            source=self.name,
        )
        self._pending_watch_commands.append(cmd)
        self.emit("watch_command", cmd)

    def consume_commands(self) -> list[Command]:
        """消费并清空待处理的交易指令"""
        commands = self._pending_commands
        self._pending_commands = []
        return commands

    def consume_watch_commands(self) -> list[WatchCommand]:
        """消费并清空待处理的监控指令"""
        commands = self._pending_watch_commands
        self._pending_watch_commands = []
        return commands

    @abstractmethod
    async def decide(self) -> None:
        """
        决策逻辑，子类实现

        在此方法中：
        1. 分析 table 中的交易对
        2. 查询 exchange 的仓位情况
        3. 调用 emit_command() 发出交易指令
        4. 调用 emit_watch() 控制数据源
        """
        ...

    async def tick_callback(self) -> bool:
        """每 tick 调用 decide()"""
        self._controller_state = ControllerState.DECIDING
        try:
            await self.decide()
            return True
        finally:
            self._controller_state = ControllerState.IDLE


class InfiniteController(BaseController):
    """
    无限时间决策 Controller

    一直执行，直到被手动停止
    """

    @abstractmethod
    async def decide(self) -> None:
        ...


class FiniteController(BaseController):
    """
    有限时间决策 Controller

    执行完后自动停止
    """

    def __init__(
        self,
        name: str,
        table: Optional[TradingPairsTable] = None,
        interval: float = 1.0,
        max_decisions: int = 1,
    ):
        super().__init__(name=name, table=table, interval=interval)
        self._max_decisions = max_decisions
        self._decision_count = 0

    @property
    def is_completed(self) -> bool:
        return self._controller_state == ControllerState.COMPLETED

    @property
    def remaining_decisions(self) -> int:
        return max(0, self._max_decisions - self._decision_count)

    async def tick_callback(self) -> bool:
        if self.is_completed:
            return False

        self._controller_state = ControllerState.DECIDING
        try:
            await self.decide()
            self._decision_count += 1

            if self._decision_count >= self._max_decisions:
                self._controller_state = ControllerState.COMPLETED
                await self.stop()
            return True
        except Exception:
            self._controller_state = ControllerState.IDLE
            raise

    @abstractmethod
    async def decide(self) -> None:
        ...


class ManualController(FiniteController):
    """
    手动输入决策 Controller

    用于用户临时的决策输入
    """

    def __init__(
        self,
        name: str,
        commands: Optional[list[Command]] = None,
    ):
        super().__init__(name=name, max_decisions=1)
        self._manual_commands = commands or []

    def add_command(self, command: Command) -> None:
        """添加手动指令"""
        self._manual_commands.append(command)

    async def decide(self) -> None:
        """执行所有手动指令"""
        for cmd in self._manual_commands:
            self.emit_command(cmd)
        self._manual_commands.clear()
