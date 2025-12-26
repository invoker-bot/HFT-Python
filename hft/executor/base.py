"""
Executor 执行器基类

低级策略，负责：
1. 根据 Command 和 Indicator 决定是否执行
2. 选择市价/限价、点位、拆单
3. 订单生命周期管理
4. 更新到 lighthouse
"""
from abc import abstractmethod
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from collections import deque
from ..core.listener import Listener
from ..strategy.command import Command, CommandStatus, OrderSide

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from ..indicator.base import BaseIndicator


class ExecutorState(Enum):
    """执行器状态"""
    IDLE = "idle"               # 空闲
    EXECUTING = "executing"     # 执行中
    PAUSED = "paused"           # 暂停


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"           # 市价单
    LIMIT = "limit"             # 限价单
    LIMIT_IOC = "limit_ioc"     # 限价IOC
    LIMIT_FOK = "limit_fok"     # 限价FOK


@dataclass
class OrderRecord:
    """订单记录"""
    order_id: str
    command: Command
    order_type: OrderType
    side: OrderSide
    price: float
    amount: float
    filled: float = 0.0
    status: str = "open"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_filled(self) -> bool:
        return self.status == "closed" and self.filled >= self.amount * 0.99

    @property
    def is_cancelled(self) -> bool:
        return self.status == "canceled"

    @property
    def is_open(self) -> bool:
        return self.status == "open"


class BaseExecutor(Listener):
    """
    执行器基类

    职责：
    1. 接收 Command 队列
    2. 根据 Indicator 和市场条件决定执行方式
    3. 管理订单生命周期
    4. 处理部分成交、超时、取消等情况
    """

    def __init__(
        self,
        name: str,
        exchange: "BaseExchange",
        interval: float = 0.1,
        max_pending_commands: int = 100,
        order_timeout: float = 60.0,
    ):
        super().__init__(name=name, interval=interval)
        self._exchange = exchange
        self._max_pending = max_pending_commands
        self._order_timeout = order_timeout

        # 状态
        self._executor_state = ExecutorState.IDLE

        # 命令队列（按优先级排序）
        self._command_queue: deque[Command] = deque(maxlen=max_pending_commands)

        # 活跃订单
        self._active_orders: dict[str, OrderRecord] = {}

        # 历史记录
        self._order_history: list[OrderRecord] = []
        self._max_history = 1000

        # 可选的指标依赖
        self._indicators: dict[str, "BaseIndicator"] = {}

    @property
    def exchange(self) -> "BaseExchange":
        return self._exchange

    @property
    def executor_state(self) -> ExecutorState:
        return self._executor_state

    @property
    def pending_commands(self) -> int:
        return len(self._command_queue)

    @property
    def active_orders(self) -> dict[str, OrderRecord]:
        return self._active_orders

    def add_indicator(self, name: str, indicator: "BaseIndicator") -> None:
        """添加指标依赖"""
        self._indicators[name] = indicator

    def get_indicator(self, name: str) -> Optional["BaseIndicator"]:
        """获取指标"""
        return self._indicators.get(name)

    def submit_command(self, command: Command) -> bool:
        """
        提交命令

        Returns:
            是否成功加入队列
        """
        if len(self._command_queue) >= self._max_pending:
            return False

        # 按优先级插入
        inserted = False
        for i, cmd in enumerate(self._command_queue):
            if command.priority > cmd.priority:
                self._command_queue.insert(i, command)
                inserted = True
                break

        if not inserted:
            self._command_queue.append(command)

        self.emit("command_submitted", command)
        return True

    def cancel_command(self, command: Command) -> bool:
        """取消命令"""
        if command in self._command_queue:
            self._command_queue.remove(command)
            command.mark_cancelled()
            self.emit("command_cancelled", command)
            return True
        return False

    # ========== 抽象方法 ==========

    @abstractmethod
    async def should_execute(self, command: Command) -> bool:
        """
        判断是否应该执行命令

        子类实现，可根据 Indicator 等条件判断
        """
        ...

    @abstractmethod
    async def decide_order_type(self, command: Command) -> OrderType:
        """
        决定订单类型

        子类实现
        """
        ...

    @abstractmethod
    async def decide_price(self, command: Command, order_type: OrderType) -> Optional[float]:
        """
        决定价格

        子类实现，返回 None 表示使用市价
        """
        ...

    @abstractmethod
    async def decide_split(self, command: Command) -> list[float]:
        """
        决定是否拆单

        子类实现，返回拆分后的数量列表
        例如 [0.5, 0.3, 0.2] 表示拆成 3 单
        """
        ...

    # ========== 核心逻辑 ==========

    async def execute_command(self, command: Command) -> bool:
        """执行单个命令"""
        command.mark_executing()
        self._executor_state = ExecutorState.EXECUTING

        try:
            # 1. 判断是否应该执行
            if not await self.should_execute(command):
                command.mark_rejected("Executor rejected")
                self.emit("command_rejected", command)
                return False

            # 2. 决定订单类型和价格
            order_type = await self.decide_order_type(command)
            price = await self.decide_price(command, order_type)

            # 3. 决定是否拆单
            splits = await self.decide_split(command)

            # 4. 执行订单
            total_filled = 0.0
            total_cost = 0.0

            for ratio in splits:
                amount = command.amount * ratio
                order = await self._place_order(command, order_type, amount, price)

                if order:
                    self._active_orders[order.order_id] = order
                    command.order_ids.append(order.order_id)

            return True

        except Exception as e:
            command.mark_failed(str(e))
            self.emit("command_failed", {"command": command, "error": str(e)})
            return False

        finally:
            self._executor_state = ExecutorState.IDLE

    async def _place_order(
        self,
        command: Command,
        order_type: OrderType,
        amount: float,
        price: Optional[float],
    ) -> Optional[OrderRecord]:
        """下单"""
        # TODO: 实现实际下单逻辑
        # order = await self._exchange.place_order(...)
        pass

    async def update_orders(self) -> None:
        """更新活跃订单状态"""
        for order_id, record in list(self._active_orders.items()):
            try:
                # TODO: 查询订单状态
                # order = await self._exchange.fetch_order(order_id)
                # record.status = order['status']
                # record.filled = order['filled']
                # record.updated_at = datetime.now()

                # 检查是否完成
                if record.is_filled or record.is_cancelled:
                    del self._active_orders[order_id]
                    self._order_history.append(record)
                    if len(self._order_history) > self._max_history:
                        self._order_history = self._order_history[-self._max_history:]

            except Exception as e:
                self.emit("order_update_error", {"order_id": order_id, "error": str(e)})

    async def cancel_stale_orders(self) -> None:
        """取消超时订单"""
        now = datetime.now()
        for order_id, record in list(self._active_orders.items()):
            elapsed = (now - record.created_at).total_seconds()
            if elapsed > self._order_timeout and record.is_open:
                try:
                    # TODO: 取消订单
                    # await self._exchange.cancel_order(order_id)
                    record.status = "canceled"
                    del self._active_orders[order_id]
                    self._order_history.append(record)
                except Exception as e:
                    self.emit("cancel_error", {"order_id": order_id, "error": str(e)})

    async def tick_callback(self) -> bool:
        """每 tick 处理命令队列和更新订单"""
        # 1. 更新活跃订单状态
        await self.update_orders()

        # 2. 取消超时订单
        await self.cancel_stale_orders()

        # 3. 处理命令队列
        if self._command_queue and self._executor_state == ExecutorState.IDLE:
            command = self._command_queue.popleft()
            await self.execute_command(command)

        return True


