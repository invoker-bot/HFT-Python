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
from unittest.mock import AsyncMock, patch, MagicMock

from hft.core.listener import Listener, ListenerState, RETRY_ATTEMPTS, RETRY_WAIT_SECONDS
from tests.conftest import MockListener


class TestListenerLifecycle:
    """Tests for Listener lifecycle management (start/stop/restart)."""

    @pytest.mark.asyncio
    async def test_initial_state_is_stopped(self, mock_listener):
        """Listener should start in STOPPED state."""
        assert mock_listener.state == ListenerState.STOPPED
        assert mock_listener.healthy is False
        assert mock_listener.enabled is True

    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self, mock_listener):
        """start() should transition state from STOPPED to RUNNING."""
        assert mock_listener.state == ListenerState.STOPPED

        await mock_listener.start(children=False, background=False)

        assert mock_listener.state == ListenerState.RUNNING
        mock_listener._start_callback_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_callback_is_called(self, mock_listener_factory):
        """start_callback should be called during start."""
        start_callback = AsyncMock()
        listener = mock_listener_factory(start_callback_fn=start_callback)

        await listener.start(children=False, background=False)

        start_callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_transitions_to_stopped(self, mock_listener):
        """stop() should transition state from RUNNING to STOPPED."""
        await mock_listener.start(children=False, background=False)
        assert mock_listener.state == ListenerState.RUNNING

        await mock_listener.stop(children=False)

        assert mock_listener.state == ListenerState.STOPPED
        mock_listener._stop_callback_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_callback_is_called(self, mock_listener_factory):
        """stop_callback should be called during stop."""
        stop_callback = AsyncMock()
        listener = mock_listener_factory(stop_callback_fn=stop_callback)

        await listener.start(children=False, background=False)
        await listener.stop(children=False)

        stop_callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_when_not_running_does_nothing(self, mock_listener):
        """stop() on a non-running listener should not change state."""
        assert mock_listener.state == ListenerState.STOPPED

        await mock_listener.stop(children=False)

        assert mock_listener.state == ListenerState.STOPPED
        mock_listener._stop_callback_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_when_already_running_does_nothing(self, mock_listener):
        """start() on an already running listener should not restart."""
        await mock_listener.start(children=False, background=False)
        mock_listener._start_callback_fn.reset_mock()

        await mock_listener.start(children=False, background=False)

        # Should not call start_callback again
        mock_listener._start_callback_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_restart_stops_and_starts(self, mock_listener):
        """restart() should stop then start the listener."""
        await mock_listener.start(children=False, background=False)

        await mock_listener.restart(children=False, background=False)

        assert mock_listener.state == ListenerState.RUNNING
        mock_listener._stop_callback_fn.assert_awaited_once()
        assert mock_listener._start_callback_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_ready_property(self, mock_listener):
        """ready should be True only when enabled and running."""
        assert mock_listener.ready is False

        await mock_listener.start(children=False, background=False)
        assert mock_listener.ready is True

        mock_listener.enabled = False
        assert mock_listener.ready is False

        mock_listener.enabled = True
        await mock_listener.stop(children=False)
        assert mock_listener.ready is False

    @pytest.mark.asyncio
    async def test_uptime_is_zero_when_not_running(self, mock_listener):
        """uptime should be 0 when not running."""
        assert mock_listener.uptime == 0.0

    @pytest.mark.asyncio
    async def test_uptime_increases_when_running(self, mock_listener):
        """uptime should increase while running."""
        await mock_listener.start(children=False, background=False)

        await asyncio.sleep(0.1)

        assert mock_listener.uptime > 0


