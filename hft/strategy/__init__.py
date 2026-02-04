"""
Strategy 策略模块

核心类：
- BaseStrategy: 策略基类，实现 get_trade_targets() 返回目标仓位
"""
from .base import BaseStrategy

__all__ = [
    "BaseStrategy",
]
