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

Feature 0008: Strategy 数据驱动增强
- 支持新格式 StrategyOutput（通用字典）
- 向后兼容旧格式 TargetPositions
- 聚合结果返回新格式 AggregatedTargets
"""
from typing import TYPE_CHECKING, Any

from ..core.listener import Listener, ListenerState
from ..plugin import pm
from .base import BaseStrategy, StrategyOutput, TargetPositions

if TYPE_CHECKING:
    from ..core.app import AppCore


# 聚合后的目标仓位类型（Feature 0008 新格式）
# {(exchange_path, symbol): {"字段名": [值列表], ...}}
# 所有字段都聚合为列表，供 Executor 的 vars 表达式使用
AggregatedTargets = dict[tuple[str, str], dict[str, list[Any]]]


def _normalize_strategy_output(
    raw_output: TargetPositions | StrategyOutput
) -> StrategyOutput:
    """
    将旧格式 TargetPositions 转换为新格式 StrategyOutput

    旧格式: {(exchange_path, symbol): (position_usd, speed)}
    新格式: {(exchange_path, symbol): {"position_usd": ..., "speed": ...}}
    """
    result: StrategyOutput = {}
    for key, value in raw_output.items():
        if isinstance(value, tuple):
            # 旧格式: (position_usd, speed)
            position_usd, speed = value
            result[key] = {"position_usd": position_usd, "speed": speed}
        elif isinstance(value, dict):
            # 新格式: 直接使用
            result[key] = value
        else:
            # 未知格式，忽略
            pass
    return result


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
        {("okx/main", "BTC/USDT:USDT"): (5000.0, 0.3)}

        # Strategy B 返回
        {("okx/main", "BTC/USDT:USDT"): (3000.0, 0.9)}

        # 聚合结果
        {("okx/main", "BTC/USDT:USDT"): (8000.0, 0.525)}
        # position = 5000 + 3000 = 8000
        # speed = (5000*0.3 + 3000*0.9) / (5000+3000) = 0.525
    """

    def __init__(self):
        super().__init__("StrategyGroup", interval=60.0)
        self._initialized = False  # 标记是否已初始化加载策略

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

        # 获取策略配置路径
        strategy_path = app.config.strategy
        strategy_name = strategy_path.name

        # 移除不需要的策略
        for strategy in list(self.children.values()):
            if strategy.name != strategy_name:
                await self.remove_strategy(strategy)

        # 添加配置的策略
        if strategy_name not in self.children:
            strategy_config = strategy_path.instance
            strategy_instance: BaseStrategy = strategy_config.instance
            await self.add_strategy(strategy_instance)

        self._initialized = True  # 标记已完成初始化

    @property
    def strategies(self) -> list[BaseStrategy]:
        """获取所有策略列表"""
        return list(self.children.values())

    def get_aggregated_targets(self) -> AggregatedTargets:
        """
        聚合所有策略的目标仓位（Issue 0013：单策略标量化）

        聚合规则（单策略场景）：
        - 当前仅支持单策略，所有字段直接是标量值（不再是列表）
        - Executor 通过 strategies["字段名"] 直接访问值（不需要 sum/avg）

        Returns:
            {(exchange_path, symbol): {"字段名": 值, ...}}
        """
        # 临时存储: (exchange_path, symbol) -> {"字段名": 值, ...}
        temp: dict[tuple[str, str], dict[str, Any]] = {}

        # 收集所有策略的目标（当前只支持单策略）
        for strategy in self.strategies:
            try:
                raw_targets = strategy.get_target_positions_usd()
                # 规范化为新格式
                targets = _normalize_strategy_output(raw_targets)
                # 插件钩子：单个策略目标计算完成
                pm.hook.on_strategy_targets_calculated(strategy=strategy, targets=targets)

                for key, fields in targets.items():
                    # Issue 0013: 单策略场景，直接使用值（不聚合为列表）
                    if key in temp:
                        self.logger.warning(
                            "Multiple strategies targeting same key %s, using last value",
                            key
                        )
                    temp[key] = dict(fields)
            except Exception as e:
                self.logger.warning(
                    "Error getting targets from %s: %s", strategy.name, e
                )

        # 插件钩子：目标聚合完成
        pm.hook.on_targets_aggregated(strategy_group=self, targets=temp)

        return temp

    @property
    def is_finished(self) -> bool:
        """
        检查策略组是否已完成

        当初始化完成且没有任何策略在运行时，认为策略组已完成。
        这会触发 AppCore 的退出流程。

        Returns:
            True 如果初始化完成且没有策略在运行
        """
        return self._initialized and len(self.children) == 0

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