class TestListenerHealthCheck:
    """Tests for health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_sets_healthy(self, mock_listener):
        """Successful health check should set health to True."""
        await mock_listener.start(children=False, background=False)

        await mock_listener.health_check(children=False)

        assert mock_listener.healthy is True

    @pytest.mark.asyncio
    async def test_health_check_callback_failure_sets_unhealthy(self, mock_listener_factory):
        """Failed health check callback should set health to False."""
        health_callback = AsyncMock(return_value=False)
        listener = mock_listener_factory(health_check_callback_fn=health_callback)

        await listener.start(children=False, background=False)

        with patch.object(listener, 'health_check_after', new_callable=AsyncMock):
            await listener.health_check(children=False)

        assert listener.healthy is False

    @pytest.mark.asyncio
    async def test_health_check_exception_sets_unhealthy(self, mock_listener_factory):
        """Exception in health check should set health to False."""
        health_callback = AsyncMock(side_effect=RuntimeError("Health check failed"))
        listener = mock_listener_factory(health_check_callback_fn=health_callback)

        await listener.start(children=False, background=False)

        with patch.object(listener, 'health_check_after', new_callable=AsyncMock):
            await listener.health_check(children=False)

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

        health_callback = AsyncMock(side_effect=failing_health_check)
        listener = mock_listener_factory(health_check_callback_fn=health_callback)

        await listener.start(children=False, background=False)

        # Patch retry wait to speed up test
        with patch('hft.core.listener.RETRY_WAIT_SECONDS', 0.01):
            with patch.object(listener, 'health_check_after', new_callable=AsyncMock):
                await listener.health_check(children=False)

        assert call_count == RETRY_ATTEMPTS
        assert listener.healthy is True

    @pytest.mark.asyncio
    async def test_health_check_emits_events(self, mock_listener):
        """Health check should emit appropriate events."""
        events_received = []

        @mock_listener.on('health_check')
        def on_health_check():
            events_received.append('health_check')

        @mock_listener.on('unhealthy')
        def on_unhealthy():
            events_received.append('unhealthy')

        await mock_listener.start(children=False, background=False)
        await mock_listener.health_check(children=False)

        assert 'health_check' in events_received


class TestListenerErrorHandling:
    """Tests for error handling with mocks."""

    @pytest.mark.asyncio
    async def test_start_callback_exception_sets_error_state(self, mock_listener_factory):
        """Exception in start_callback should set state to ERROR."""
        start_callback = AsyncMock(side_effect=RuntimeError("Start failed"))
        listener = mock_listener_factory(start_callback_fn=start_callback)

        await listener.start(children=False, background=False)

        assert listener.state == ListenerState.ERROR

    @pytest.mark.asyncio
    async def test_stop_callback_exception_sets_error_state(self, mock_listener_factory):
        """Exception in stop_callback should set state to ERROR."""
        stop_callback = AsyncMock(side_effect=RuntimeError("Stop failed"))
        listener = mock_listener_factory(stop_callback_fn=stop_callback)

        await listener.start(children=False, background=False)

        # Patch retry wait to speed up test
        with patch('hft.core.listener.RETRY_WAIT_SECONDS', 0.01):
            await listener.stop(children=False)

        assert listener.state == ListenerState.ERROR

    @pytest.mark.asyncio
    async def test_tick_callback_exception_sets_unhealthy(self, mock_listener_factory):
        """Exception in tick_callback should set health to False."""
        tick_callback = AsyncMock(side_effect=RuntimeError("Tick failed"))
        listener = mock_listener_factory(tick_callback_fn=tick_callback)

        await listener.start(children=False, background=False)

        # Patch retry wait to speed up test
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
    async def test_start_with_children_starts_all(self, mock_listener_factory):
        """start(children=True) should start all children."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")

        parent.add_child(child1)
        parent.add_child(child2)

        await parent.start(children=True, background=False)

        assert parent.state == ListenerState.RUNNING
        assert child1.state == ListenerState.RUNNING
        assert child2.state == ListenerState.RUNNING

    @pytest.mark.asyncio
    async def test_stop_with_children_stops_all(self, mock_listener_factory):
        """stop(children=True) should stop all children."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")

        parent.add_child(child1)
        parent.add_child(child2)

        await parent.start(children=True, background=False)
        await parent.stop(children=True)

        assert parent.state == ListenerState.STOPPED
        assert child1.state == ListenerState.STOPPED
        assert child2.state == ListenerState.STOPPED

    @pytest.mark.asyncio
    async def test_health_check_with_children_checks_all(self, mock_listener_factory):
        """health_check(children=True) should check all children."""
        parent = mock_listener_factory(name="parent")
        child = mock_listener_factory(name="child")

        parent.add_child(child)

        await parent.start(children=True, background=False)
        await parent.health_check(children=True)

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


class TestListenerBackgroundTasks:
    """Tests for background task management."""

    @pytest.mark.asyncio
    async def test_add_background_task_creates_task(self, mock_listener):
        """add_background_task should create and store a task."""
        async def dummy_coro():
            return True  # Signal completion

        await mock_listener.start(children=False, background=False)

        mock_listener.add_background_task("test_task", dummy_coro, interval=0.01)

        assert "test_task" in mock_listener._background_tasks

        # Wait for task to complete
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_remove_background_task_cancels_task(self, mock_listener):
        """remove_background_task should cancel and remove the task."""
        task_running = True

        async def long_running_coro():
            nonlocal task_running
            while task_running:
                await asyncio.sleep(0.01)

        await mock_listener.start(children=False, background=False)
        mock_listener.add_background_task("test_task", long_running_coro, interval=0.01)

        assert "test_task" in mock_listener._background_tasks

        mock_listener.remove_background_task("test_task")
        task_running = False

        assert "test_task" not in mock_listener._background_tasks

    @pytest.mark.asyncio
    async def test_start_with_background_adds_tick_task(self, mock_listener):
        """start(background=True) should add a tick background task."""
        await mock_listener.start(children=False, background=True)

        assert "tick" in mock_listener._background_tasks

        # Clean up
        mock_listener.remove_background_task("tick")

    @pytest.mark.asyncio
    async def test_stop_removes_all_background_tasks(self, mock_listener):
        """stop should remove all background tasks."""
        await mock_listener.start(children=False, background=True)
        assert len(mock_listener._background_tasks) > 0

        await mock_listener.stop(children=False)

        assert len(mock_listener._background_tasks) == 0


class TestListenerStateDict:
    """Tests for state_dict property."""

    @pytest.mark.asyncio
    async def test_state_dict_contains_expected_keys(self, mock_listener):
        """state_dict should contain all expected keys."""
        state = mock_listener.state_dict

        expected_keys = ['enabled', 'ready', 'healthy', 'state', 'parent', 'children', 'task_count', 'uptime']
        for key in expected_keys:
            assert key in state

    @pytest.mark.asyncio
    async def test_state_dict_reflects_current_state(self, mock_listener):
        """state_dict values should reflect current listener state."""
        assert mock_listener.state_dict['state'] == ListenerState.STOPPED
        assert mock_listener.state_dict['ready'] is False

        await mock_listener.start(children=False, background=False)

        assert mock_listener.state_dict['state'] == ListenerState.RUNNING
        assert mock_listener.state_dict['ready'] is True

    @pytest.mark.asyncio
    async def test_state_dict_shows_children_count(self, mock_listener_factory):
        """state_dict should show correct children count."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")

        assert parent.state_dict['children'] == 0

        parent.add_child(child1)
        assert parent.state_dict['children'] == 1

        parent.add_child(child2)
        assert parent.state_dict['children'] == 2


