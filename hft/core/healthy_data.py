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
import time
import bisect
import random
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Generic, Optional, TypeVar, Union
import numpy as np
from .duration import parse_duration

T = TypeVar('T')  # 泛型类型参数，表示存储的数据类型


class UnhealthyDataError(Exception):
    """
    数据不健康异常

    当数据过期或不存在时抛出。
    调用方应捕获此异常并触发数据刷新。
    """


DataFunc = Callable[[], Awaitable[tuple[T, Optional[float]]]]


class BaseHealthyData(ABC, Generic[T]):

    __pickle_excludes__ = {'_data_lock', '_update_data_lock'}
    # is_array: bool = False  # 是否为数组类型数据

    def __init__(self, max_age: float = 10):
        self.max_age = max_age
        self._dirty: bool = False  # 数据脏标记
        self.initialize()

    def initialize(self):
        self._data_lock = asyncio.Lock()  # 保护数据更新的锁，防止并发更新冲突
        self._update_data_lock = asyncio.Lock()  # 减少多次更新数据

    def __getstate__(self):
        return {k: v for k, v in self.__dict__.items() if k not in self.__pickle_excludes__}

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.initialize()

    @property
    def data_lock(self) -> asyncio.Lock:
        """获取数据锁，用于上次获取数据的并发保护"""
        return self._data_lock

    @property
    @abstractmethod
    def is_healthy(self) -> bool:
        """检查当前数据是否健康"""

    def is_stale(self) -> bool:
        """数据是否过期"""
        return not self.is_healthy

    @property
    def age(self) -> float:
        """最近的数据年龄（秒）"""
        return time.time() - self.timestamp

    @property
    def data(self) -> Optional[T]:
        """获取最新数据（可能不健康）"""
        return self.get()[0]

    @property
    def timestamp(self) -> float:
        """最近数据的时间戳"""
        return self.get()[1]

    async def mark_dirty(self) -> None:
        """标记数据为脏"""
        async with self._data_lock:
            self._dirty = True

    @abstractmethod
    def get(self) -> tuple[Optional[T], float]:
        """获取最新数据"""
        # return self._data_tuple

    def get_data(self) -> Optional[T]:
        """获取最新数据（可能不健康）"""
        data, _ = self.get()
        return data

    async def get_or_raise(self) -> tuple[T, float]:
        async with self._data_lock:
            if not self.is_healthy:
                raise UnhealthyDataError("Data is unhealthy")
            return self.get()  # data is not None now

    async def get_data_or_raise(self) -> T:
        return (await self.get_or_raise())[0]

    @abstractmethod
    async def update(self, data: T, timestamp: Optional[float] = None) -> None:
        """更新数据"""

    async def update_by_func(self, update_func: DataFunc):
        async with self._update_data_lock:
            data, timestamp = await asyncio.wait_for(update_func(), timeout=self.max_age)
        await self.update(data, timestamp)

    async def get_or_update_by_func(self, update_func: DataFunc) -> tuple[T, float]:
        """
        获取数据，不健康时自动更新

        Args:
            update_func: 异步数据获取函数，返回 (data, timestamp)

        Returns:
            数据

        Raises:
            UnhealthyDataError: 更新后仍然不健康
        """
        try:
            return await self.get_or_raise()  # 快速路径：数据健康时直接返回
        except UnhealthyDataError:
            async with self._update_data_lock:
                try:
                    return await self.get_or_raise()  # 双重检查：锁内再次检查，可能其他协程已经更新完成
                except UnhealthyDataError:
                    try:
                        data, timestamp = await asyncio.wait_for(update_func(), timeout=self.max_age)
                        await self.update(data, timestamp)
                    except Exception as e:
                        raise UnhealthyDataError(f"Update failed: {e}") from e
                    return await self.get_or_raise()  # 更新后再次看是否健康

    async def get_data_or_update_by_func(self, update_func: DataFunc) -> T:
        return (await self.get_or_update_by_func(update_func))[0]

    async def ensure_update(self, update_func: DataFunc, active: bool = True) -> bool:
        """
        确保数据健康（必要时 fetch）
        Args:
            update_func: 异步数据获取函数，返回 (data, timestamp)
            active: 是否主动更新（True时为watch方式，False时为fetch方式）

        Returns:
            是否成功更新
        """
        try:
            if active:
                await self.update_by_func(update_func)
            else:
                await self.get_or_update_by_func(update_func)
            return True
        except UnhealthyDataError:
            return False

    def __bool__(self) -> bool:
        return self.is_healthy


