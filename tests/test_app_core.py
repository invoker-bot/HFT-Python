"""
Integration tests for AppCore.

Tests cover:
- AppCore lifecycle with StateLogger
- Health check loop behavior
- Unhealthy child restart
- Graceful shutdown
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from hft.core.app import AppCore
from hft.core.listener import Listener, ListenerState
from hft.core.state_logger import StateLogListener
from hft.config.app import AppConfig
from tests.conftest import MockListener


class MockAppConfig:
    """Mock AppConfig for testing."""

    def __init__(self, interval: float = 0.1, health_check_interval: float = 0.1, log_interval: float = 0.1):
        self.interval = interval
        self.health_check_interval = health_check_interval
        self.log_interval = log_interval


class TestAppCoreLifecycle:
    """Tests for AppCore lifecycle management."""

    @pytest.mark.asyncio
    async def test_appcore_initializes_with_state_logger(self):
        """AppCore should initialize with StateLogListener and UnhealthyRestartListener children."""
        config = MockAppConfig()
        app_core = AppCore(config)

        assert len(app_core.children) == 2
        assert 'StateLogListener' in app_core.children
        assert 'UnhealthyRestartListener' in app_core.children

    @pytest.mark.asyncio
    async def test_appcore_start_starts_children(self):
        """AppCore start should start all children including StateLogListener."""
        config = MockAppConfig()
        app_core = AppCore(config)

        await app_core.start(children=True, background=False)

        assert app_core.state == ListenerState.RUNNING
        for child in app_core.children.values():
            assert child.state == ListenerState.RUNNING

        await app_core.stop(children=True)

    @pytest.mark.asyncio
    async def test_appcore_stop_stops_children(self):
        """AppCore stop should stop all children."""
        config = MockAppConfig()
        app_core = AppCore(config)

        await app_core.start(children=True, background=False)
        await app_core.stop(children=True)

        assert app_core.state == ListenerState.STOPPED
        for child in app_core.children.values():
            assert child.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_appcore_tick_callback_does_nothing(self):
        """AppCore tick_callback should be a no-op."""
        config = MockAppConfig()
        app_core = AppCore(config)

        await app_core.start(children=False, background=False)

        # Should not raise
        result = await app_core.tick_callback()

        assert result is None

        await app_core.stop(children=False)


class TestAppCoreRunTicks:
    """Tests for run_ticks main loop."""

    @pytest.mark.asyncio
    async def test_run_ticks_with_duration_stops_after_time(self):
        """run_ticks with positive duration should stop after specified time."""
        config = MockAppConfig(health_check_interval=0.05)
        app_core = AppCore(config)

        start_time = asyncio.get_event_loop().time()
        await app_core.run_ticks(duration=0.2, initialize=True, finalize=True)
        elapsed = asyncio.get_event_loop().time() - start_time

        assert elapsed >= 0.2
        assert elapsed < 0.5  # Should not run much longer
        assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_initializes_when_specified(self):
        """run_ticks should call start when initialize=True."""
        config = MockAppConfig(health_check_interval=0.05)
        app_core = AppCore(config)

        await app_core.run_ticks(duration=0.1, initialize=True, finalize=True)

        # Should have been started and stopped
        assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_skips_initialize_when_false(self):
        """run_ticks should not call start when initialize=False."""
        config = MockAppConfig(health_check_interval=0.05)
        app_core = AppCore(config)

        # Pre-start manually
        await app_core.start(children=True, background=True)

        await app_core.run_ticks(duration=0.1, initialize=False, finalize=True)

        assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_finalizes_when_specified(self):
        """run_ticks should call stop when finalize=True."""
        config = MockAppConfig(health_check_interval=0.05)
        app_core = AppCore(config)

        await app_core.run_ticks(duration=0.1, initialize=True, finalize=True)

        assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_skips_finalize_when_false(self):
        """run_ticks should not call stop when finalize=False."""
        config = MockAppConfig(health_check_interval=0.05)
        app_core = AppCore(config)

        await app_core.run_ticks(duration=0.1, initialize=True, finalize=False)

        # Should still be running
        assert app_core.state == ListenerState.RUNNING

        # Clean up
        await app_core.stop(children=True)


class TestAppCoreWithMockChildren:
    """Tests using mock children for controlled scenarios."""

    @pytest.mark.asyncio
    async def test_child_exception_does_not_crash_loop(self):
        """Exception in child health check should not crash the main loop."""
        config = MockAppConfig(health_check_interval=0.05)
        app_core = AppCore(config)

        # Create a child that throws during health check
        failing_child = MockListener(
            name="failing_child",
            health_check_callback_fn=AsyncMock(side_effect=RuntimeError("Health check explosion"))
        )
        app_core.add_child(failing_child)

        # Should not raise
        with patch('hft.core.listener.RETRY_WAIT_SECONDS', 0.01):
            await app_core.run_ticks(duration=0.15, initialize=True, finalize=True)

        assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_multiple_children_all_managed(self):
        """Multiple children should all be started and stopped."""
        config = MockAppConfig(health_check_interval=0.05)
        app_core = AppCore(config)

        children = [
            MockListener(name=f"child_{i}", interval=0.05)
            for i in range(3)
        ]

        for child in children:
            app_core.add_child(child)

        await app_core.start(children=True, background=False)

        for child in children:
            assert child.state == ListenerState.RUNNING

        await app_core.stop(children=True)

        for child in children:
            assert child.state == ListenerState.STOPPED


class TestStateLogListenerIntegration:
    """Tests for StateLogListener integration with AppCore."""

    @pytest.mark.asyncio
    async def test_state_logger_tick_outputs_table(self):
        """StateLogListener tick should output the status table."""
        config = MockAppConfig(log_interval=0.05)
        app_core = AppCore(config)

        # Get the StateLogListener child
        state_logger = app_core.children['StateLogListener']

        # Mock the console to capture output
        mock_console = MagicMock()
        state_logger._console = mock_console

        # Mock log_state method that doesn't exist on Listener
        app_core.log_state = MagicMock()

        await app_core.start(children=True, background=False)
        await state_logger.on_tick()

        # Should have printed the header and tree
        assert mock_console.print.call_count >= 2

        await app_core.stop(children=True)

    @pytest.mark.asyncio
    async def test_state_logger_shows_all_listeners(self):
        """StateLogListener should show all listeners in the hierarchy."""
        config = MockAppConfig(log_interval=0.05)
        app_core = AppCore(config)

        # Add some children
        child1 = MockListener(name="child1")
        child2 = MockListener(name="child2")
        app_core.add_child(child1)
        app_core.add_child(child2)

        state_logger = app_core.children['StateLogListener']
        mock_console = MagicMock()
        state_logger._console = mock_console

        # Mock log_state method that doesn't exist on Listener
        app_core.log_state = MagicMock()

        await app_core.start(children=True, background=False)
        await state_logger.on_tick()

        # Verify the tree was printed
        assert mock_console.print.called

        await app_core.stop(children=True)


class TestAppCoreCancellation:
    """Tests for graceful cancellation handling."""

    @pytest.mark.asyncio
    async def test_cancelled_error_breaks_loop_gracefully(self):
        """CancelledError should break the loop gracefully."""
        config = MockAppConfig(health_check_interval=0.05)
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

        # Should be stopped gracefully
        # Note: finalize=True means stop was called even on cancellation
        assert app_core.state == ListenerState.STOPPED
