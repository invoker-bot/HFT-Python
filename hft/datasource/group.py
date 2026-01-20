"""
DataSourceGroup - 数据源管理器

三层架构：
- DataSourceGroup: 顶层管理器，从 load_markets() 同步所有交易对
- TradingPairDataSource: 中间层，代表 (exchange_class, symbol) 对，持久存在
- BaseDataSource: 底层数据源（ticker, orderbook 等），按需创建，自动销毁

.. deprecated::
    此模块已被 hft.indicator.group.IndicatorGroup 替代。
    新代码请使用 hft.indicator.group 下的类。
    将在 Phase 3 清理时移除。

设计理念：
- TradingPairDataSource 持久存在，可存储元数据
- 底层 DataSource 按需创建（query 时），无访问时自动 unwatch 并销毁
- 资源浪费在 watch 操作，所以只管理 watch 层的生命周期

使用示例：
    # 获取 ticker 数据
    ticker_ds = datasource_group.query("okx", "BTC/USDT:USDT", DataType.TICKER)
    if ticker_ds:
        data = ticker_ds.get_latest()

    # 批量获取
    sources = datasource_group.query_many("okx", ["BTC/USDT:USDT", "ETH/USDT:USDT"], DataType.TICKER)
"""
# pylint: disable=import-outside-toplevel
import time
import asyncio
from enum import Enum
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional, Generic, TypeVar, Type, TYPE_CHECKING
from ..core.listener import Listener, GroupListener

if TYPE_CHECKING:
    from ..exchange.group import ExchangeGroup
    from ..exchange.base import BaseExchange
    from .base import BaseDataSource
    from .funding_rate_datasource import FundingRateDataSource
    from .funding_rate_fetcher import GlobalFundingRateFetcher
    from ..indicator.lazy_indicator import LazyIndicator


T = TypeVar('T')  # 数据元素类型


class DataType(Enum):
    """
    数据类型枚举

    .. deprecated::
        此枚举已废弃。新代码请直接使用字符串 ID（如 "ticker", "trades"）。
        通过 IndicatorGroup.get_indicator(indicator_id, exchange_class, symbol) 获取数据源。

    定义支持的市场数据类型，每种类型对应一个 BaseDataSource 子类：
    - TICKER: 最新价格信息 -> TickerDataSource
    - ORDER_BOOK: 订单簿深度 -> OrderBookDataSource
    - TRADES: 成交记录 -> TradesDataSource
    - OHLCV: K线数据 -> OHLCVDataSource
    - FUNDING_RATE: 资金费率 -> FundingRateDataSource（由全局 fetcher 填充）
    """
    TICKER = "ticker"
    ORDER_BOOK = "order_book"
    TRADES = "trades"
    OHLCV = "ohlcv"
    FUNDING_RATE = "funding_rate"


class UnhealthyDataError(Exception):
    """
    数据不健康异常

    当数据不满足健康检查条件时抛出：
    - 数据过期（超过 freshness_threshold）
    - 数据量不足（低于 min_count）
    - 数据覆盖时长不足（低于 min_coverage）
    """
    pass


