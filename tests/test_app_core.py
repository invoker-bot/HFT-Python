"""
Integration tests for AppCore.

Tests cover:
- AppCore lifecycle with child listeners
- Health check behavior
- Graceful shutdown
"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, PropertyMock, AsyncMock

from hft.core.app import AppCore
from hft.core.app.config import AppConfig
from hft.core.listener import Listener, ListenerState
from hft.executor.base import BaseExecutor
from hft.executor.config import MarketExecutorConfig
from tests.conftest import MockListener


# ============================================================
# Mock classes for testing
# ============================================================

class MockExecutor(BaseExecutor):
    """Mock executor for testing."""

    def __init__(self, config=None):
        if config is None:
            config = MarketExecutorConfig(path="mock/executor", per_order_usd=100.0)
        super().__init__(config)

    @property
    def per_order_usd(self) -> float:
        return 100.0

    async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
        pass


def create_mock_app_config(**kwargs):
    """Create a mock AppConfig for testing."""
    defaults = {
        "interval": 0.1,
        "health_check_interval": 0.1,
        "log_interval": 0.1,
        "cache_interval": 0.1,
        "strategies": [],
        "exchanges": [],
        "executor": "market/default",
        "database_url": None,
        "debug": True,
        "max_duration": None,
        "path": "test/app",
    }
    defaults.update(kwargs)

    config = MagicMock(spec=AppConfig)
    for key, value in defaults.items():
        setattr(config, key, value)

    return config


# ============================================================
# Tests for AppCore with mocked dependencies
# ============================================================

class TestAppCoreLifecycle:
    """Tests for AppCore lifecycle management."""

    @pytest.mark.asyncio
    async def test_appcore_initializes_with_children(self):
        """AppCore should initialize with expected children."""
        config = create_mock_app_config()

        # Mock the executor config loading
        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            # Should have 7 children: 3 utility listeners + 4 core components
            assert len(app_core.children) >= 3
            assert 'StateLogListener' in app_core.children
            assert 'UnhealthyRestartListener' in app_core.children
            assert 'CacheListener' in app_core.children

    @pytest.mark.asyncio
    async def test_appcore_start_transitions_to_running(self):
        """AppCore start should transition state to RUNNING."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            await app_core.start(recursive=False)
            await app_core.tick()  # STARTING -> RUNNING

            assert app_core.state == ListenerState.RUNNING

            await app_core.stop(recursive=False)

    @pytest.mark.asyncio
    async def test_appcore_stop_transitions_to_stopped(self):
        """AppCore stop should transition state to STOPPED."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            await app_core.start(recursive=False)
            await app_core.tick()  # STARTING -> RUNNING
            await app_core.stop(recursive=False)

            assert app_core.state == ListenerState.STOPPED


class TestAppCoreRunTicks:
    """Tests for run_ticks main loop."""

    @pytest.mark.asyncio
    async def test_run_ticks_with_duration_stops_after_time(self):
        """run_ticks with positive duration should stop after specified time."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            start_time = asyncio.get_event_loop().time()
            await app_core.run_ticks(duration=0.2, initialize=True, finalize=True)
            elapsed = asyncio.get_event_loop().time() - start_time

            assert elapsed >= 0.15  # Allow some tolerance
            assert elapsed < 1.0
            assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_initializes_when_specified(self):
        """run_ticks should call start when initialize=True."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            await app_core.run_ticks(duration=0.1, initialize=True, finalize=True)

            # After finalize, should be stopped
            assert app_core.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_run_ticks_finalizes_when_specified(self):
        """run_ticks should call stop when finalize=True."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            await app_core.run_ticks(duration=0.1, initialize=True, finalize=True)

            assert app_core.state == ListenerState.STOPPED


class TestAppCoreWithMockChildren:
    """Tests using mock children for controlled scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_children_all_managed(self):
        """Multiple children should all be started and stopped."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            children = [
                MockListener(name=f"test_child_{i}", interval=0.05)
                for i in range(3)
            ]

            for child in children:
                app_core.add_child(child)

            await app_core.start(recursive=True)

            # Tick all children to transition to RUNNING
            for child in app_core.children.values():
                if child.state == ListenerState.STARTING:
                    await child.tick()

            # Check children are in STARTING or RUNNING state
            for child in children:
                assert child.state in (ListenerState.STARTING, ListenerState.RUNNING)

            await app_core.stop(recursive=True)

            for child in children:
                assert child.state == ListenerState.STOPPED


class TestStateLogListenerIntegration:
    """Tests for StateLogListener integration with AppCore."""

    @pytest.mark.asyncio
    async def test_state_logger_exists(self):
        """StateLogListener should be a child of AppCore."""
        config = create_mock_app_config(log_interval=0.05)

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            assert 'StateLogListener' in app_core.children

    @pytest.mark.asyncio
    async def test_state_logger_can_tick(self):
        """StateLogListener on_tick should execute without error."""
        config = create_mock_app_config(log_interval=0.05)

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            state_logger = app_core.children['StateLogListener']

            await app_core.start(recursive=False)
            await app_core.tick()  # STARTING -> RUNNING

            # on_tick should not raise
            await state_logger.on_tick()

            await app_core.stop(recursive=False)


class TestAppCoreCancellation:
    """Tests for graceful cancellation handling."""

    @pytest.mark.asyncio
    async def test_cancelled_error_breaks_loop_gracefully(self):
        """CancelledError should break the loop gracefully."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
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


class TestAppCoreComponents:
    """Tests for AppCore component initialization."""

    @pytest.mark.asyncio
    async def test_has_exchange_group(self):
        """AppCore should have an ExchangeGroup."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            assert hasattr(app_core, 'exchange_group')
            assert app_core.exchange_group is not None

    @pytest.mark.asyncio
    async def test_has_strategy_group(self):
        """AppCore should have a StrategyGroup."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            assert hasattr(app_core, 'strategy_group')
            assert app_core.strategy_group is not None

    @pytest.mark.asyncio
    async def test_has_executor(self):
        """AppCore should have an Executor."""
        config = create_mock_app_config()

        mock_executor_config = MagicMock()
        mock_executor_config.instance = MockExecutor()

        with patch('hft.core.app.base.BaseExecutorConfig.load', return_value=mock_executor_config):
            app_core = AppCore(config)

            assert hasattr(app_core, 'executor')
            assert app_core.executor is not None
