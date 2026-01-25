import time
import asyncio
from typing import Optional
from unittest.mock import AsyncMock

import pytest
from sleepfake import SleepFake
from hft.core.listener import Listener


class BenchmarkTimer:

    def __init__(self):
        self.time_delta = 0

    def now(self):
        return self.time_delta + time.time()


g_benchmark_timer = BenchmarkTimer()


class BenchmarkSleepFake(SleepFake):

    def __init__(self, bench_mark_timer: BenchmarkTimer):
        super().__init__()
        self.timer = bench_mark_timer
        self.start = time.time()

    def __enter__(self):
        result = super().__enter__()
        self.start = time.time()
        return result

    async def __aenter__(self):
        result = await super().__aenter__()
        self.start = time.time()
        return result

    def calibrate(self):
        self.timer.time_delta += time.time() - self.start
        self.start = time.time()

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object):
        self.calibrate()
        super().__exit__(exc_type, exc_val, exc_tb)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.calibrate()
        return await super().__aexit__(exc_type, exc_val, exc_tb)


class MockListener(Listener):
    """A concrete implementation of Listener for testing."""

    def __init__(
        self,
        name: Optional[str] = None,
        interval: float = 0.1,
        on_tick_fn: Optional[AsyncMock] = None,
        on_start_fn: Optional[AsyncMock] = None,
        on_stop_fn: Optional[AsyncMock] = None,
        on_health_check_fn: Optional[AsyncMock] = None,
    ):
        super().__init__(name=name or "MockListener", interval=interval)
        self._on_tick_fn = on_tick_fn or AsyncMock(return_value=False)
        self._on_start_fn = on_start_fn or AsyncMock()
        self._on_stop_fn = on_stop_fn or AsyncMock()
        self._on_health_check_fn = on_health_check_fn

    async def on_tick(self) -> bool:
        return await self._on_tick_fn()

    async def on_start(self):
        return await self._on_start_fn()

    async def on_stop(self):
        return await self._on_stop_fn()

    async def on_health_check(self):
        if self._on_health_check_fn:
            return await self._on_health_check_fn()
        return await super().on_health_check()


# ============================================================
# 类索引测试用的 Mock 类层次结构
# ============================================================

class MockExecutor(Listener):
    """模拟执行器基类"""
    async def on_tick(self) -> bool:
        return False


class MockMarketExecutor(MockExecutor):
    """模拟市价执行器"""
    pass


class MockLimitExecutor(MockExecutor):
    """模拟限价执行器"""
    pass


class MockStrategy(Listener):
    """模拟策略基类"""
    async def on_tick(self) -> bool:
        return False


class MockTrendStrategy(MockStrategy):
    """模拟趋势策略"""
    pass


class MockMeanReversionStrategy(MockStrategy):
    """模拟均值回归策略"""
    pass


@pytest.fixture
def mock_listener():
    """Create a basic mock listener."""
    return MockListener(name="test_listener", interval=0.1)


@pytest.fixture
def mock_listener_factory():
    """Factory to create mock listeners with custom callbacks."""
    def factory(name: str = "test_listener", interval: float = 0.1, **kwargs):
        return MockListener(name=name, interval=interval, **kwargs)
    return factory


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