@dataclass
class DataArray(Generic[T]):
    """
    时序数据数组，支持自动过期清理和健康检查

    .. deprecated::
        此类已被 hft.core.healthy_data.HealthyDataArray 替代。
        新代码请使用 HealthyDataArray。

    这是一个增强版的 deque，专为时序市场数据设计：
    - 固定容量：超出自动淘汰最旧数据（FIFO）
    - 过期清理：可以清理超过 max_age 的数据
    - 健康检查：检查数据新鲜度、数量、时间覆盖

    健康检查维度：
    1. 新鲜度(freshness): 最后更新距今是否在阈值内
    2. 数据量(count): 是否有足够多的数据点
    3. 时间覆盖(coverage): 数据跨度是否足够长（用于计算指标）

    使用示例：
        >>> arr = DataArray[OHLCVData](maxlen=100, freshness_threshold=60.0)
        >>> arr.append(ohlcv_data)
        >>> if arr.check_healthy(require_fresh=True, min_count=20):
        ...     data = arr.get_latest(20)

    注意：
        假设数据元素有 timestamp 属性（Unix 毫秒或秒）
    """
    # === 配置参数 ===
    maxlen: int = 1000                  # 最大容量，超出后淘汰最旧数据
    max_age: float = 600.0              # 数据最大保留时间（秒），用于 cleanup
    freshness_threshold: float = 10.0  # 新鲜度阈值（秒），超过则 is_fresh=False

    # === 内部状态 ===
    _data: deque = field(default_factory=deque, repr=False)  # 底层存储
    last_update: float = 0.0  # 最后一次 append/extend 的时间
    last_access: float = 0.0  # 最后一次 get 操作的时间

    def __post_init__(self):
        self._data = deque(maxlen=self.maxlen)

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return len(self._data) > 0

    # ===== 数据操作 =====

    def append(self, item: T) -> None:
        """追加数据"""
        self._data.append(item)
        self.last_update = time.time()

    def extend(self, items: list[T]) -> None:
        """批量追加数据"""
        self._data.extend(items)
        self.last_update = time.time()

    def get_latest(self, n: int = 1) -> list[T]:
        """获取最新 n 条数据"""
        self.last_access = time.time()
        if n >= len(self._data):
            return list(self._data)
        return list(self._data)[-n:]

    def get_all(self) -> list[T]:
        """获取所有数据"""
        self.last_access = time.time()
        return list(self._data)

    def get_since(self, timestamp: float) -> list[T]:
        """获取指定时间戳之后的数据（假设数据有 timestamp 属性）"""
        self.last_access = time.time()
        result = []
        for item in self._data:
            item_ts = getattr(item, 'timestamp', None)
            if item_ts is None:
                continue
            if item_ts >= timestamp:
                result.append(item)
        return result

    def cleanup_expired(self) -> int:
        """清理过期数据，返回清理数量"""
        if not self._data:
            return 0

        now = time.time()
        cutoff = now - self.max_age
        count = 0

        while self._data:
            item = self._data[0]
            item_ts = getattr(item, 'timestamp', None)
            if item_ts is None or item_ts >= cutoff:
                break
            self._data.popleft()
            count += 1

        return count

    def clear(self) -> None:
        """清空数据"""
        self._data.clear()
        self.last_update = 0.0

    # ===== 健康检查 =====

    @property
    def is_fresh(self) -> bool:
        """数据是否新鲜（最后更新在阈值内）"""
        if self.last_update == 0:
            return False
        return (time.time() - self.last_update) <= self.freshness_threshold

    @property
    def age(self) -> float:
        """数据年龄（距离最后更新的秒数）"""
        if self.last_update == 0:
            return float('inf')
        return time.time() - self.last_update

    @property
    def coverage_duration(self) -> float:
        """
        数据覆盖时长（最早到最新数据的时间跨度）

        假设数据有 timestamp 属性
        """
        if len(self._data) < 2:
            return 0.0

        first_ts = getattr(self._data[0], 'timestamp', None)
        last_ts = getattr(self._data[-1], 'timestamp', None)

        if first_ts is None or last_ts is None:
            return 0.0

        return last_ts - first_ts

    def is_coverage_sufficient(self, min_duration: float) -> bool:
        """检查数据覆盖是否足够长"""
        return self.coverage_duration >= min_duration

    def check_healthy(
        self,
        require_fresh: bool = True,
        min_count: int = 0,
        min_coverage: float = 0.0,
        raise_on_unhealthy: bool = False
    ) -> bool:
        """
        综合健康检查

        Args:
            require_fresh: 是否要求数据新鲜
            min_count: 最少数据条数
            min_coverage: 最小覆盖时长（秒）
            raise_on_unhealthy: 不健康时是否抛出异常

        Returns:
            是否健康

        Raises:
            UnhealthyDataError: 不健康且 raise_on_unhealthy=True
        """
        reasons = []

        if require_fresh and not self.is_fresh:
            reasons.append(f"stale (age={self.age:.1f}s > {self.freshness_threshold}s)")

        if len(self._data) < min_count:
            reasons.append(f"insufficient count ({len(self._data)} < {min_count})")

        if min_coverage > 0 and not self.is_coverage_sufficient(min_coverage):
            reasons.append(f"insufficient coverage ({self.coverage_duration:.1f}s < {min_coverage}s)")

        if reasons:
            if raise_on_unhealthy:
                raise UnhealthyDataError(f"Data unhealthy: {', '.join(reasons)}")
            return False

        return True

    def get_healthy(
        self,
        n: int = 1,
        require_fresh: bool = True,
        min_count: int = 0,
        min_coverage: float = 0.0
    ) -> list[T]:
        """
        获取数据，同时检查健康状态

        Args:
            n: 获取最新 n 条
            require_fresh: 是否要求新鲜
            min_count: 最少数据条数
            min_coverage: 最小覆盖时长

        Returns:
            数据列表

        Raises:
            UnhealthyDataError: 数据不健康
        """
        self.check_healthy(
            require_fresh=require_fresh,
            min_count=max(min_count, n),
            min_coverage=min_coverage,
            raise_on_unhealthy=True
        )
        return self.get_latest(n)


