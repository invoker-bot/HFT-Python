from typing import Generic, TypeVar
from functools import cached_property
from ...core.scope.scopes import ExchangeScope, TradingPairClassScope, TradingPairScope
from ...core.healthy_data import HealthyData, HealthyDataArray
from ...core.duration import parse_duration
from ...exchange.base import BaseExchange
from ..base import BaseIndicator

T = TypeVar('T')  # 数据类型

class BaseDataSource(Generic[T], BaseIndicator):
    """
    数据源基类

    从 exchange 获取数据的特殊 Indicator，支持 watch/fetch 两种模式。

    子类需要实现：
    - _watch(): WebSocket 订阅模式
    - _fetch(): REST API 轮询模式
    - calculate_vars(): 返回变量字典
    """
    DEFAULT_IS_ARRAY = True
    DEFAULT_MAX_AGE = 15.0 # 15 秒数据过期，有可能过松，或者过严
    DEFAULT_WINDOW = 600.0  # 10 分钟的数据窗口
    DEFAULT_HEALTHY_WINDOW = 60.0 # 默认1分钟健康窗口
    DEFAULT_DUPLICATE_TIMESTAMP_DELTA = 1e-6
    DEFAULT_HEALTHY_POINTS = 3  # 最少数据点数，越多越严格，采样是否充足
    DEFAULT_HEALTHY_CV = 0.8  # 最多变异系
    DEFAULT_HEALTHY_RANGE = 0.6  # 最少覆盖比例

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        is_array: bool = kwargs.get("is_array", self.DEFAULT_IS_ARRAY)
        max_age = parse_duration(kwargs.get("max_age", self.DEFAULT_MAX_AGE))
        window = parse_duration(kwargs.get("window", self.DEFAULT_WINDOW))
        duplicate_timestamp_delta = parse_duration(kwargs.get(
            "duplicate_timestamp_delta",
            self.DEFAULT_DUPLICATE_TIMESTAMP_DELTA
        ))
        healthy_points: int = kwargs.get("healthy_points", self.DEFAULT_HEALTHY_POINTS)  # 最少数据点数，越多越严格，采样是否充足
        healthy_cv: float = kwargs.get("healthy_cv", self.DEFAULT_HEALTHY_CV)  # 最多变异系数（倒数），越大越严格，采样间隔是否均匀
        healthy_range: float = kwargs.get("healthy_range", self.DEFAULT_HEALTHY_RANGE)
        healthy_window = parse_duration(kwargs.get("healthy_window", self.DEFAULT_HEALTHY_WINDOW))
        if is_array:
            self.data = HealthyDataArray[T](max_age=max_age, window=window,
                                         healthy_window=healthy_window,
                                         duplicate_timestamp_delta=duplicate_timestamp_delta,
                                         healthy_points=healthy_points,
                                         healthy_cv=healthy_cv,
                                         healthy_range=healthy_range)
        else:
            self.data = HealthyData[T](max_age=max_age)

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


class BaseExchangeDataSource(BaseDataSource[T]):
    """
    交易所数据源基类

    绑定到某个交易所实例。
    """
    __pickle_exclude__ = {*BaseDataSource.__pickle_exclude__, "exchange"}

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        assert isinstance(self.scope.scope, ExchangeScope)

    @property
    def exchange_path(self) -> str:
        """交易所实例配置路径"""
        return self.scope.get_var("exchange_path")  # 确保变量存在

    @cached_property
    def exchange(self) -> 'BaseExchange':
        exchange_group = self.root.exchange_group
        return exchange_group.exchange_instances[self.exchange_path]


class BaseTradingPairClassDataSource(BaseDataSource[T]):
    """
    交易对数据源基类

    绑定到某个交易所实例和交易对。
    """
    __pickle_exclude__ = {*BaseDataSource.__pickle_exclude__, "exchange"}

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        assert isinstance(self.scope.scope, TradingPairClassScope), \
            "TradingPairClassDataSource must be used within TradingPairClassScope"

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


class BaseTradingPairDataSource(BaseDataSource[T]):
    """
    交易对数据源基类

    绑定到某个交易所实例和交易对。
    """
    __pickle_exclude__ = {*BaseDataSource.__pickle_exclude__, "exchange"}

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        assert isinstance(self.scope.scope, TradingPairScope), \
            "TradingPairDataSource must be used within TradingPairScope"

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


