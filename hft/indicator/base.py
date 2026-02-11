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
from functools import cached_property
from typing import TypeVar, Generic, TYPE_CHECKING, Any, Optional
from abc import abstractmethod
from pyee.asyncio import AsyncIOEventEmitter
from ..core.listener import Listener
from ..core.duration import parse_duration
from ..core.healthy_data import HealthyData, HealthyDataArray
from ..core.scope.scopes import ExchangeClassScope, ExchangeScope, TradingPairClassScope, TradingPairScope
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from ..core.scope.base import FlowScopeNode

# 默认过期时间（秒）
T = TypeVar('T')  # 数据类型

class BaseIndicator(Listener):
    """
    指标基类（Feature 0006 统一架构）

    通过 scope 绑定到特定层级（ExchangeClassScope、TradingPairClassScope 等）。
    由 AppCore.query_indicator() 创建/查询，支持 lazy 创建和自动停止。

    子类需要实现：
    - get_vars(): 返回变量字典，注入到 FlowScopeNode 供表达式求值
    """
    DEFAULT_DISABLE_SECONDS = 600.0  # 10 分钟
    classes: dict[str, type['BaseIndicator']] = {}  #
    supported_scope: Optional[type['FlowScopeNode']] = None # 支持的 Scope 类型
    # 不 pickle 事件发射器
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "event", "scope"}

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

class BaseDataIndicator(Generic[T], BaseIndicator):
    """
    数据源基类

    数据存储在 self.data（HealthyData 或 HealthyDataArray）中。

    子类需要实现：
    - get_vars(): 返回变量字典
    - get_functions(): 返回函数字典（可选）
    """
    DEFAULT_IS_ARRAY = True
    DEFAULT_MAX_AGE = 15.0 # 15 秒数据过期，有可能过松，或者过严
    DEFAULT_WINDOW = 600.0  # 10 分钟的数据窗口
    DEFAULT_HEALTHY_WINDOW = 300.0 # 默认5分钟健康窗口
    DEFAULT_DUPLICATE_TIMESTAMP_DELTA = 1e-6
    DEFAULT_HEALTHY_POINTS = 3  # 最少数据点数，越多越严格，采样是否充足
    DEFAULT_HEALTHY_CV = 0.8  # 最多变异系
    DEFAULT_HEALTHY_RANGE = 0.6  # 最少覆盖比例

    @classmethod
    def get_healthy_data_params(cls, kwargs: dict[str, Any]) -> dict[str, Any]:
        max_age = parse_duration(kwargs.get("max_age", cls.DEFAULT_MAX_AGE))
        window = parse_duration(kwargs.get("window", cls.DEFAULT_WINDOW))
        duplicate_timestamp_delta = parse_duration(kwargs.get(
            "duplicate_timestamp_delta",
            cls.DEFAULT_DUPLICATE_TIMESTAMP_DELTA
        ))
        healthy_points: int = kwargs.get("healthy_points", cls.DEFAULT_HEALTHY_POINTS)  # 最少数据点数，越多越严格，采样是否充足
        healthy_cv: float = kwargs.get("healthy_cv", cls.DEFAULT_HEALTHY_CV)  # 最多变异系数（倒数），越大越严格，采样间隔是否均匀
        healthy_range: float = kwargs.get("healthy_range", cls.DEFAULT_HEALTHY_RANGE)
        healthy_window = parse_duration(kwargs.get("healthy_window", cls.DEFAULT_HEALTHY_WINDOW))
        return {
            "max_age": max_age,
            "window": window,
            "duplicate_timestamp_delta": duplicate_timestamp_delta,
            "healthy_points": healthy_points,
            "healthy_cv": healthy_cv,
            "healthy_range": healthy_range,
            "healthy_window": healthy_window,
        }

    def create_healthy_data_array(self) -> HealthyDataArray[T]:
        return HealthyDataArray[T](**self.data_array_params)

    def create_healthy_data(self) -> HealthyData[T]:
        return HealthyData[T](**self.data_params)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        is_array: bool = kwargs.get("is_array", self.DEFAULT_IS_ARRAY)
        max_age = parse_duration(kwargs.get("max_age", self.DEFAULT_MAX_AGE))
        self.data_array_params = self.get_healthy_data_params(kwargs)
        self.data_params = {"max_age": max_age}
        if is_array:
            self.data = self.create_healthy_data_array()
        else:
            self.data = self.create_healthy_data()

    @property
    def ready(self) -> bool:
        """数据源就绪状态"""
        if not super().ready:
            return False
        return self.data.is_healthy

    @property
    def is_array(self) -> bool:
        """是否为数组类型数据"""
        return isinstance(self.data, HealthyDataArray)


class BaseExchangeClassDataIndicator(BaseDataIndicator[T]):
    """
    交易所类别数据源基类

    绑定到某个交易所类别实例。
    """
    __pickle_exclude__ = {*BaseDataIndicator.__pickle_exclude__, "exchange"}
    supported_scope = ExchangeClassScope

    @property
    def exchange_class(self) -> str:
        """交易所实例配置路径"""
        return self.scope.get_var("exchange_class")  # 确保变量存在

    @cached_property
    def exchange(self) -> 'BaseExchange':
        exchange_group = self.root.exchange_group
        exchange_path = next(iter(exchange_group.exchange_group[self.exchange_class]))
        return exchange_group.exchange_instances[exchange_path]


class BaseExchangeDataIndicator(BaseDataIndicator[T]):
    """
    交易所数据源基类

    绑定到某个交易所实例。
    """
    __pickle_exclude__ = {*BaseDataIndicator.__pickle_exclude__, "exchange"}
    supported_scope = ExchangeScope

    @property
    def exchange_path(self) -> str:
        """交易所实例配置路径"""
        return self.scope.get_var("exchange_path")  # 确保变量存在

    @cached_property
    def exchange(self) -> 'BaseExchange':
        exchange_group = self.root.exchange_group
        return exchange_group.exchange_instances[self.exchange_path]


class BaseTradingPairClassDataIndicator(BaseDataIndicator[T]):
    """
    交易对数据源基类

    绑定到某个交易所实例和交易对。
    """
    __pickle_exclude__ = {*BaseDataIndicator.__pickle_exclude__, "exchange"}
    supported_scope = TradingPairClassScope

    @property
    def exchange_class(self) -> str:
        """交易所实例配置路径"""
        return self.scope.get_var("exchange_class")  # 确保变量存在

    @property
    def symbol(self) -> str:
        """交易所实例 Scope"""
        scope = self.scope
        return scope.get_var("symbol")

    @cached_property
    def exchange(self) -> 'BaseExchange':
        exchange_group = self.root.exchange_group
        exchange_path = next(iter(exchange_group.exchange_group[self.exchange_class]))
        return exchange_group.exchange_instances[exchange_path]


class BaseTradingPairDataIndicator(BaseDataIndicator[T]):
    """
    交易对数据源基类

    绑定到某个交易所实例和交易对。
    """
    __pickle_exclude__ = {*BaseDataIndicator.__pickle_exclude__, "exchange"}
    supported_scope = TradingPairScope

    @property
    def exchange_path(self) -> str:
        """交易所实例配置路径"""
        return self.scope.get_var("exchange_path")  # 确保变量存在

    @property
    def symbol(self) -> str:
        """交易所实例 Scope"""
        scope = self.scope
        return scope.get_var("symbol")

    @cached_property
    def exchange(self) -> 'BaseExchange':
        exchange_group = self.root.exchange_group
        return exchange_group.exchange_instances[self.exchange_path]
