"""
Executor 执行器模块

核心类：
- BaseExecutor: 执行器基类，每个 tick 轮询 StrategyGroup 获取目标仓位
- MarketExecutor: 市价单执行器
- SmartExecutor: 智能执行器（自动选择市价/限价）

工作流程：
    Executor.on_tick()
        -> StrategyGroup.get_aggregated_targets()
        -> 计算当前仓位与目标的差值
        -> 差值超过 per_order_usd 时执行交易
"""
# from .base import BaseExecutor, ExecutionResult, ExecutorState
# from .market_executor import MarketExecutor
# from .smart_executor import SmartExecutor
# from .spread_executor import (ASSpread, BaseSpread, DynamicSpread, FixedSpread,
#                               SpreadResult, StdSpread)

__all__ = [
    # base
    # "ExecutorState",
    # "ExecutionResult",
    # "BaseExecutor",
    # market
    # "MarketExecutor",
    # smart
    # "SmartExecutor",
    # spread
    # "SpreadResult",
    # "BaseSpread",
    # "FixedSpread",
    # "StdSpread",
    # "ASSpread",
    # "DynamicSpread",
]
