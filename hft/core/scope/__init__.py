"""
Scope 系统模块

提供多层级变量作用域管理，支持数据驱动的策略配置。
"""
from .base import BaseScope
from .manager import ScopeManager
from .vm import VirtualMachine

__all__ = [
    "BaseScope",
    "ScopeManager",
    "VirtualMachine",
]
