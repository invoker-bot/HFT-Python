"""
Unit tests for the Listener base class.

Tests cover:
- Lifecycle management (start/stop/restart)
- Health check and recovery
- Error handling with mocks
- Parent-child relationships
- Background task management
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from hft.core.listener import Listener, ListenerState, RETRY_ATTEMPTS, RETRY_WAIT_SECONDS
from tests.conftest import MockListener


class TestListenerLifecycle:
    """Tests for Listener lifecycle management (start/stop/restart)."""

    @pytest.mark.asyncio
    async def test_initial_state_is_starting(self, mock_listener):
        """Listener should start in STARTING state."""
        assert mock_listener.state == ListenerState.STARTING
        assert mock_listener.healthy is False
        assert mock_listener.enabled is True

    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self, mock_listener):
        """start() should transition state from STARTING to RUNNING."""
        assert mock_listener.state == ListenerState.STARTING

        await mock_listener.start(recursive=False)

        assert mock_listener.state == ListenerState.RUNNING
        mock_listener._on_start_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_start_is_called(self, mock_listener_factory):
        """on_start should be called during start."""
        on_start = AsyncMock()
        listener = mock_listener_factory(on_start_fn=on_start)

        await listener.start(recursive=False)

        on_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_transitions_to_stopped(self, mock_listener):
        """stop() should transition state from RUNNING to STOPPED."""
        await mock_listener.start(recursive=False)
        assert mock_listener.state == ListenerState.RUNNING

        await mock_listener.stop(recursive=False)

        assert mock_listener.state == ListenerState.STOPPED
        mock_listener._on_stop_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_stop_is_called(self, mock_listener_factory):
        """on_stop should be called during stop."""
        on_stop = AsyncMock()
        listener = mock_listener_factory(on_stop_fn=on_stop)

        await listener.start(recursive=False)
        await listener.stop(recursive=False)

        on_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_restart_stops_and_starts(self, mock_listener):
        """restart() should stop then start the listener."""
        await mock_listener.start(recursive=False)

        await mock_listener.restart(recursive=False)

        assert mock_listener.state == ListenerState.RUNNING
        mock_listener._on_stop_fn.assert_awaited_once()
        assert mock_listener._on_start_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_ready_property(self, mock_listener):
        """ready should be True only when enabled, healthy, and running."""
        # Initially not ready (STARTING state, not healthy)
        assert mock_listener.ready is False

        # Need to stop first, then start (because initial state is STARTING)
        mock_listener._state = ListenerState.STOPPED
        await mock_listener.start(recursive=False)

        # After start and tick, should be running and healthy
        await mock_listener.tick()  # Execute on_tick to set healthy=True
        assert mock_listener.ready is True

        mock_listener.enabled = False
        assert mock_listener.ready is False

        mock_listener.enabled = True
        await mock_listener.stop(recursive=False)
        assert mock_listener.ready is False

    @pytest.mark.asyncio
    async def test_uptime_is_zero_when_not_running(self, mock_listener):
        """uptime should be 0 when not running."""
        assert mock_listener.uptime == 0.0

    @pytest.mark.asyncio
    async def test_uptime_increases_when_running(self, mock_listener):
        """uptime should increase while running."""
        await mock_listener.start(recursive=False)

        await asyncio.sleep(0.1)

        assert mock_listener.uptime > 0


class TestListenerHealthCheck:
    """Tests for health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_sets_healthy(self, mock_listener):
        """Successful health check should set healthy to True."""
        await mock_listener.start(recursive=False)

        await mock_listener.health_check(recursive=False)

        assert mock_listener.healthy is True

    @pytest.mark.asyncio
    async def test_health_check_failure_sets_unhealthy(self, mock_listener_factory):
        """Failed health check should set healthy to False."""
        on_health_check = AsyncMock(return_value=False)
        listener = mock_listener_factory(on_health_check_fn=on_health_check)

        await listener.start(recursive=False)

        with patch.object(listener, 'on_health_check_error', new_callable=AsyncMock):
            await listener.health_check(recursive=False)

        assert listener.healthy is False

    @pytest.mark.asyncio
    async def test_health_check_exception_sets_unhealthy(self, mock_listener_factory):
        """Exception in health check should set healthy to False."""
        on_health_check = AsyncMock(side_effect=RuntimeError("Health check failed"))
        listener = mock_listener_factory(on_health_check_fn=on_health_check)

        await listener.start(recursive=False)

        with patch.object(listener, 'on_health_check_error', new_callable=AsyncMock):
            await listener.health_check(recursive=False)

        assert listener.healthy is False

    @pytest.mark.asyncio
    async def test_health_check_retries_on_failure(self, mock_listener_factory):
        """Health check should retry on failure up to RETRY_ATTEMPTS times."""
        call_count = 0

        async def failing_health_check():
            nonlocal call_count
            call_count += 1
            if call_count < RETRY_ATTEMPTS:
                raise RuntimeError("Temporary failure")
            return True

        on_health_check = AsyncMock(side_effect=failing_health_check)
        listener = mock_listener_factory(on_health_check_fn=on_health_check)

        await listener.start(recursive=False)

        with patch('hft.core.listener.RETRY_WAIT_SECONDS', 0.01):
            with patch.object(listener, 'on_health_check_error', new_callable=AsyncMock):
                await listener.health_check(recursive=False)

        assert call_count == RETRY_ATTEMPTS
        assert listener.healthy is True


