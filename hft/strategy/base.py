"""
Strategy 策略基类

策略只负责计算目标仓位，不负责执行。执行由 Executor 统一处理。

核心接口：
    get_target_positions_usd() -> TargetPositions
    返回策略期望的目标仓位（USD 计价）和执行紧急度

数据流：
    Executor.on_tick()
        -> 遍历所有 Strategy.get_target_positions_usd()
        -> 聚合目标仓位（position sum, speed 加权平均）
        -> 计算与当前仓位的差值
        -> 执行交易

退出流程：
1. Strategy.on_tick() 返回 True -> 策略从 StrategyGroup 中移除
2. StrategyGroup.is_finished 变为 True -> StrategyGroup.on_tick() 返回 True
3. AppCore.on_tick() 检测到策略组完成 -> 返回 True -> 程序正常退出
"""
from abc import abstractmethod
from typing import Optional, TYPE_CHECKING
from ..core.listener import Listener
from .config import BaseStrategyConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from .group import StrategyGroup


# 目标仓位类型: {exchange_class: {symbol: (position_usd, speed)}}
# position_usd: 正数=多仓，负数=空仓，单位 USD
# speed: 执行紧急度 [0.0, 1.0]，越高越急
TargetPositions = dict[str, dict[str, tuple[float, float]]]


class BaseStrategy(Listener):
    """
    策略基类

    策略的核心职责是计算目标仓位，不直接执行交易。
    Executor 会在每个 tick 调用 get_target_positions_usd() 获取目标，
    然后根据与当前仓位的差值决定是否执行交易。

    核心方法：
        get_target_positions_usd() -> TargetPositions
            返回 {exchange_class: {symbol: (position_usd, speed)}}
            - position_usd: 目标仓位价值（USD），正数=多仓，负数=空仓
            - speed: 执行紧急度 [0.0, 1.0]

    多策略聚合：
        - position_usd: 直接求和
        - speed: 按仓位绝对值加权平均

    退出机制：
        当策略完成任务后，on_tick() 返回 True 即可触发退出。

    Example:
        class MyStrategy(BaseStrategy):
            def get_target_positions_usd(self) -> TargetPositions:
                return {
                    "okx": {
                        "BTC/USDT:USDT": (5000.0, 0.3),  # $5000 多仓，不急
                        "ETH/USDT:USDT": (-2000.0, 0.8), # $2000 空仓，较急
                    }
                }

            async def on_tick(self) -> bool:
                # 策略逻辑（更新内部状态等）
                if self.should_exit():
                    return True
                return False

    Attributes:
        strategy_group: 所属的策略组（通过 parent 访问）
    """

    @property
    def strategy_group(self) -> Optional["StrategyGroup"]:
        """获取所属的策略组"""
        parent = self.parent
        from .group import StrategyGroup
        if isinstance(parent, StrategyGroup):
            return parent
        return None

    def __init__(self, config: 'BaseStrategyConfig'):
        super().__init__(name=config.path, interval=config.interval)
        self.config = config

    @abstractmethod
    def get_target_positions_usd(self) -> TargetPositions:
        """
        获取策略的目标仓位

        这是策略的核心输出方法。Executor 会在每个 tick 调用此方法，
        聚合所有策略的目标仓位后执行交易。

        Returns:
            {exchange_class: {symbol: (position_usd, speed)}}
            - exchange_class: 交易所类型，如 "okx", "binance"
            - symbol: 交易对，如 "BTC/USDT:USDT"
            - position_usd: 目标仓位价值（USD），正数=多仓，负数=空仓
            - speed: 执行紧急度 [0.0, 1.0]，越高执行越快

        Example:
            return {
                "okx": {
                    "BTC/USDT:USDT": (5000.0, 0.5),   # $5000 多仓
                    "ETH/USDT:USDT": (-2000.0, 0.8),  # $2000 空仓
                }
            }
        """
