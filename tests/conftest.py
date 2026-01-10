import pytest
import asyncio
from typing import Optional
from unittest.mock import AsyncMock

from hft.core.listener import Listener, ListenerState


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
        super().__init__(name or "MockListener", interval)
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