class SimpleExecutor(BaseExecutor):
    """
    简单执行器

    直接使用市价单执行
    """

    async def should_execute(self, command: Command) -> bool:
        return True

    async def decide_order_type(self, command: Command) -> OrderType:
        return OrderType.MARKET

    async def decide_price(self, command: Command, order_type: OrderType) -> Optional[float]:
        return None  # 市价单不需要价格

    async def decide_split(self, command: Command) -> list[float]:
        return [1.0]  # 不拆单


class SmartExecutor(BaseExecutor):
    """
    智能执行器

    根据市场条件选择订单类型和价格
    支持拆单
    """

    def __init__(
        self,
        name: str,
        exchange: "BaseExchange",
        split_threshold: float = 1.0,       # 超过此数量才拆单
        max_splits: int = 5,                # 最大拆单数
        use_limit_order: bool = True,       # 优先使用限价单
        **kwargs,
    ):
        super().__init__(name=name, exchange=exchange, **kwargs)
        self._split_threshold = split_threshold
        self._max_splits = max_splits
        self._use_limit_order = use_limit_order

    async def should_execute(self, command: Command) -> bool:
        """根据指标判断是否执行"""
        # 可以根据 indicator 判断
        for name, indicator in self._indicators.items():
            result = indicator.last_result
            if result and hasattr(result, 'confidence'):
                if result.confidence < 0.3:
                    return False
        return True

    async def decide_order_type(self, command: Command) -> OrderType:
        """根据优先级决定订单类型"""
        if command.priority >= 90:
            return OrderType.MARKET  # 高优先级用市价单
        elif self._use_limit_order:
            return OrderType.LIMIT
        else:
            return OrderType.MARKET

    async def decide_price(self, command: Command, order_type: OrderType) -> Optional[float]:
        """计算限价单价格"""
        if order_type == OrderType.MARKET:
            return None

        # 使用命令中的期望价格，或者从订单簿获取
        if command.price:
            return command.price

        # TODO: 从订单簿获取最优价格
        return None

    async def decide_split(self, command: Command) -> list[float]:
        """决定拆单策略"""
        if command.amount <= self._split_threshold:
            return [1.0]

        # 根据数量决定拆单数
        num_splits = min(
            self._max_splits,
            max(2, int(command.amount / self._split_threshold))
        )

        # 均匀拆分
        ratio = 1.0 / num_splits
        return [ratio] * num_splits