class TestListenerEvents:
    """Tests for event emission."""

    @pytest.mark.asyncio
    async def test_started_event_emitted(self, mock_listener):
        """started event should be emitted on successful start."""
        events = []

        @mock_listener.on('started')
        def on_started():
            events.append('started')

        await mock_listener.start(children=False, background=False)

        assert 'started' in events

    @pytest.mark.asyncio
    async def test_stopped_event_emitted(self, mock_listener):
        """stopped event should be emitted on successful stop."""
        events = []

        @mock_listener.on('stopped')
        def on_stopped():
            events.append('stopped')

        await mock_listener.start(children=False, background=False)
        await mock_listener.stop(children=False)

        assert 'stopped' in events


class TestListenerConcurrency:
    """Tests for concurrent access and locking."""

    @pytest.mark.asyncio
    async def test_concurrent_start_only_starts_once(self, mock_listener):
        """Concurrent start calls should only start once."""
        # Start multiple concurrent starts
        await asyncio.gather(
            mock_listener.start(children=False, background=False),
            mock_listener.start(children=False, background=False),
            mock_listener.start(children=False, background=False),
        )

        # start_callback should only be called once
        mock_listener._start_callback_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_stop_only_stops_once(self, mock_listener):
        """Concurrent stop calls should only stop once."""
        await mock_listener.start(children=False, background=False)

        # Start multiple concurrent stops
        await asyncio.gather(
            mock_listener.stop(children=False),
            mock_listener.stop(children=False),
            mock_listener.stop(children=False),
        )

        # stop_callback should only be called once
        mock_listener._stop_callback_fn.assert_awaited_once()


