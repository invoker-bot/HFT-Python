"""
DataSourceGroup - 数据源管理器

统一管理各类市场数据，复用现有的 BaseDataSource 子类。

模块组成：
- DataType: 数据类型枚举（ticker, orderbook, trades, ohlcv）
- DataArray: 时序数据数组，支持健康检查和自动过期
- DataSourceGroup: 数据源管理器，自动创建/清理 DataSource 实例

设计理念：
- 策略通过 query() 方法获取数据源，不需要关心 DataSource 的创建和生命周期
- DataSourceGroup 自动管理 DataSource 的 watch/unwatch
- 长时间未访问的 DataSource 会自动清理，节省资源

使用示例：
    # 在策略中获取数据源
    ds = datasource_group.query_single(DataType.TICKER, "okx", "BTC/USDT:USDT")
    if ds:
        ticker = ds.get_latest()

    # 批量获取多个交易对
    sources = datasource_group.query(
        DataType.OHLCV,
        "binance",
        ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    )
"""
import time
import asyncio
from enum import Enum
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Generic, TypeVar, Type, TYPE_CHECKING
from ..core.listener import Listener, ListenerState

if TYPE_CHECKING:
    from ..exchange.group import ExchangeGroup
    from ..exchange.base import BaseExchange
    from .base import BaseDataSource


T = TypeVar('T')  # 数据元素类型


class DataType(Enum):
    """
    数据类型枚举

    定义支持的市场数据类型，每种类型对应一个 BaseDataSource 子类：
    - TICKER: 最新价格信息 -> TickerDataSource
    - ORDER_BOOK: 订单簿深度 -> OrderBookDataSource
    - TRADES: 成交记录 -> TradesDataSource
    - OHLCV: K线数据 -> OHLCVDataSource
    """
    TICKER = "ticker"
    ORDER_BOOK = "order_book"
    TRADES = "trades"
    OHLCV = "ohlcv"


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


class DataSourceGroup(Listener):
    """
    数据源管理器

    复用现有的 BaseDataSource 子类（TickerDataSource, TradesDataSource 等）

    Features:
    - 自动创建和管理 DataSource 实例
    - query 时自动触发 request_watch()
    - 长时间无 query 自动清理 DataSource
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_datasources")

    def __init__(self, auto_cleanup_timeout: float = 300.0):
        super().__init__("DataSourceGroup", interval=10.0)
        self._auto_cleanup_timeout = auto_cleanup_timeout

        # DataSource 实例: (DataType, class_name, symbol) -> BaseDataSource
        self._datasources: dict[tuple[DataType, str, str], "BaseDataSource"] = {}

        # 最后访问时间: key -> timestamp
        self._last_access: dict[tuple[DataType, str, str], float] = {}

    def initialize(self):
        super().initialize()
        self._datasources = {}
        self._last_access = {}

    # ===== 属性 =====

    @property
    def exchange_group(self) -> "ExchangeGroup":
        """获取 ExchangeGroup（从 root 获取）"""
        return self.root.exchange_group

    # ===== DataSource 类映射 =====

    def _get_datasource_class(self, data_type: DataType) -> Type["BaseDataSource"]:
        """获取 DataType 对应的 DataSource 类"""
        from .ticker import TickerDataSource
        from .trades import TradesDataSource
        from .ohlcv import OHLCVDataSource
        from .orderbook import OrderBookDataSource

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

    # ===== 核心方法 =====

    def get_datasource(
        self,
        data_type: DataType,
        class_name: str,
        symbol: str
    ) -> Optional["BaseDataSource"]:
        """
        获取或创建 DataSource 实例

        Args:
            data_type: 数据类型
            class_name: 交易所类型
            symbol: 交易对

        Returns:
            DataSource 实例，或 None（如果无法创建）
        """
        key = (data_type, class_name, symbol)

        # 已存在
        if key in self._datasources:
            self._last_access[key] = time.time()
            ds = self._datasources[key]
            ds.request_watch()  # 刷新 watch 计时器
            return ds

        # 检查交易所是否存在
        exchange = self.exchange_group.get_exchange_by_class(class_name)
        if exchange is None:
            self.logger.warning("No exchange found for class %s", class_name)
            return None

        try:
            ds_class = self._get_datasource_class(data_type)
            ds = ds_class(exchange=exchange, symbol=symbol)

            # 添加为子 Listener
            self.add_child(ds)

            # 如果正在运行，启动它
            if self.state in (ListenerState.STARTING, ListenerState.RUNNING):
                asyncio.create_task(ds.start())

            self._datasources[key] = ds
            self._last_access[key] = time.time()

            self.logger.info("Created datasource: %s/%s/%s", data_type.value, class_name, symbol)
            return ds

        except Exception as e:
            self.logger.exception("Failed to create datasource: %s", e)
            return None

    def query(
        self,
        data_type: DataType,
        class_name: str,
        symbols: list[str],
    ) -> dict[str, "BaseDataSource"]:
        """
        查询数据源（自动创建和激活）

        Args:
            data_type: 数据类型
            class_name: 交易所类型
            symbols: 交易对列表

        Returns:
            {symbol: DataSource} 字典
        """
        result = {}
        for symbol in symbols:
            ds = self.get_datasource(data_type, class_name, symbol)
            if ds is not None:
                result[symbol] = ds
        return result

    def query_single(
        self,
        data_type: DataType,
        class_name: str,
        symbol: str,
    ) -> Optional["BaseDataSource"]:
        """查询单个交易对的数据源"""
        return self.get_datasource(data_type, class_name, symbol)

    # ===== 清理 =====

    async def _cleanup_stale_datasources(self) -> int:
        """清理长时间未访问的 DataSource"""
        now = time.time()
        to_remove = []

        for key, last_time in list(self._last_access.items()):
            if now - last_time > self._auto_cleanup_timeout:
                to_remove.append(key)

        for key in to_remove:
            data_type, class_name, symbol = key
            await self._remove_datasource(key)
            self.logger.info(
                "Removed stale datasource: %s/%s/%s",
                data_type.value, class_name, symbol
            )

        return len(to_remove)

    async def _remove_datasource(self, key: tuple[DataType, str, str]) -> None:
        """移除 DataSource"""
        if key in self._datasources:
            ds = self._datasources[key]
            await ds.stop()
            self.remove_child(ds.name)
            del self._datasources[key]

        if key in self._last_access:
            del self._last_access[key]

    # ===== 生命周期 =====

    async def on_tick(self) -> bool:
        """定期清理 stale DataSource"""
        removed = await self._cleanup_stale_datasources()
        if removed > 0:
            self.logger.debug("Cleaned up %d stale datasources", removed)
        return False

    async def on_stop(self) -> None:
        """停止时清理所有 DataSource"""
        for key in list(self._datasources.keys()):
            await self._remove_datasource(key)
        await super().on_stop()

    # ===== 状态 =====

    @property
    def log_state_dict(self) -> dict:
        return {
            "datasources": len(self._datasources),
        }

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = defaultdict(int)
        for key in self._datasources:
            data_type, class_name, _ = key
            stats[f"{data_type.value}:{class_name}"] += 1
        return dict(stats)
