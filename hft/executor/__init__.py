"""
Executor 执行器模块

新架构（轮询模式）：
- BaseExecutor: 执行器基类，每个 tick 轮询 StrategyGroup 获取目标仓位
- MarketExecutor: 市价单执行器

工作流程：
    Executor.on_tick()
        -> StrategyGroup.get_aggregated_targets()
        -> 计算当前仓位与目标的差值
        -> 差值超过 per_order_usd 时执行交易

遗留架构（已弃用）：
- order_executor 模块中的类已弃用，将在未来版本中移除
- 请使用新的 BaseExecutor/MarketExecutor 替代
"""
from .base import (
    ExecutorState,
    ExecutionResult,
    BaseExecutor,
)
from .market_executor import MarketExecutor
from .spread_executor import (
    SpreadResult,
    BaseSpread,
    FixedSpread,
    StdSpread,
    ASSpread,
    DynamicSpread,
)

# 主要导出（新架构）
__all__ = [
    # base
    "ExecutorState",
    "ExecutionResult",
    "BaseExecutor",
    # market
    "MarketExecutor",
    # spread
    "SpreadResult",
    "BaseSpread",
    "FixedSpread",
    "StdSpread",
    "ASSpread",
    "DynamicSpread",
]


# ============================================================
# 遗留导出（已弃用，将在未来版本中移除）
# ============================================================

def __getattr__(name):
    """延迟导入遗留模块，并发出弃用警告"""
    import warnings

    legacy_exports = {
        "OrderState",
        "BaseOrderExecutor",
        "LegacyMarketOrderExecutor",
        "LimitOrderExecutor",
        "MultipleLimitOrderExecutor",
        "LevelConfig",
    }

    if name in legacy_exports:
        warnings.warn(
            f"{name} is deprecated and will be removed in a future version. "
            "Please use BaseExecutor/MarketExecutor instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from . import order_executor
        if name == "LegacyMarketOrderExecutor":
            return order_executor.MarketOrderExecutor
        return getattr(order_executor, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