class TestListenerErrorHandling:
    """Tests for error handling with mocks."""

    @pytest.mark.asyncio
    async def test_on_start_exception_sets_unhealthy(self, mock_listener_factory):
        """Exception in on_start should set healthy to False."""
        on_start = AsyncMock(side_effect=RuntimeError("Start failed"))
        listener = mock_listener_factory(on_start_fn=on_start)

        with patch('hft.core.listener.RETRY_WAIT_SECONDS', 0.01):
            await listener.start(recursive=False)

        assert listener.healthy is False

    @pytest.mark.asyncio
    async def test_on_tick_exception_sets_unhealthy(self, mock_listener_factory):
        """Exception in on_tick should set healthy to False."""
        on_tick = AsyncMock(side_effect=RuntimeError("Tick failed"))
        listener = mock_listener_factory(on_tick_fn=on_tick)

        await listener.start(recursive=False)

        with patch('hft.core.listener.RETRY_WAIT_SECONDS', 0.01):
            await listener.tick()

        assert listener.healthy is False


class TestListenerParentChild:
    """Tests for parent-child relationships."""

    @pytest.mark.asyncio
    async def test_add_child_sets_parent(self, mock_listener_factory):
        """add_child should set parent reference on child."""
        parent = mock_listener_factory(name="parent")
        child = mock_listener_factory(name="child")

        parent.add_child(child)

        assert child.parent is parent
        assert child.name in parent.children

    @pytest.mark.asyncio
    async def test_remove_child_clears_parent(self, mock_listener_factory):
        """remove_child should clear parent reference."""
        parent = mock_listener_factory(name="parent")
        child = mock_listener_factory(name="child")

        parent.add_child(child)
        parent.remove_child(child.name)

        assert child.parent is None
        assert child.name not in parent.children

    @pytest.mark.asyncio
    async def test_root_returns_topmost_parent(self, mock_listener_factory):
        """root should return the topmost parent in the hierarchy."""
        grandparent = mock_listener_factory(name="grandparent")
        parent = mock_listener_factory(name="parent")
        child = mock_listener_factory(name="child")

        grandparent.add_child(parent)
        parent.add_child(child)

        assert child.root is grandparent
        assert parent.root is grandparent
        assert grandparent.root is grandparent

    @pytest.mark.asyncio
    async def test_start_with_recursive_starts_all(self, mock_listener_factory):
        """start(recursive=True) should start all children."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")

        parent.add_child(child1)
        parent.add_child(child2)

        await parent.start(recursive=True)

        assert parent.state == ListenerState.RUNNING
        assert child1.state == ListenerState.RUNNING
        assert child2.state == ListenerState.RUNNING

    @pytest.mark.asyncio
    async def test_stop_with_recursive_stops_all(self, mock_listener_factory):
        """stop(recursive=True) should stop all children."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")

        parent.add_child(child1)
        parent.add_child(child2)

        await parent.start(recursive=True)
        await parent.stop(recursive=True)

        assert parent.state == ListenerState.STOPPED
        assert child1.state == ListenerState.STOPPED
        assert child2.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_health_check_with_recursive_checks_all(self, mock_listener_factory):
        """health_check(recursive=True) should check all children."""
        parent = mock_listener_factory(name="parent")
        child = mock_listener_factory(name="child")

        parent.add_child(child)

        await parent.start(recursive=True)
        await parent.health_check(recursive=True)

        assert parent.healthy is True
        assert child.healthy is True

    @pytest.mark.asyncio
    async def test_iter_includes_self_and_descendants(self, mock_listener_factory):
        """__iter__ should yield self and all descendants."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")
        grandchild = mock_listener_factory(name="grandchild")

        parent.add_child(child1)
        parent.add_child(child2)
        child1.add_child(grandchild)

        listeners = list(parent)

        assert len(listeners) == 4
        assert parent in listeners
        assert child1 in listeners
        assert child2 in listeners
        assert grandchild in listeners


class TestListenerBackgroundTask:
    """Tests for background task management."""

    @pytest.mark.asyncio
    async def test_update_background_creates_task(self, mock_listener):
        """update_background should create a background task."""
        await mock_listener.start(recursive=False)

        mock_listener.update_background()

        assert mock_listener._background_task is not None

        # Clean up
        mock_listener.delete_background()

    @pytest.mark.asyncio
    async def test_delete_background_cancels_task(self, mock_listener):
        """delete_background should cancel the background task."""
        await mock_listener.start(recursive=False)
        mock_listener.update_background()

        assert mock_listener._background_task is not None

        mock_listener.delete_background()

        assert mock_listener._background_task is None

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self, mock_listener):
        """start() should create a background task."""
        await mock_listener.start(recursive=False)

        assert mock_listener._background_task is not None

        # Clean up
        mock_listener.delete_background()


class TestListenerLogStateDict:
    """Tests for log_state_dict property."""

    @pytest.mark.asyncio
    async def test_log_state_dict_contains_expected_keys(self, mock_listener):
        """log_state_dict should contain all expected keys."""
        state = mock_listener.log_state_dict

        expected_keys = ['enabled', 'ready', 'healthy', 'state', 'uptime']
        for key in expected_keys:
            assert key in state

    @pytest.mark.asyncio
    async def test_log_state_dict_reflects_current_state(self, mock_listener):
        """log_state_dict values should reflect current listener state."""
        assert mock_listener.log_state_dict['state'] == ListenerState.STARTING
        assert mock_listener.log_state_dict['ready'] is False

        # Set to STOPPED first, then start
        mock_listener._state = ListenerState.STOPPED
        await mock_listener.start(recursive=False)
        await mock_listener.tick()  # Execute on_tick to set healthy=True

        assert mock_listener.log_state_dict['state'] == ListenerState.RUNNING
        assert mock_listener.log_state_dict['ready'] is True


class TestListenerSerialization:
    """Tests for __getstate__ and __setstate__ methods."""

    @pytest.mark.asyncio
    async def test_getstate_returns_serializable_dict(self, mock_listener):
        """__getstate__ should return a serializable dictionary."""
        await mock_listener.start(recursive=False)

        state = mock_listener.__getstate__()

        assert isinstance(state, dict)
        assert state['name'] == mock_listener.name
        assert state['interval'] == mock_listener.interval
        assert state['_enabled'] == mock_listener.enabled
        assert state['_state'] == mock_listener.state
        assert state['_healthy'] == mock_listener.healthy

        await mock_listener.stop(recursive=False)

    @pytest.mark.asyncio
    async def test_setstate_restores_basic_attributes(self, mock_listener):
        """__setstate__ should restore basic attributes."""
        await mock_listener.start(recursive=False)
        original_state = mock_listener.__getstate__()
        await mock_listener.stop(recursive=False)

        new_listener = MockListener(name="new_listener")
        new_listener.__setstate__(original_state)

        assert new_listener.name == original_state['name']
        assert new_listener.interval == original_state['interval']
        assert new_listener._enabled == original_state['_enabled']
        assert new_listener._state == original_state['_state']

    @pytest.mark.asyncio
    async def test_setstate_reinitializes_non_serializable_objects(self, mock_listener):
        """__setstate__ should reinitialize locks and background task."""
        state = mock_listener.__getstate__()

        new_listener = MockListener(name="new_listener")
        new_listener.__setstate__(state)

        assert new_listener._alock is not None
        assert new_listener._background_task is None
        assert new_listener._parent is None

    @pytest.mark.asyncio
    async def test_getstate_excludes_non_serializable_objects(self, mock_listener):
        """__getstate__ should not include locks, tasks, or weakrefs."""
        await mock_listener.start(recursive=False)

        state = mock_listener.__getstate__()

        assert '_alock' not in state
        assert '_background_task' not in state
        assert '_parent' not in state

        await mock_listener.stop(recursive=False)
