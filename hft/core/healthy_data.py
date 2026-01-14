"""
HealthyData - 健康数据封装

封装数据和健康检查逻辑，get 时自动检查健康状态。

核心概念：
- 数据有"年龄"：从上次更新到现在的时间
- 数据有"健康状态"：年龄小于 max_age 且数据非空
- 获取数据时自动检查健康状态，不健康时可抛出异常或返回 None

使用场景：
- 缓存交易所 API 数据（ticker, orderbook 等）
- 确保策略使用的数据是新鲜的
- 自动触发数据刷新

Example:
    >>> ticker = HealthyData[dict](max_age=5.0)
    >>> ticker.set({"last": 100.0, "bid": 99.9, "ask": 100.1})
    >>> ticker.get()  # 返回数据（如果不超过 5 秒）
    >>> # 5 秒后...
    >>> ticker.get()  # 抛出 UnhealthyDataError
"""
import asyncio
import time
from typing import TypeVar, Generic, Optional, Callable, Awaitable
from dataclasses import dataclass, field


T = TypeVar('T')  # 泛型类型参数，表示存储的数据类型


class UnhealthyDataError(Exception):
    """
    数据不健康异常

    当数据过期或不存在时抛出。
    调用方应捕获此异常并触发数据刷新。
    """


@dataclass
class HealthyData(Generic[T]):
    """
    健康数据封装

    特性：
    - 存放数据（可通过 watch 或 fetch 更新）
    - get 时自动检查健康状态
    - 不健康时触发回调或抛出异常

    Usage:
        ticker = HealthyData[Ticker](max_age=5.0)
        ticker.set(await exchange.fetch_ticker(symbol))

        # 获取时自动检查健康
        try:
            data = ticker.get()
        except UnhealthyDataError:
            # 触发重新获取
            ...
    """
    # === 配置参数 ===
    max_age: float = 10.0  # 数据最大有效年龄（秒），超过则视为不健康
    on_unhealthy: Optional[Callable[["HealthyData"], Awaitable[None]]] = field(
        default=None, repr=False
    )  # 数据不健康时的回调函数（可选）

    # === 内部状态（不应直接访问）===
    _data: Optional[T] = field(default=None, repr=False)  # 存储的数据
    _timestamp: float = 0.0  # 数据更新时间戳（Unix 时间）
    _update_count: int = 0  # 累计更新次数，用于统计
    _dirty: bool = False  # 数据是否被标记为脏（需要刷新）

    def set(self, data: T, timestamp: Optional[float] = None) -> None:
        """
        设置数据

        Args:
            data: 数据
            timestamp: 数据时间戳，默认使用当前时间
        """
        self._data = data
        self._timestamp = timestamp if timestamp is not None else time.time()
        self._update_count += 1
        self._dirty = False  # 设置新数据后清除 dirty 标记

    def mark_dirty(self) -> None:
        """
        标记数据为脏（需要刷新）

        典型场景：下单后仓位数据需要刷新
        """
        self._dirty = True

    @property
    def is_dirty(self) -> bool:
        """数据是否被标记为脏"""
        return self._dirty

    def get(self, raise_on_unhealthy: bool = True) -> Optional[T]:
        """
        获取数据（检查健康状态）

        Args:
            raise_on_unhealthy: 不健康时是否抛出异常

        Returns:
            数据，或 None（如果不健康且不抛异常）

        Raises:
            UnhealthyDataError: 数据不健康且 raise_on_unhealthy=True
        """
        if not self.is_healthy:
            if raise_on_unhealthy:
                raise UnhealthyDataError(
                    f"Data unhealthy: age={self.age:.1f}s > max_age={self.max_age}s"
                )
            return None
        return self._data

    def get_unchecked(self) -> Optional[T]:
        """获取数据（不检查健康状态）"""
        return self._data

    @property
    def is_healthy(self) -> bool:
        """检查数据是否健康（非空、未过期、未标记为脏）"""
        if self._data is None:
            return False
        if self._dirty:
            return False
        return self.age <= self.max_age

    @property
    def is_stale(self) -> bool:
        """数据是否过期"""
        return not self.is_healthy

    @property
    def age(self) -> float:
        """数据年龄（秒）"""
        if self._timestamp == 0:
            return float('inf')
        return time.time() - self._timestamp

    @property
    def timestamp(self) -> float:
        """数据时间戳"""
        return self._timestamp

    @property
    def has_data(self) -> bool:
        """是否有数据"""
        return self._data is not None

    @property
    def update_count(self) -> int:
        """更新次数"""
        return self._update_count

    def clear(self) -> None:
        """清除数据"""
        self._data = None
        self._timestamp = 0.0
        self._dirty = False

    def __bool__(self) -> bool:
        """bool 转换：有数据且健康"""
        return self.is_healthy


@dataclass
class HealthyDataWithFallback(HealthyData[T]):
    """
    带 fallback 的健康数据

    扩展 HealthyData，在数据不健康时自动调用 fetch_func 获取新数据。
    适用于需要自动刷新的缓存场景。

    Example:
        >>> async def fetch_ticker():
        ...     return await exchange.fetch_ticker("BTC/USDT")
        >>> ticker = HealthyDataWithFallback(max_age=5.0, fetch_func=fetch_ticker)
        >>> data = await ticker.get_or_fetch()  # 自动获取或刷新

    注意：
        - fetch_func 必须是异步函数
        - 如果 fetch 失败，会抛出 UnhealthyDataError
        - get_or_fetch 使用锁防止并发重复 fetch
    """
    # 异步获取数据的函数，当数据不健康时自动调用
    fetch_func: Optional[Callable[[], Awaitable[T]]] = field(default=None, repr=False)
    # 防止并发 fetch 的锁（运行期资源，不参与 pickle）
    _fetch_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    async def get_or_fetch(self) -> T:
        """
        获取数据，不健康时自动 fetch

        使用双重检查锁定模式防止并发 fetch，避免重复 API 调用。

        Returns:
            数据

        Raises:
            UnhealthyDataError: fetch 后仍然不健康
        """
        # 快速路径：数据健康时直接返回
        if self.is_healthy:
            return self._data

        # 慢速路径：需要 fetch，加锁保护
        async with self._fetch_lock:
            # 双重检查：锁内再次检查，可能其他协程已经 fetch 完成
            if self.is_healthy:
                return self._data

            if self.fetch_func is not None:
                try:
                    data = await self.fetch_func()
                    self.set(data)
                    return data
                except Exception as e:
                    raise UnhealthyDataError(f"Fetch failed: {e}") from e

            raise UnhealthyDataError("Data unhealthy and no fetch_func provided")

    async def ensure_healthy(self) -> bool:
        """
        确保数据健康（必要时 fetch）

        Returns:
            是否健康
        """
        if self.is_healthy:
            return True

        async with self._fetch_lock:
            if self.is_healthy:
                return True

            if self.fetch_func is not None:
                try:
                    data = await self.fetch_func()
                    self.set(data)
                    return True
                except Exception:
                    return False

            return False
