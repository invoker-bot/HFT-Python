"""
Integration tests for AppCore.

Tests cover:
- AppCore lifecycle with child listeners
- Health check behavior
- Graceful shutdown
"""
import pytest
import asyncio
from unittest.mock import MagicMock

from hft.core.app import AppCore
from hft.core.listener import ListenerState
from tests.conftest import MockListener


class MockAppConfig:
    """Mock AppConfig for testing."""

    def __init__(
        self,
        interval: float = 0.1,
        health_check_interval: float = 0.1,
        log_interval: float = 0.1,
        cache_interval: float = 0.1,
        data_path: str = "data/test_cache.pkl"
    ):
        self.interval = interval
        self.health_check_interval = health_check_interval
        self.log_interval = log_interval
        self.cache_interval = cache_interval
        self.data_path = data_path


class TestAppCoreLifecycle:
    """Tests for AppCore lifecycle management."""

    @pytest.mark.asyncio
    async def test_appcore_initializes_with_children(self):
        """AppCore should initialize with StateLogListener, UnhealthyRestartListener and CacheListener children."""
        config = MockAppConfig()
        app_core = AppCore(config)

        assert len(app_core.children) == 3
        assert 'StateLogListener' in app_core.children
        assert 'UnhealthyRestartListener' in app_core.children
        assert 'CacheListener' in app_core.children

    @pytest.mark.asyncio
    async def test_appcore_start_starts_children(self):
        """AppCore start should start all children."""
        config = MockAppConfig()
        app_core = AppCore(config)

        await app_core.start(recursive=True)

        assert app_core.state == ListenerState.RUNNING
        for child in app_core.children.values():
            assert child.state == ListenerState.RUNNING

        await app_core.stop(recursive=True)

    @pytest.mark.asyncio
    async def test_appcore_stop_stops_children(self):
        """AppCore stop should stop all children."""
        config = MockAppConfig()
        app_core = AppCore(config)

        await app_core.start(recursive=True)
        await app_core.stop(recursive=True)

        assert app_core.state == ListenerState.STOPPED
        for child in app_core.children.values():
            assert child.state == ListenerState.STOPPED


class TestAppCoreRunTicks:
    """Tests for run_ticks main loop."""

    @pytest.mark.asyncio
    async def test_run_ticks_with_duration_stops_after_time(self):
        """run_ticks with positive duration should stop after specified time."""
        config = MockAppConfig()
        app_core = AppCore(config)

        start_time = asyncio.get_event_loop().time()
        await app_core.run_ticks(duration=0.2, initialize=True, finalize=True)
        elapsed = asyncio.get_event_loop().time() - start_time

        assert elapsed >= 0.2
        assert elapsed < 0.5
        assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_initializes_when_specified(self):
        """run_ticks should call start when initialize=True."""
        config = MockAppConfig()
        app_core = AppCore(config)

        await app_core.run_ticks(duration=0.1, initialize=True, finalize=True)

        assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_finalizes_when_specified(self):
        """run_ticks should call stop when finalize=True."""
        config = MockAppConfig()
        app_core = AppCore(config)

        await app_core.run_ticks(duration=0.1, initialize=True, finalize=True)

        assert app_core.state == ListenerState.STOPPED


class TestAppCoreWithMockChildren:
    """Tests using mock children for controlled scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_children_all_managed(self):
        """Multiple children should all be started and stopped."""
        config = MockAppConfig()
        app_core = AppCore(config)

        children = [
            MockListener(name=f"child_{i}", interval=0.05)
            for i in range(3)
        ]

        for child in children:
            app_core.add_child(child)

        await app_core.start(recursive=True)

        for child in children:
            assert child.state == ListenerState.RUNNING

        await app_core.stop(recursive=True)

        for child in children:
            assert child.state == ListenerState.STOPPED


class TestStateLogListenerIntegration:
    """Tests for StateLogListener integration with AppCore."""

    @pytest.mark.asyncio
    async def test_state_logger_tick_outputs(self):
        """StateLogListener on_tick should output the status."""
        config = MockAppConfig(log_interval=0.05)
        app_core = AppCore(config)

        state_logger = app_core.children['StateLogListener']

        mock_console = MagicMock()
        state_logger._console = mock_console

        await app_core.start(recursive=True)
        await state_logger.on_tick()

        assert mock_console.print.call_count >= 2

        await app_core.stop(recursive=True)

    @pytest.mark.asyncio
    async def test_state_logger_shows_all_listeners(self):
        """StateLogListener should show all listeners in the hierarchy."""
        config = MockAppConfig(log_interval=0.05)
        app_core = AppCore(config)

        child1 = MockListener(name="child1")
        child2 = MockListener(name="child2")
        app_core.add_child(child1)
        app_core.add_child(child2)

        state_logger = app_core.children['StateLogListener']
        mock_console = MagicMock()
        state_logger._console = mock_console

        await app_core.start(recursive=True)
        await state_logger.on_tick()

        assert mock_console.print.called

        await app_core.stop(recursive=True)


class TestAppCoreCancellation:
    """Tests for graceful cancellation handling."""

    @pytest.mark.asyncio
    async def test_cancelled_error_breaks_loop_gracefully(self):
        """CancelledError should break the loop gracefully."""
        config = MockAppConfig()
        app_core = AppCore(config)

        async def run_and_cancel():
            task = asyncio.create_task(
                app_core.run_ticks(duration=-1, initialize=True, finalize=True)
            )
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_and_cancel()

        assert app_core.state == ListenerState.STOPPED
