"""
Indicator 指标基类

Feature 0006: Indicator 与 DataSource 统一架构

核心概念：
- BaseIndicator: 所有指标的基类，使用 HealthyDataArray 存储数据
- GlobalIndicator: 全局唯一的指标（如全局资金费率），更长过期时间
- BaseDataSource: 从 exchange 获取数据的特殊 Indicator

事件机制（通过 _event: AsyncIOEventEmitter）：
- update: 新数据写入 _data 后触发，载荷 (timestamp: float, value: T)
- ready: 从 not ready 变为 ready 时触发，载荷 ()
- error: 发生错误时触发，载荷 (error: Exception)

ready_condition 表达式变量：
- timeout: 当前时间与最新数据的时间差（秒）
- cv: 采样间隔变异系数（需要 window）
- range: 覆盖比例（需要 window）

calculate_vars 用途：
- 供 Executor.condition 表达式使用
- 供 Strategy 决策使用
- 不用于 ready_condition 求值
"""
import inspect
from abc import abstractmethod
from typing import TYPE_CHECKING, Any
from pyee.asyncio import AsyncIOEventEmitter
from ..core.listener import Listener

if TYPE_CHECKING:
    from ..core.scope.base import FlowScopeNode



# 默认过期时间（秒）
DEFAULT_DISABLE_SECONDS = 600.0  # 10 分钟

class BaseIndicator(Listener):
    """
    指标基类（Feature 0006 统一架构）

    特性：
    1. 使用 HealthyDataArray 存储时序数据
    2. 通过 event 发出 update/ready/error 事件
    3. 支持 ready_condition 表达式判断就绪状态
    4. 自动过期机制（长时间未 query 自动停止）

    子类需要实现：
    - calculate_vars(direction): 返回变量字典供 Executor 使用
    """
    classes:dict[str, type['BaseIndicator']] = {}  #

    # 不 pickle 事件发射器
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "event", "scope")

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        # should use get_or_create and query indicator function:
        self.namespace: str = kwargs.get("namespace", "")
        self.scope: 'FlowScopeNode' = kwargs["scope"]
        self.event = AsyncIOEventEmitter()
        # ... additional initialization ...

    @abstractmethod
    def get_vars(self) -> dict[str, Any]:
        """
        计算并返回该指标提供的变量字典, 仅在ready后计算

        Returns:
            变量字典，用于 Executor 的 condition 表达式求值
            例如 {"medal_edge": 0.0005, "rsi": 65.0}
        """

    def __init_subclass__(cls, **kwargs):
        if not inspect.isabstract(cls):
            BaseIndicator.classes[cls.__name__] = cls  # 注册子类
