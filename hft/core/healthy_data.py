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
import bisect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar

import numpy as np

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
        # if self._timestamp == 0:
        #     return float('inf')
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


def _default_is_duplicate(_x: Any, _y: Any) -> bool:
    """默认去重函数：同时间戳视为重复（可 pickle）"""
    return True


def _never_duplicate(_x: Any, _y: Any) -> bool:
    """永不去重函数：用于事件类数据如 Trades（可 pickle）"""
    return False


class HealthyDataArray(Generic[T]):
    """
    带时间戳的健康数据数组

    存储格式: [(timestamp, value), ...]
    按时间戳升序排序，支持中间插入，自动去重，基于时间窗口自动清理

    健康判断基于三个指标：
    - timeout: 当前时间与最新数据的时间差（越小越好）
    - cv: 采样间隔的变异系数（越小表示采样越均匀）
    - range: 实际覆盖时间 / 期望窗口时间（越大表示覆盖越完整）
    """

    def __init__(
        self,
        max_seconds: float,
        duplicate_tolerance: float = 1e-6,
        is_duplicate_fn: Callable[[T, T], bool] | None = None,
    ):
        """
        Args:
            max_seconds: 最大保留时间窗口（秒），超出时自动清理旧数据
            duplicate_tolerance: 时间戳去重容差（秒）
            is_duplicate_fn: 判断两个值是否重复的函数，默认同时间戳视为重复
        """
        self._max_seconds = max_seconds
        self._duplicate_tolerance = duplicate_tolerance
        self._is_duplicate_fn = (
            is_duplicate_fn if is_duplicate_fn is not None else _default_is_duplicate
        )
        self._data: list[tuple[float, T]] = []

    def append(self, timestamp: float, value: T) -> None:
        """
        添加数据，按时间戳排序插入，自动去重和清理

        去重行为：当 timestamp 在容差范围内且 is_duplicate_fn 返回 True 时，
        用新值覆盖旧值（适用于 snapshot 类数据如 Ticker/OrderBook）。

        Args:
            timestamp: 时间戳
            value: 数据值
        """
        # 从后往前找插入位置，同时检查重复
        pos = len(self._data)
        for i in range(len(self._data) - 1, -1, -1):
            if abs(self._data[i][0] - timestamp) < self._duplicate_tolerance:
                if self._is_duplicate_fn(self._data[i][1], value):
                    # 覆盖旧值（而非丢弃新值）
                    self._data[i] = (timestamp, value)
                    return
            elif self._data[i][0] > timestamp:
                pos = i  # 保持升序
            else:
                break

        self._data.insert(pos, (timestamp, value))
        # 使用数组中最新的时间戳来清理，而不是插入的时间戳
        # 这样即使插入历史数据，也能正确清理超窗的旧数据
        if self._data:
            latest_ts = self._data[-1][0]
            self._shrink(latest_ts - self._max_seconds)

    def _shrink(self, before_timestamp: float) -> None:
        """清理指定时间戳之前的数据"""
        start = bisect.bisect_left(self._data, before_timestamp, key=lambda x: x[0])
        if start > 0:
            self._data = self._data[start:]

    def assign(self, points: list[tuple[float, T]]) -> None:
        """
        批量替换数据（权威快照优化）

        将内部数据直接替换为该快照，适用于上游一次性返回完整权威数据的场景
        （如 OHLCV fetch 返回最近 N 根 candle）。

        处理流程：
        1. 按 timestamp 排序
        2. 同 timestamp（duplicate_tolerance 内）按 is_duplicate_fn 做 replace 归并
        3. 按 max_seconds 做 shrink（仅保留窗口内数据）

        使用约束：
        - 仅用于"权威快照"：该批数据必须覆盖当前窗口内的全部有效点
        - 与 watch 并行时需避免竞态：要么串行化写入，要么 assign 后立即用最新点 upsert

        Args:
            points: [(timestamp, value), ...] 数据点列表
        """
        if not points:
            self._data = []
            return

        # 1. 按 timestamp 排序
        sorted_points = sorted(points, key=lambda x: x[0])

        # 2. 去重归并（同 timestamp 保留最后一个或按 is_duplicate_fn 合并）
        merged: list[tuple[float, T]] = []
        for ts, val in sorted_points:
            if merged and abs(merged[-1][0] - ts) < self._duplicate_tolerance:
                if self._is_duplicate_fn(merged[-1][1], val):
                    # 覆盖旧值
                    merged[-1] = (ts, val)
                else:
                    merged.append((ts, val))
            else:
                merged.append((ts, val))

        # 3. 替换内部数据
        self._data = merged

        # 4. 按 max_seconds 做 shrink
        if self._data:
            latest_ts = self._data[-1][0]
            self._shrink(latest_ts - self._max_seconds)

    @property
    def latest(self) -> Optional[T]:
        """最新值"""
        return self._data[-1][1] if self._data else None

    @property
    def latest_timestamp(self) -> float:
        """最新时间戳，无数据返回 0.0"""
        return self._data[-1][0] if self._data else 0.0

    @property
    def timeout(self) -> float:
        """数据超时时间（秒）：当前时间与最新数据的时间差"""
        if not self._data:
            return float('inf')
        # 使用 max(0.0, ...) 防止交易所时间超前本机导致负值
        return max(0.0, time.time() - self._data[-1][0])

    def get_cv(
        self,
        start_timestamp: float,
        end_timestamp: float,
        min_points: int = 3,
    ) -> float:
        """
        计算指定时间范围内的采样间隔变异系数

        Args:
            start_timestamp: 起始时间戳
            end_timestamp: 结束时间戳
            min_points: 最少数据点数（需要 min_points 个点来计算 min_points-1 个间隔）

        Returns:
            变异系数，数据不足返回 100.0（极不健康）
        """
        start_pos = bisect.bisect_left(self._data, start_timestamp, key=lambda x: x[0])
        end_pos = bisect.bisect_right(self._data, end_timestamp, key=lambda x: x[0])

        # 需要至少 min_points 个点
        if len(self._data) == 0 or end_pos - start_pos < min_points:
            return 100.0

        times = np.array(
            [self._data[i][0] for i in range(start_pos, end_pos)],
            dtype=float,
        )
        dtimes = np.diff(times)

        if len(dtimes) < 2:
            return 100.0

        m = abs(dtimes.mean())
        if m < 1e-8:
            return 100.0

        return float(dtimes.std() / m)

    def get_range(
        self,
        start_timestamp: float,
        end_timestamp: float,
        min_points: int = 3,
    ) -> float:
        """
        计算指定时间范围内的数据覆盖比例

        Args:
            start_timestamp: 起始时间戳
            end_timestamp: 结束时间戳
            min_points: 最少数据点数

        Returns:
            覆盖比例（实际覆盖 / 期望覆盖），数据不足返回 0.0（极不健康）
        """
        if len(self._data) == 0:
            return 0.0

        # bisect_left: 第一个 >= start_timestamp 的位置
        start_pos = bisect.bisect_left(self._data, start_timestamp, key=lambda x: x[0])
        # bisect_right: 第一个 > end_timestamp 的位置（exclusive）
        end_pos = bisect.bisect_right(self._data, end_timestamp, key=lambda x: x[0])

        # end_pos 是 exclusive，所以窗口内的点数是 end_pos - start_pos
        num_points = end_pos - start_pos
        if num_points < min_points:
            return 0.0

        # 最后一个点的索引是 end_pos - 1
        last_idx = end_pos - 1
        actual_range = abs(self._data[last_idx][0] - self._data[start_pos][0])
        expected_range = abs(end_timestamp - start_timestamp)

        if expected_range < 1e-8:
            return 0.0

        return actual_range / expected_range

    def is_healthy(
        self,
        start_timestamp: float,
        end_timestamp: float,
        timeout_threshold: float = 60,
        cv_threshold: float = 0.8,
        range_threshold: float = 0.6,
        min_points: int = 3,
    ) -> bool:
        """
        判断指定时间范围内的数据是否健康

        三个条件同时满足：
        1. timeout < timeout_threshold: 数据足够新鲜
        2. cv < cv_threshold: 采样足够均匀
        3. range > range_threshold: 覆盖时间足够长
        """
        if self.timeout >= timeout_threshold:
            return False

        cv = self.get_cv(start_timestamp, end_timestamp, min_points)
        if cv >= cv_threshold:
            return False

        range_val = self.get_range(start_timestamp, end_timestamp, min_points)
        if range_val <= range_threshold:
            return False

        return True

    def clear(self) -> None:
        """清空数据"""
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return len(self._data) > 0

    def __getitem__(self, index: int) -> T:
        """索引访问，返回值（不含时间戳）"""
        return self._data[index][1]

    def __iter__(self):
        """迭代返回值（不含时间戳）"""
        # Keep it tuple-unpacking (instead of subscript) to avoid rare astroid/pylint crashes.
        return (value for _, value in self._data)

    def items(self):
        """迭代返回 (timestamp, value) 元组"""
        return iter(self._data)
