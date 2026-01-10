"""
策略组管理模块

StrategyGroup 管理多个策略实例：
- 动态加载/卸载策略
- 为 Executor 提供聚合的目标仓位接口
- 支持级联退出：所有策略完成后触发 AppCore 退出

数据流：
    Executor.on_tick()
        -> StrategyGroup.get_aggregated_targets()
            -> 遍历所有 Strategy.get_target_positions_usd()
            -> 聚合（position sum, speed 加权平均）
        -> Executor 执行交易

退出流程：
    1. Strategy.on_tick() 返回 True -> 策略完成
    2. 策略被从 StrategyGroup 移除
    3. StrategyGroup.is_finished 变为 True
    4. StrategyGroup.on_tick() 返回 True
    5. AppCore.on_tick() 检测到并退出
"""
from typing import TYPE_CHECKING
from collections import defaultdict
from ..core.listener import Listener, ListenerState
from .base import BaseStrategy, TargetPositions
from .config import BaseStrategyConfig

if TYPE_CHECKING:
    from ..core.app import AppCore


# 聚合后的目标仓位类型（与 TargetPositions 相同）
AggregatedTargets = dict[str, dict[str, tuple[float, float]]]


class StrategyGroup(Listener):
    """
    策略组管理器

    管理多个策略，为 Executor 提供聚合的目标仓位。

    核心方法：
        get_aggregated_targets() -> AggregatedTargets
            聚合所有策略的目标仓位：
            - position_usd: 直接求和
            - speed: 按仓位绝对值加权平均

    Example:
        # Strategy A 返回
        {"okx": {"BTC/USDT:USDT": (5000.0, 0.3)}}

        # Strategy B 返回
        {"okx": {"BTC/USDT:USDT": (3000.0, 0.9)}}

        # 聚合结果
        {"okx": {"BTC/USDT:USDT": (8000.0, 0.525)}}
        # position = 5000 + 3000 = 8000
        # speed = (5000*0.3 + 3000*0.9) / (5000+3000) = 0.525
    """

    def __init__(self):
        super().__init__("StrategyGroup", interval=60.0)

    async def add_strategy(self, strategy: BaseStrategy):
        """添加策略"""
        self.add_child(strategy)
        if self.state in (ListenerState.STARTING, ListenerState.RUNNING):
            await strategy.start()

    async def remove_strategy(self, strategy: BaseStrategy):
        """移除策略"""
        await strategy.stop()
        self.remove_child(strategy.name)

    async def on_start(self):
        """启动时加载配置的策略"""
        app: 'AppCore' = self.root
        for strategy in list(self.children.values()):
            if strategy.name not in app.config.strategies:
                await self.remove_strategy(strategy)
        for strategy_name in app.config.strategies:
            if strategy_name not in self.children:
                strategy_config = BaseStrategyConfig.load(strategy_name)
                strategy_instance: BaseStrategy = strategy_config.instance
                await self.add_strategy(strategy_instance)

    @property
    def strategies(self) -> list[BaseStrategy]:
        """获取所有策略列表"""
        return list(self.children.values())

    def get_aggregated_targets(self) -> AggregatedTargets:
        """
        聚合所有策略的目标仓位

        聚合规则：
        - position_usd: 直接求和
        - speed: 按仓位绝对值加权平均

        Returns:
            {exchange_class: {symbol: (aggregated_position_usd, aggregated_speed)}}
        """
        # 临时存储: exchange_class -> symbol -> [(position, speed), ...]
        temp: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
            lambda: defaultdict(list)
        )

        # 收集所有策略的目标
        for strategy in self.strategies:
            try:
                targets = strategy.get_target_positions_usd()
                for exchange_class, symbols in targets.items():
                    for symbol, (position, speed) in symbols.items():
                        temp[exchange_class][symbol].append((position, speed))
            except Exception as e:
                self.logger.warning(
                    "Error getting targets from %s: %s", strategy.name, e
                )

        # 聚合
        result: AggregatedTargets = {}
        for exchange_class, symbols in temp.items():
            result[exchange_class] = {}
            for symbol, values in symbols.items():
                # position: 直接求和
                total_position = sum(pos for pos, _ in values)

                # speed: 按仓位绝对值加权平均
                total_weight = sum(abs(pos) for pos, _ in values)
                if total_weight > 0:
                    weighted_speed = sum(
                        abs(pos) * speed for pos, speed in values
                    ) / total_weight
                else:
                    weighted_speed = 0.5  # 默认值

                result[exchange_class][symbol] = (total_position, weighted_speed)

        return result

    @property
    def is_finished(self) -> bool:
        """
        检查策略组是否已完成

        当没有任何策略在运行时，认为策略组已完成。
        这会触发 AppCore 的退出流程。

        Returns:
            True 如果没有策略在运行
        """
        return len(self.children) == 0

    async def on_tick(self) -> bool:
        """
        策略组定时回调

        当所有策略都已完成（children 为空）时返回 True，
        触发策略组退出，进而触发 AppCore 退出。

        Returns:
            True 如果所有策略都已完成，否则 False
        """
        if self.is_finished:
            self.logger.info("All strategies finished, StrategyGroup exiting")
            return True
        return False

    @property
    def log_state_dict(self) -> dict:
        return {
            "strategies": len(self.children),
        }
