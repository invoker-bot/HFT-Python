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
        tick_callback_fn: Optional[AsyncMock] = None,
        start_callback_fn: Optional[AsyncMock] = None,
        stop_callback_fn: Optional[AsyncMock] = None,
        health_check_callback_fn: Optional[AsyncMock] = None,
    ):
        super().__init__(name or "MockListener", interval)
        self._tick_callback_fn = tick_callback_fn or AsyncMock()
        self._start_callback_fn = start_callback_fn or AsyncMock()
        self._stop_callback_fn = stop_callback_fn or AsyncMock()
        self._health_check_callback_fn = health_check_callback_fn

    async def tick_callback(self):
        return await self._tick_callback_fn()

    async def start_callback(self):
        return await self._start_callback_fn()

    async def stop_callback(self):
        return await self._stop_callback_fn()

    async def health_check_callback(self):
        if self._health_check_callback_fn:
            return await self._health_check_callback_fn()
        return await super().health_check_callback()


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