class TestListenerSerialization:
    """Tests for __getstate__ and __setstate__ methods."""

    @pytest.mark.asyncio
    async def test_getstate_returns_serializable_dict(self, mock_listener):
        """__getstate__ should return a serializable dictionary."""
        await mock_listener.start(children=False, background=False)

        state = mock_listener.__getstate__()

        assert isinstance(state, dict)
        assert state['name'] == mock_listener.name
        assert state['interval'] == mock_listener.interval
        assert state['_enabled'] == mock_listener.enabled
        assert state['_state'] == mock_listener.state
        assert state['_health'] == mock_listener.healthy
        assert '_children' in state

        await mock_listener.stop(children=False)

    @pytest.mark.asyncio
    async def test_getstate_includes_children_state(self, mock_listener_factory):
        """__getstate__ should recursively include children's state."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")

        parent.add_child(child1)
        parent.add_child(child2)

        await parent.start(children=True, background=False)

        state = parent.__getstate__()

        assert len(state['_children']) == 2
        assert 'child1' in state['_children']
        assert 'child2' in state['_children']
        assert state['_children']['child1']['name'] == 'child1'
        assert state['_children']['child2']['name'] == 'child2'

        await parent.stop(children=True)

    @pytest.mark.asyncio
    async def test_setstate_restores_basic_attributes(self, mock_listener):
        """__setstate__ should restore basic attributes."""
        # Get current state
        await mock_listener.start(children=False, background=False)
        original_state = mock_listener.__getstate__()
        await mock_listener.stop(children=False)

        # Create a new listener and restore state
        new_listener = MockListener(name="new_listener")
        new_listener.__setstate__(original_state)

        assert new_listener.name == original_state['name']
        assert new_listener.interval == original_state['interval']
        assert new_listener._enabled == original_state['_enabled']
        assert new_listener._state == original_state['_state']

    @pytest.mark.asyncio
    async def test_setstate_reinitializes_non_serializable_objects(self, mock_listener):
        """__setstate__ should reinitialize locks and empty task dict."""
        state = mock_listener.__getstate__()

        new_listener = MockListener(name="new_listener")
        new_listener.__setstate__(state)

        # Should have fresh lock and empty tasks
        assert new_listener._alock is not None
        assert isinstance(new_listener._background_tasks, dict)
        assert len(new_listener._background_tasks) == 0
        assert new_listener._parent is None

    @pytest.mark.asyncio
    async def test_getstate_excludes_non_serializable_objects(self, mock_listener):
        """__getstate__ should not include locks, tasks, or weakrefs."""
        await mock_listener.start(children=False, background=True)

        state = mock_listener.__getstate__()

        # These should not be in the state
        assert '_alock' not in state
        assert '_background_tasks' not in state
        assert '_parent' not in state
        assert 'logger' not in state

        await mock_listener.stop(children=False)

    @pytest.mark.asyncio
    async def test_nested_children_state(self, mock_listener_factory):
        """__getstate__ should handle nested children correctly."""
        grandparent = mock_listener_factory(name="grandparent")
        parent = mock_listener_factory(name="parent")
        child = mock_listener_factory(name="child")

        grandparent.add_child(parent)
        parent.add_child(child)

        state = grandparent.__getstate__()

        # Check nested structure
        assert 'parent' in state['_children']
        assert 'child' in state['_children']['parent']['_children']
        assert state['_children']['parent']['_children']['child']['name'] == 'child'

