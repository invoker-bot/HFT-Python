"""
Indicator 指标基类

Feature 0006: Indicator 与 DataSource 统一架构

核心概念：
- BaseIndicator: 所有指标的基类，通过 scope 绑定到特定层级
- BaseDataSource: 从 exchange 获取数据的特殊 Indicator，使用 HealthyData/HealthyDataArray 存储
- ComputedIndicator: 从其他 Indicator/DataSource 派生计算的指标

事件机制（通过 event: AsyncIOEventEmitter）：
- 由子类自行定义和触发（如 FundingRate 的 "update" 事件）

get_vars 用途：
- 供 Executor.condition 表达式使用
- 供 Strategy 决策使用
- 通过 VM.inject_indicators() 注入到 FlowScopeNode
"""
import inspect
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Optional
from pyee.asyncio import AsyncIOEventEmitter
from ..core.listener import Listener

if TYPE_CHECKING:
    from ..core.scope.base import FlowScopeNode


# 默认过期时间（秒）
DEFAULT_DISABLE_SECONDS = 600.0  # 10 分钟

class BaseIndicator(Listener):
    """
    指标基类（Feature 0006 统一架构）

    通过 scope 绑定到特定层级（ExchangeClassScope、TradingPairClassScope 等）。
    由 AppCore.query_indicator() 创建/查询，支持 lazy 创建和自动停止。

    子类需要实现：
    - get_vars(): 返回变量字典，注入到 FlowScopeNode 供表达式求值
    """
    classes: dict[str, type['BaseIndicator']] = {}  #
    supported_scope: Optional[type['FlowScopeNode']] = None # 支持的 Scope 类型
    # 不 pickle 事件发射器
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "event", "scope")

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        # should use get_or_create and query indicator function:
        self.namespace: str = kwargs.get("namespace", "")
        self.scope: 'FlowScopeNode' = kwargs["scope"]
        self.event = AsyncIOEventEmitter()
        if self.supported_scope is not None:
            assert isinstance(self.scope.scope, self.supported_scope)
        # ... additional initialization ...

    @abstractmethod
    def get_vars(self) -> dict[str, Any]:
        """
        计算并返回该指标提供的变量字典, 仅在ready后计算

        Returns:
            变量字典，用于 Executor 的 condition 表达式求值
            例如 {"medal_edge": 0.0005, "rsi": 65.0}
        """

    def get_functions(self) -> dict[str, Any]:
        """
        返回该指标可提供的函数字典

        Returns:
            函数字典，用于 Executor 的 condition 表达式求值
            例如 {"is_overbought": func, "is_oversold": func}
        """
        return {}

    def __init_subclass__(cls, **kwargs):
        if not inspect.isabstract(cls):
            BaseIndicator.classes[cls.__name__] = cls  # 注册子类