class HealthyData(BaseHealthyData[T]):
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
    def __init__(self, max_age: float = 10.0):
        super().__init__(max_age=max_age)
        self._data_tuple: tuple[Optional[T], float] = (None, 0.0)  # 存储的数据和时间戳

    @property
    def is_healthy(self) -> bool:
        """检查当前数据是否健康"""
        data, timestamp = self._data_tuple
        if data is None:
            return False
        if self._dirty:
            return False
        return (time.time() - timestamp) <= self.max_age

    def get(self) -> tuple[Optional[T], float]:  # 获取最新的一个数据点
        return self._data_tuple

    async def update(self, data: T, timestamp: Optional[float] = None) -> None:
        """
        设置数据

        Args:
            data: 数据
            timestamp: 数据时间戳，默认使用当前时间
        """
        if timestamp is None:
            timestamp = time.time()
        async with self._data_lock:
            self._data_tuple = (data, timestamp)
            self._dirty = False


def always_duplicate(_x: Any, _y: Any) -> bool:
    """默认去重函数：同时间戳视为重复（可 pickle）"""
    return True


def never_duplicate(_x: Any, _y: Any) -> bool:
    """永不去重函数：用于事件类数据如 Trades（可 pickle）"""
    return False


class HealthyDataArray(BaseHealthyData[T]):
    """
    带时间戳的健康数据数组

    存储格式: [(value, timestamp), ...]
    按时间戳升序排序，支持中间插入，自动去重，基于时间窗口自动清理

    健康判断基于三个指标：
    - timeout: 当前时间与最新数据的时间差（越小越好）
    - cv: 采样间隔的变异系数（越小表示采样越均匀）
    - range: 实际覆盖时间 / 期望窗口时间（越大表示覆盖越完整）
    """

    def __init__(
        self,
        max_age: float,
        window: Union[str, int, float, None],
        healthy_window: Union[str, int, float, None] = None,
        duplicate_timestamp_delta: float = 1e-6,
        healthy_points: int = 3,  # 最少数据点数，越多越严格，采样是否充足
        healthy_cv: float = 0.5,  # 最多变异系数（倒数），越大越严格，采样间隔是否均匀
        healthy_range: float = 0.25,  # 最少覆盖面积，越大越严格，覆盖时间是否完整
    ):
        """
        Args:
            window: 最大保留时间窗口（秒），超出时自动清理旧数据
            duplicate_timestamp_delta: 时间戳去重容差（秒）
            duplicate_value_fn: 判断两个值是否重复的函数，默认同时间戳视为重复
        """
        super().__init__(max_age=max_age)
        self.window = parse_duration(window)
        self.healthy_window = parse_duration(healthy_window) if healthy_window is not None else self.window
        self._data_list: list[tuple[T, float]] = []
        self._duplicate_timestamp_delta = duplicate_timestamp_delta
        self._healthy_points = healthy_points
        self._healthy_cv = healthy_cv
        self._healthy_range = healthy_range
        self._random_rate = random.random() # [0, 1] 用于打散shrink调用时间，避免多实例同时触发

    @property
    def data_list(self) -> list[tuple[T, float]]:
        """获取内部数据（value, timestamp）"""
        return self._data_list

    async def append(self, value: T, timestamp: Optional[float] = None,
                     duplicate_value_fn=always_duplicate) -> None:
        """
        添加数据，按时间戳排序插入，自动去重和清理

        去重行为：当 timestamp 在容差范围内且 duplicate_value_fn 返回 True 时，
        用新值覆盖旧值（适用于 snapshot 类数据如 Ticker/OrderBook）。
        Args:
            value: 数据值
            timestamp: 时间戳
        """
        if timestamp is None:
            timestamp = time.time()
        # 从后往前找插入位置，同时检查重复
        async with self._data_lock:
            pos = len(self._data_list)
            for i in range(len(self._data_list) - 1, -1, -1):
                delta_time = timestamp - self._data_list[i][1]
                if abs(delta_time) < self._duplicate_timestamp_delta:
                    if duplicate_value_fn(self._data_list[i][0], value):
                        # 覆盖旧值
                        self._data_list[i] = (value, timestamp)
                        return
                if self._data_list[i][1] < timestamp:
                    pos = i + 1  # 保持升序
                    break
                else:
                    pos = i
            self._data_list.insert(pos, (value, timestamp))
            self._dirty = False
            self._shrink()

    def _shrink(self) -> None:
        """清理过期数据（摊还 O(1)：时间跨度超过 (2+random)*window 时一次性截断，随机抖动避免多实例同时触发）"""
        if len(self._data_list) < 2:
            return
        time_span = self._data_list[-1][1] - self._data_list[0][1]
        if time_span > (2 + self._random_rate) * self.window:
            cut = bisect.bisect_left(self._data_list, time.time() - self.window, key=lambda x: x[1])
            if cut > 0:
                self._data_list = self._data_list[cut:]

    async def assign(self, points: list[tuple[T, float]]):
        """
        批量替换数据（权威快照优化）
        """
        async with self._data_lock:
            self._data_list = points

    def is_cv_healthy(
        self,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
    ) -> bool:
        """
        计算指定时间范围内的采样间隔变异系数

        Args:
            start_timestamp: 起始时间戳
            end_timestamp: 结束时间戳
        """
        start_pos = bisect.bisect_left(self._data_list, start_timestamp, key=lambda x: x[1]) if start_timestamp is not None else 0
        end_pos = bisect.bisect_right(self._data_list, end_timestamp, key=lambda x: x[1]) if end_timestamp is not None else len(self._data_list)

        if end_pos - start_pos < self._healthy_points:
            return False

        times = np.array(
            [self._data_list[i][1] for i in range(start_pos, end_pos)],
            dtype=float,
        )
        dtimes = np.diff(times)

        if len(dtimes) < 2:  # 变异系数需要至少两个间隔
            return False

        return self._healthy_cv * dtimes.std() < abs(dtimes.mean())

    def is_range_healthy(
        self,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
    ) -> bool:
        """
        计算指定时间范围内的数据覆盖比例

        Args:
            start_timestamp: 起始时间戳
            end_timestamp: 结束时间戳

        Returns:
            覆盖比例（实际覆盖 / 期望覆盖），数据不足返回 0.0（极不健康）
        """
        start_pos = bisect.bisect_left(self._data_list, start_timestamp, key=lambda x: x[1]) if start_timestamp is not None else 0
        end_pos = bisect.bisect_right(self._data_list, end_timestamp, key=lambda x: x[1]) if end_timestamp is not None else len(self._data_list)

        if end_pos - start_pos < self._healthy_points:
            return False

        # 最后一个点的索引是 end_pos - 1
        last_idx = end_pos - 1
        if last_idx < start_pos:
            return False
        actual_range = self._data_list[last_idx][1] - self._data_list[start_pos][1]
        if start_timestamp is None and end_timestamp is None:
            expected_range = self.healthy_window
        else:
            expected_range = end_timestamp - start_timestamp
        return expected_range * self._healthy_range < actual_range

    def get(self) -> tuple[Optional[T], float]:
        """获取最新数据点"""
        try:
            return self._data_list[-1]
        except IndexError:
            return (None, 0.0)

    async def update(self, data: T, timestamp: Optional[float] = None):
        await self.append(data, timestamp)

    @property
    def is_healthy(self) -> bool:
        """
        判断指定时间范围内的数据是否健康

        三个条件同时满足：
        1. timeout < timeout_threshold: 数据足够新鲜
        2. cv < cv_threshold: 采样足够均匀
        3. range > range_threshold: 覆盖时间足够长
        """
        if self._dirty:
            return False
        data, timestamp = self.get()
        if data is None:
            return False
        if time.time() - timestamp > self.max_age:
            return False
        return self.is_range_healthy() and self.is_cv_healthy()

    def __len__(self) -> int:
        return len(self._data_list)

    def __getitem__(self, index: int) -> T:
        """索引访问，返回值（不含时间戳）"""
        return self._data_list[index][0]

    def __iter__(self):
        """迭代返回值"""
        # Keep it tuple-unpacking (instead of subscript) to avoid rare astroid/pylint crashes.
        for value, _ in self._data_list:
            yield value

    def clear(self) -> None:
        """清空数据"""
        self._data_list.clear()
