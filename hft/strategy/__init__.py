"""
Strategy 策略模块

核心类：
- BaseStrategy: 策略基类，实现 get_trade_targets() 返回目标仓位
- StrategyGroup: 策略聚合器，汇总多个策略的目标

已弃用：
- TradeSignal, SignalSide: 未使用的事件驱动信号系统
"""
from .signal_strategy import TradeSignal, SignalSide  # deprecated
from .group import StrategyGroup
from .base import BaseStrategy

__all__ = [
    # 核心（新架构）
    "StrategyGroup",
    "BaseStrategy",
    # 已弃用
    "TradeSignal",
    "SignalSide",
]