class TradingPairDataSource(GroupListener):
    """
    交易对数据源 - 中间层

    代表一个 (exchange_class, symbol) 对，持久存在。
    管理各类型数据源（ticker, orderbook 等）的生命周期。

    特性：
    - lazy_start：初始为 STOPPED 状态
    - 持久存在，不会被 DataSourceGroup 删除
    - 底层 DataSource 按需创建（query 时），不删除只 stop()
    - 底层 DataSource 无访问时自动 stop()（保留缓存）
    """
    __pickle_exclude__ = (*GroupListener.__pickle_exclude__, "_exchange")

    # 延迟启动
    lazy_start: bool = True

    # 数据源自动休眠超时（秒）
    DEFAULT_AUTO_STOP_TIMEOUT: float = 300.0

    def __init__(
        self,
        exchange: "BaseExchange",
        symbol: str,
        auto_stop_timeout: float = DEFAULT_AUTO_STOP_TIMEOUT,
    ):
        name = f"{exchange.class_name}:{symbol}"
        super().__init__(name=name, interval=10.0)
        self._exchange = exchange
        self._exchange_class = exchange.class_name
        self._symbol = symbol
        self._auto_stop_timeout = auto_stop_timeout

        # 各数据类型的最后访问时间
        self._last_query_time: dict[DataType, float] = {}

        # 资金费率数据源（被动容器，由 GlobalFundingRateFetcher 填充）
        self._funding_rate_datasource: Optional["FundingRateDataSource"] = None

    @property
    def exchange(self) -> "BaseExchange":
        return self._exchange

    @property
    def exchange_class(self) -> str:
        return self._exchange_class

    @property
    def symbol(self) -> str:
        return self._symbol

    # ===== 资金费率数据源 =====

    @property
    def funding_rate_datasource(self) -> Optional["FundingRateDataSource"]:
        """
        获取资金费率数据源

        注意：这是一个被动容器，数据由 GlobalFundingRateFetcher 填充。
        如果需要确保存在，使用 ensure_funding_rate_datasource()。
        """
        return self._funding_rate_datasource

    def ensure_funding_rate_datasource(self) -> "FundingRateDataSource":
        """
        确保资金费率数据源存在

        如果不存在则创建。由 GlobalFundingRateFetcher 调用。

        Returns:
            FundingRateDataSource 实例
        """
        if self._funding_rate_datasource is None:
            from .funding_rate_datasource import FundingRateDataSource
            self._funding_rate_datasource = FundingRateDataSource(
                exchange_class=self._exchange_class,
                symbol=self._symbol,
            )
        return self._funding_rate_datasource

    # ===== DataSource 类映射 =====

    def _get_datasource_class(self, data_type: DataType) -> Type["BaseDataSource"]:
        """
        获取 DataType 对应的 DataSource 类

        注意：FUNDING_RATE 不支持此方法，应使用 funding_rate_datasource 属性。
        """
        from .ticker_datasource import TickerDataSource
        from .trades_datasource import TradesDataSource
        from .ohlcv_datasource import OHLCVDataSource
        from .orderbook_datasource import OrderBookDataSource

        if data_type == DataType.FUNDING_RATE:
            raise ValueError(
                "FUNDING_RATE is not a regular DataSource. "
                "Use trading_pair.funding_rate_datasource instead."
            )

        mapping = {
            DataType.TICKER: TickerDataSource,
            DataType.ORDER_BOOK: OrderBookDataSource,
            DataType.TRADES: TradesDataSource,
            DataType.OHLCV: OHLCVDataSource,
        }

        cls = mapping.get(data_type)
        if cls is None:
            raise ValueError(f"Unsupported DataType: {data_type}")
        return cls

    def _get_child_name(self, data_type: DataType) -> str:
        """获取数据源的 child name"""
        return data_type.value

    # ===== GroupListener 接口 =====

    def sync_children_params(self) -> dict[str, Any]:
        """
        返回所有已创建的 children（不删除，只管理 stop/start）
        """
        params = {}
        for data_type in self._last_query_time.keys():
            params[data_type.value] = {"data_type": data_type}
        return params

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        """创建数据源实例（lazy_start，初始为 STOPPED）"""
        data_type = param["data_type"]
        ds_class = self._get_datasource_class(data_type)
        ds = ds_class(exchange=self._exchange, symbol=self._symbol)
        # 不调用 request_watch()，因为 lazy_start 会保持 STOPPED
        return ds

    # ===== 查询接口 =====

    def query(self, data_type: DataType) -> Optional["BaseDataSource"]:
        """
        获取指定类型的数据源

        首次 query 时创建，后续 query 刷新访问时间。
        如果数据源已 stop()，会重新 start()。

        Args:
            data_type: 数据类型

        Returns:
            DataSource 实例
        """
        from ..core.listener import ListenerState

        # 更新访问时间
        self._last_query_time[data_type] = time.time()

        child_name = self._get_child_name(data_type)

        # 已存在
        if child_name in self.children:
            ds = self.children[child_name]
            ds.request_watch()  # 刷新 watch 计时器

            # 如果已 stop，重新 start
            if ds.state == ListenerState.STOPPED:
                asyncio.create_task(ds.start())
                self.logger.debug("Restarted datasource: %s/%s", self._symbol, data_type.value)

            return ds

        # 不存在，需要创建
        try:
            ds = self.create_dynamic_child(child_name, {"data_type": data_type})
            self.add_child(ds)

            # 立即启动（因为是 query 触发的）
            ds.request_watch()
            asyncio.create_task(ds.start())

            self.logger.debug("Created datasource: %s/%s", self._symbol, data_type.value)
            return ds

        except Exception as e:
            self.logger.exception("Failed to create datasource %s: %s", data_type.value, e)
            return None

    def has_active_datasource(self, data_type: DataType) -> bool:
        """检查是否有活跃的数据源（已创建且正在运行）"""
        from ..core.listener import ListenerState

        child_name = self._get_child_name(data_type)
        if child_name not in self.children:
            return False
        return self.children[child_name].state == ListenerState.RUNNING

    def has_datasource(self, data_type: DataType) -> bool:
        """检查是否有数据源（不管是否运行）"""
        child_name = self._get_child_name(data_type)
        return child_name in self.children

    # ===== 指标查询接口 =====

    def query_indicator(
        self,
        indicator_class: Type["LazyIndicator"],
        **kwargs
    ) -> Optional["LazyIndicator"]:
        """
        获取指定类型的指标

        首次 query 时创建并启动，后续 query 刷新访问时间。
        如果指标已 stop()，会重新 start()。

        Args:
            indicator_class: 指标类（如 VWAPIndicator）
            **kwargs: 传递给指标构造函数的参数

        Returns:
            指标实例

        Example:
            vwap = trading_pair.query_indicator(VWAPIndicator, window=200)
            value = vwap.get_value()
        """
        from ..core.listener import ListenerState

        indicator_name = indicator_class.__name__

        # 已存在
        if indicator_name in self.children:
            indicator = self.children[indicator_name]
            indicator.request_access()  # 刷新访问时间

            # 如果已 stop，重新 start
            if indicator.state == ListenerState.STOPPED:
                asyncio.create_task(indicator.start())
                self.logger.debug("Restarted indicator: %s", indicator_name)

            return indicator

        # 不存在，需要创建
        try:
            indicator = indicator_class(**kwargs)
            self.add_child(indicator)

            # 立即启动
            indicator.request_access()
            asyncio.create_task(indicator.start())

            self.logger.debug("Created indicator: %s", indicator_name)
            return indicator

        except Exception as e:
            self.logger.exception("Failed to create indicator %s: %s", indicator_name, e)
            return None

    def has_indicator(self, indicator_class: Type["LazyIndicator"]) -> bool:
        """检查是否有指标（不管是否运行）"""
        return indicator_class.__name__ in self.children

    def has_active_indicator(self, indicator_class: Type["LazyIndicator"]) -> bool:
        """检查是否有活跃的指标（已创建且正在运行）"""
        from ..core.listener import ListenerState

        indicator_name = indicator_class.__name__
        if indicator_name not in self.children:
            return False
        return self.children[indicator_name].state == ListenerState.RUNNING

    # ===== 生命周期 =====

    async def on_tick(self) -> bool:
        """定期检查并停止空闲的数据源（不删除）"""
        from ..core.listener import ListenerState

        now = time.time()

        # 检查每个数据源是否需要 stop
        for data_type, last_time in list(self._last_query_time.items()):
            child_name = self._get_child_name(data_type)
            if child_name not in self.children:
                continue

            ds = self.children[child_name]

            # 超时未访问且正在运行 -> stop
            if now - last_time > self._auto_stop_timeout:
                if ds.state == ListenerState.RUNNING:
                    await ds.stop()
                    self.logger.debug("Stopped idle datasource: %s/%s", self._symbol, data_type.value)

        return False

    @property
    def log_state_dict(self) -> dict:
        from ..core.listener import ListenerState
        from ..indicator.lazy_indicator import LazyIndicator

        active_datasources = []
        active_indicators = []

        for name, child in self.children.items():
            if child.state != ListenerState.RUNNING:
                continue
            if isinstance(child, LazyIndicator):
                active_indicators.append(name)
            else:
                active_datasources.append(name)

        return {
            "symbol": self._symbol,
            "total_children": len(self.children),
            "active_datasources": active_datasources,
            "active_indicators": active_indicators,
        }


