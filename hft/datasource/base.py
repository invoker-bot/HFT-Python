"""
DataSource 基类

自动开启/关闭的数据源，支持 watch + fallback fetch 模式
"""
import asyncio
import time
from abc import abstractmethod
from enum import Enum
from collections import deque
from typing import Optional, Any, Generic, TypeVar, TYPE_CHECKING
from pyee.asyncio import AsyncIOEventEmitter
from ..core.listener import Listener

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


T = TypeVar('T')  # 数据类型


class DataSourceState(Enum):
    """数据源状态"""
    IDLE = "idle"               # 空闲（未监控）
    WATCHING = "watching"       # 监控中
    FETCHING = "fetching"       # 正在获取数据


class BaseDataSource(Listener, Generic[T]):
    """
    数据源基类

    特性：
    1. lazy_start：初始为 STOPPED 状态，首次 query 时才启动
    2. 自动休眠：5分钟没有 query 就自动 stop()（保留缓存）
    3. watch + fallback fetch：优先使用 WebSocket，超时后 fallback 到 REST
    4. 数据去重：确保不收集重复数据
    5. 缓存管理：从前往后缓存，限制最大长度

    Events:
    - update(data): 新数据到达
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "event", "_exchange")

    # 延迟启动：初始为 STOPPED，首次 query 时才启动
    lazy_start: bool = True

    # 默认配置
    DEFAULT_WATCH_TIMEOUT: float = 5.0          # watch 超时时间（秒）
    DEFAULT_AUTO_UNWATCH_TIMEOUT: float = 300.0  # 自动 unwatch 超时（秒）
    DEFAULT_MAX_CACHE_SIZE: int = 1000           # 最大缓存大小

    def __init__(
        self,
        exchange: "BaseExchange",
        symbol: str,
        name: Optional[str] = None,
        watch_timeout: float = DEFAULT_WATCH_TIMEOUT,
        auto_unwatch_timeout: float = DEFAULT_AUTO_UNWATCH_TIMEOUT,
        max_cache_size: int = DEFAULT_MAX_CACHE_SIZE,
        interval: float = 0.1,
    ):
        if name is None:
            name = f"{self.__class__.__name__}:{symbol}"
        super().__init__(name=name, interval=interval)
        self._exchange = exchange
        self._exchange_class = exchange.class_name
        self._symbol = symbol
        self._watch_timeout = watch_timeout
        self._auto_unwatch_timeout = auto_unwatch_timeout
        self._max_cache_size = max_cache_size

        # 事件发射器
        self.event = AsyncIOEventEmitter()

        # 状态
        self._ds_state = DataSourceState.IDLE
        self._last_watch_request: float = 0.0   # 最后一次收到 watch 请求的时间
        self._last_data_time: float = 0.0       # 最后一次收到数据的时间

        # 缓存
        self._cache: deque[T] = deque(maxlen=max_cache_size)
        self._last_data_id: Optional[Any] = None  # 用于去重

    def initialize(self):
        super().initialize()
        self.event = AsyncIOEventEmitter()

    @property
    def exchange(self) -> "BaseExchange":
        """获取 exchange"""
        return self._exchange

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def ds_state(self) -> DataSourceState:
        return self._ds_state

    @property
    def is_watching(self) -> bool:
        return self._ds_state == DataSourceState.WATCHING

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def last_data_time(self) -> float:
        return self._last_data_time

    def request_watch(self) -> None:
        """请求监控（刷新 auto-unwatch 计时器）"""
        self._last_watch_request = time.time()

    def should_auto_unwatch(self) -> bool:
        """是否应该自动 unwatch"""
        if self._last_watch_request == 0:
            return False
        elapsed = time.time() - self._last_watch_request
        return elapsed > self._auto_unwatch_timeout

    # ========== 抽象方法 ==========

    @abstractmethod
    async def _watch(self) -> Optional[T]:
        """
        WebSocket 订阅获取数据

        子类实现，返回新数据或 None
        """
        ...

    @abstractmethod
    async def _fetch(self) -> Optional[T]:
        """
        REST API 获取数据

        子类实现，作为 watch 的 fallback
        """
        ...

    @abstractmethod
    def _get_data_id(self, data: T) -> Any:
        """
        获取数据的唯一标识，用于去重

        子类实现
        """
        ...

    @abstractmethod
    def _process_data(self, data: T) -> Optional[T]:
        """
        处理数据，返回处理后的数据或 None（丢弃）

        子类可覆盖
        """
        return data

    def _emit_plugin_hook(self, data: T) -> None:
        """
        触发 Plugin Hook（模板方法）

        子类可覆盖以触发特定的 Plugin Hook。
        在 _add_to_cache() 成功添加数据后调用。

        Args:
            data: 已处理的数据
        """
        pass

    # ========== 核心逻辑 ==========

    async def _watch_with_fallback(self) -> Optional[T]:
        """watch + fallback fetch"""
        try:
            self._ds_state = DataSourceState.WATCHING
            data = await asyncio.wait_for(
                self._watch(),
                timeout=self._watch_timeout
            )
            return data
        except asyncio.TimeoutError:
            self._ds_state = DataSourceState.FETCHING
            return await self._fetch()

    def _add_to_cache(self, data: T) -> bool:
        """
        添加数据到缓存（去重）

        Returns:
            是否成功添加（非重复数据）
        """
        data_id = self._get_data_id(data)
        if data_id == self._last_data_id:
            return False  # 重复数据

        processed = self._process_data(data)
        if processed is None:
            return False  # 被过滤

        self._cache.append(processed)
        self._last_data_id = data_id
        self._last_data_time = time.time()

        # 发出更新事件
        self.event.emit("update", processed)

        # 触发 Plugin Hook
        self._emit_plugin_hook(processed)

        return True

    async def on_tick(self) -> bool:
        """每 tick 获取数据"""
        # 检查是否应该自动 unwatch
        if self.should_auto_unwatch():
            self._ds_state = DataSourceState.IDLE
            self.logger.debug("Auto unwatch %s", self._symbol)
            return True  # 信号完成，停止 tick

        # 只有收到过 watch 请求才获取数据
        if self._last_watch_request == 0:
            return False  # 继续等待

        try:
            data = await self._watch_with_fallback()
            if data is not None:
                self._add_to_cache(data)
            return False  # 继续 tick
        except Exception as e:
            self.logger.warning("Watch error for %s: %s", self._symbol, e)
            return False  # 继续 tick，下次重试
        finally:
            if self._ds_state != DataSourceState.IDLE:
                self._ds_state = DataSourceState.WATCHING

    # ========== 数据访问 ==========

    def get_latest(self) -> Optional[T]:
        """获取最新数据"""
        if not self._cache:
            return None
        return self._cache[-1]

    def get_last_n(self, n: int) -> list[T]:
        """获取最后 N 条数据"""
        return list(self._cache)[-n:]

    def get_all(self) -> list[T]:
        """获取所有缓存数据"""
        return list(self._cache)

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._last_data_id = None

    async def fetch_initial(self, limit: int = 100) -> list[T]:
        """
        初始化时获取历史数据

        用于 OHLCV 等需要初始数据的场景
        """
        return []
