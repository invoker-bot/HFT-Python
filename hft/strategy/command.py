"""
交易指令系统（已弃用）

.. deprecated::
    本模块属于旧的 Controller/Command 架构，已被新的 Strategy/Executor 架构取代。
    新架构中 Strategy 直接返回目标仓位，Executor 轮询执行。
    请使用 hft.strategy.base.BaseStrategy 替代。

Command: Controller 发出的交易指令
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from .pairs_strategy import TradingPairs


class OrderSide(Enum):
    """订单方向"""
    BUY = "buy"
    SELL = "sell"


class CommandType(Enum):
    """指令类型"""
    # 交易指令
    OPEN_LONG = "open_long"         # 开多
    OPEN_SHORT = "open_short"       # 开空
    CLOSE_LONG = "close_long"       # 平多
    CLOSE_SHORT = "close_short"     # 平空
    REDUCE_POSITION = "reduce"      # 减仓
    INCREASE_POSITION = "increase"  # 加仓

    # 数据源指令
    WATCH_DATASOURCE = "watch"      # 开启数据源监控
    UNWATCH_DATASOURCE = "unwatch"  # 关闭数据源监控

    # 系统指令
    CANCEL_ORDER = "cancel"         # 取消订单
    CANCEL_ALL = "cancel_all"       # 取消所有订单


class CommandStatus(Enum):
    """指令状态"""
    PENDING = "pending"             # 等待执行
    EXECUTING = "executing"         # 执行中
    PARTIAL = "partial"             # 部分完成
    COMPLETED = "completed"         # 完成
    CANCELLED = "cancelled"         # 已取消
    FAILED = "failed"               # 失败
    REJECTED = "rejected"           # 被 Executor 拒绝


@dataclass
class Command:
    """
    交易指令

    由 Controller 发出，传递给 Executor 执行

    Attributes:
        cmd_type: 指令类型
        pair: 目标交易对
        amount: 数量（以 base 货币计）
        priority: 优先级 0-100，越大越紧急
        price: 期望价格（可选，由 Executor 决定最终价格）
        timeout: 超时时间（秒）
        metadata: 额外元数据
    """
    cmd_type: CommandType
    pair: TradingPairs
    amount: float                               # 数量 (正数)
    priority: int = 50                          # 优先级 0-100
    price: Optional[float] = None               # 期望价格
    timeout: float = 60.0                       # 超时时间（秒）

    # 状态追踪
    status: CommandStatus = CommandStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    executed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # 执行结果
    filled_amount: float = 0.0                  # 已成交数量
    average_price: float = 0.0                  # 平均成交价
    order_ids: list[str] = field(default_factory=list)  # 关联的订单 ID

    # 元数据
    source: str = ""                            # 来源 (controller 名称)
    reason: str = ""                            # 发出原因
    metadata: dict = field(default_factory=dict)

    @property
    def side(self) -> OrderSide:
        """订单方向"""
        if self.cmd_type in (CommandType.OPEN_LONG, CommandType.CLOSE_SHORT, CommandType.INCREASE_POSITION):
            return OrderSide.BUY
        return OrderSide.SELL

    @property
    def is_open(self) -> bool:
        """是否是开仓指令"""
        return self.cmd_type in (CommandType.OPEN_LONG, CommandType.OPEN_SHORT)

    @property
    def is_close(self) -> bool:
        """是否是平仓指令"""
        return self.cmd_type in (CommandType.CLOSE_LONG, CommandType.CLOSE_SHORT)

    @property
    def is_reduce(self) -> bool:
        """是否是减仓指令"""
        return self.cmd_type == CommandType.REDUCE_POSITION

    @property
    def is_pending(self) -> bool:
        return self.status == CommandStatus.PENDING

    @property
    def is_executing(self) -> bool:
        return self.status == CommandStatus.EXECUTING

    @property
    def is_completed(self) -> bool:
        return self.status == CommandStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status in (CommandStatus.FAILED, CommandStatus.REJECTED, CommandStatus.CANCELLED)

    @property
    def unfilled_amount(self) -> float:
        """未成交数量"""
        return max(0.0, self.amount - self.filled_amount)

    @property
    def fill_ratio(self) -> float:
        """成交比例 0.0 - 1.0"""
        if self.amount <= 0:
            return 0.0
        return min(1.0, self.filled_amount / self.amount)

    def mark_executing(self) -> None:
        """标记为执行中"""
        self.status = CommandStatus.EXECUTING
        self.executed_at = datetime.now()

    def mark_completed(self, filled: float, avg_price: float) -> None:
        """标记为完成"""
        self.status = CommandStatus.COMPLETED
        self.filled_amount = filled
        self.average_price = avg_price
        self.completed_at = datetime.now()

    def mark_partial(self, filled: float, avg_price: float) -> None:
        """标记为部分完成"""
        self.status = CommandStatus.PARTIAL
        self.filled_amount = filled
        self.average_price = avg_price

    def mark_failed(self, reason: str = "") -> None:
        """标记为失败"""
        self.status = CommandStatus.FAILED
        self.reason = reason
        self.completed_at = datetime.now()

    def mark_rejected(self, reason: str = "") -> None:
        """标记为被拒绝"""
        self.status = CommandStatus.REJECTED
        self.reason = reason
        self.completed_at = datetime.now()

    def mark_cancelled(self) -> None:
        """标记为已取消"""
        self.status = CommandStatus.CANCELLED
        self.completed_at = datetime.now()


@dataclass
class WatchCommand:
    """
    数据源监控指令
    """
    pair: TradingPairs
    datasource_type: str                        # ticker, trades, ohlcv, orderbook
    watch: bool = True                          # True=watch, False=unwatch
    priority: int = 50
    created_at: datetime = field(default_factory=datetime.now)
    source: str = ""