class DataSourceGroup(GroupListener):
    """
    数据源管理器 - 顶层

    从 ExchangeGroup 的 load_markets() 同步所有交易对，
    为每个 (exchange_class, symbol) 创建 TradingPairDataSource。

    Features:
    - TradingPairDataSource 持久存在，不会被删除
    - 提供统一的 query 接口
    - 自动同步新增/删除的交易对
    - GlobalFundingRateFetcher 定时获取所有交易对的资金费率
    """
    __pickle_exclude__ = (*GroupListener.__pickle_exclude__, "_funding_rate_fetcher")

    def __init__(
        self,
        auto_destroy_timeout: float = 300.0,
        funding_rate_interval: float = 3.0,
    ):
        super().__init__("DataSourceGroup", interval=60.0)
        self._auto_destroy_timeout = auto_destroy_timeout
        self._funding_rate_interval = funding_rate_interval
        self._funding_rate_fetcher: Optional["GlobalFundingRateFetcher"] = None

    # ===== 属性 =====

    @property
    def exchange_group(self) -> "ExchangeGroup":
        """获取 ExchangeGroup（从 root 获取）"""
        return self.root.exchange_group

    @property
    def funding_rate_fetcher(self) -> Optional["GlobalFundingRateFetcher"]:
        """获取资金费率获取器"""
        return self._funding_rate_fetcher

    # ===== 生命周期 =====

    async def on_start(self) -> None:
        """启动时创建 GlobalFundingRateFetcher"""
        from .funding_rate_fetcher import GlobalFundingRateFetcher

        # 创建并添加资金费率获取器
        self._funding_rate_fetcher = GlobalFundingRateFetcher(
            interval=self._funding_rate_interval
        )
        self.add_child(self._funding_rate_fetcher)

    # ===== GroupListener 接口 =====

    def sync_children_params(self) -> dict[str, Any]:
        """
        从 ExchangeGroup 获取所有 (exchange_class, symbol) 对

        Returns:
            {child_name: {"exchange": exchange, "symbol": symbol}}
        """
        params = {}
        for exchange in self.exchange_group.children.values():
            if not exchange.ready:
                continue
            for symbol in exchange.market_trading_pairs.keys():
                child_name = f"{exchange.class_name}:{symbol}"
                params[child_name] = {
                    "exchange": exchange,
                    "symbol": symbol,
                }
        return params

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        """创建 TradingPairDataSource"""
        return TradingPairDataSource(
            exchange=param["exchange"],
            symbol=param["symbol"],
            auto_stop_timeout=self._auto_destroy_timeout,
        )

    # ===== 查询接口 =====

    def _get_trading_pair_source(
        self,
        exchange_class: str,
        symbol: str
    ) -> Optional[TradingPairDataSource]:
        """获取 TradingPairDataSource"""
        child_name = f"{exchange_class}:{symbol}"
        return self.children.get(child_name)

    def query(
        self,
        exchange_class: str,
        symbol: str,
        data_type: DataType
    ) -> Optional["BaseDataSource"]:
        """
        查询数据源

        Args:
            exchange_class: 交易所类名（如 "okx", "binance"）
            symbol: 交易对（如 "BTC/USDT:USDT"）
            data_type: 数据类型

        Returns:
            DataSource 实例，或 None（如果交易对不存在）
        """
        pair_source = self._get_trading_pair_source(exchange_class, symbol)
        if pair_source is None:
            self.logger.warning(
                "Trading pair not found: %s:%s",
                exchange_class, symbol
            )
            return None

        return pair_source.query(data_type)

    def query_many(
        self,
        exchange_class: str,
        symbols: list[str],
        data_type: DataType
    ) -> dict[str, "BaseDataSource"]:
        """
        批量查询数据源

        Args:
            exchange_class: 交易所类名
            symbols: 交易对列表
            data_type: 数据类型

        Returns:
            {symbol: DataSource} 字典
        """
        result = {}
        for symbol in symbols:
            ds = self.query(exchange_class, symbol, data_type)
            if ds is not None:
                result[symbol] = ds
        return result

    def get_trading_pair(
        self,
        exchange_class: str,
        symbol: str
    ) -> Optional[TradingPairDataSource]:
        """
        获取 TradingPairDataSource（不创建数据源）

        用于访问交易对的元数据，不触发数据源创建。
        """
        return self._get_trading_pair_source(exchange_class, symbol)

    def list_trading_pairs(self, exchange_class: Optional[str] = None) -> list[str]:
        """
        列出所有交易对

        Args:
            exchange_class: 可选，过滤指定交易所

        Returns:
            交易对列表（格式：exchange_class:symbol）
        """
        result = []
        for name in self.children.keys():
            if exchange_class is None or name.startswith(f"{exchange_class}:"):
                result.append(name)
        return result

    # ===== 生命周期 =====

    async def on_tick(self) -> bool:
        """定期同步交易对"""
        await self._sync_children()
        return False

    @property
    def log_state_dict(self) -> dict:
        # 统计各交易所的交易对数量
        stats = defaultdict(int)
        for name in self.children.keys():
            exchange_class = name.split(":")[0]
            stats[exchange_class] += 1

        return {
            "trading_pairs": len(self.children),
            "by_exchange": dict(stats),
        }

    def get_stats(self) -> dict:
        """获取详细统计信息"""
        from ..indicator.lazy_indicator import LazyIndicator
        from ..core.listener import ListenerState

        stats = {
            "total_pairs": len(self.children),
            "by_exchange": defaultdict(int),
            "active_datasources": defaultdict(int),
            "active_indicators": defaultdict(int),
        }

        for name, pair_source in self.children.items():
            exchange_class = name.split(":")[0]
            stats["by_exchange"][exchange_class] += 1

            for data_type in DataType:
                if pair_source.has_active_datasource(data_type):
                    stats["active_datasources"][data_type.value] += 1

            # 统计活跃指标
            for child_name, child in pair_source.children.items():
                if isinstance(child, LazyIndicator) and child.state == ListenerState.RUNNING:
                    stats["active_indicators"][child_name] += 1

        stats["by_exchange"] = dict(stats["by_exchange"])
        stats["active_datasources"] = dict(stats["active_datasources"])
        stats["active_indicators"] = dict(stats["active_indicators"])
        return stats
