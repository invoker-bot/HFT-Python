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

Feature 0008: Strategy 数据驱动增强
- 支持通用字典输出（StrategyOutput）
- 向后兼容旧格式（TargetPositions）
- 支持 requires、vars、conditional_vars
"""
# pylint: disable=import-outside-toplevel,protected-access
from typing import TYPE_CHECKING
from ..core.listener import Listener
# from ..core.scope.instance_ids import get_all_instance_ids
if TYPE_CHECKING:
    from ..core.scope.manager import ScopeManager
    from .config import BaseStrategyConfig

# 新版 Strategy 输出类型（Feature 0008）: {(exchange_path, symbol): {"字段名": 值, ...}}
# 支持任意字段，如 position_usd, speed, position_amount, max_position_usd 等
# 所有字段都会传递给 Executor，聚合到 strategies namespace


class BaseStrategy(Listener):
    """
    策略基类

    策略的核心职责是计算目标仓位，不直接执行交易。
    Executor 会在每个 tick 调用 get_target_positions_usd() 获取目标，
    然后根据与当前仓位的差值决定是否执行交易。

    核心方法：

    多策略聚合：
        - position_usd: 直接求和
        - speed: 按仓位绝对值加权平均

    退出机制：
        当策略完成任务后，on_tick() 返回 True 即可触发退出。

    Feature 0008: 数据驱动增强
        - requires: 依赖的 Indicator 列表
        - vars: 变量列表（按顺序计算）
        - conditional_vars: 条件变量（条件满足时更新）

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
    # def __init__(self, config: 'BaseStrategyConfig'):
    #     super().__init__(name=config.path, interval=config.interval)
    #     self.config = config
    #
    #     # Feature 0008: conditional_vars 状态持久化
    #     # {变量名: (当前值, 上次更新时间)}
    #     self._conditional_var_states: dict[str, tuple[Any, float]] = {}
    #
    #     # Feature 0012: Scope 系统
    #     self.scope_manager: Optional['ScopeManager'] = None
    #     self.scope_trees: list['LinkedScopeTree'] = []
    #     # 节点到树的映射（用于快速查找节点所属的树）
    #     self._node_to_tree: dict['LinkedScopeNode', 'LinkedScopeTree'] = {}

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.config: 'BaseStrategyConfig' = kwargs['config']

    @property
    def scope_manager(self) -> 'ScopeManager':
        return self.root.scope_manager

    # ============================================================
    # Feature 0008: 变量计算机制
    # ============================================================

    @property
    def indicator_group(self):
        """获取 IndicatorGroup"""
        return self.root.indicator_group

    # layer = self.root.vm.execute(self.config.flow, self.root)
    # @abstractmethod
    def calculate_flow_nodes(self):
        layer = self.root.vm.execute(self.config.flow, self.root)
        return layer
