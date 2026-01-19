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
    """Tests for Listener lifecycle management (start/stop/restart).

    Lifecycle flow:
    1. Create Listener -> state = STOPPED
    2. start() -> state = STARTING (if was STOPPED)
    3. tick() -> on_start() called, state = RUNNING
    """

    @pytest.mark.asyncio
    async def test_initial_state_is_stopped(self, mock_listener):
        """Listener should start in STOPPED state."""
        assert mock_listener.state == ListenerState.STOPPED
        assert mock_listener.healthy is False
        assert mock_listener.enabled is True

    @pytest.mark.asyncio
    async def test_start_transitions_to_starting(self, mock_listener):
        """start() should transition state from STOPPED to STARTING."""
        assert mock_listener.state == ListenerState.STOPPED

        await mock_listener.start(recursive=False)

        assert mock_listener.state == ListenerState.STARTING

    @pytest.mark.asyncio
    async def test_tick_transitions_to_running(self, mock_listener):
        """tick() should transition from STARTING to RUNNING and call on_start."""
        await mock_listener.start(recursive=False)
        assert mock_listener.state == ListenerState.STARTING

        await mock_listener.tick()

        assert mock_listener.state == ListenerState.RUNNING
        mock_listener._on_start_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_start_is_called(self, mock_listener_factory):
        """on_start should be called during tick after start."""
        on_start = AsyncMock()
        listener = mock_listener_factory(on_start_fn=on_start)

        await listener.start(recursive=False)
        await listener.tick()

        on_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_transitions_to_stopped(self, mock_listener):
        """stop() should transition state from RUNNING to STOPPED."""
        await mock_listener.start(recursive=False)
        await mock_listener.tick()  # STARTING -> RUNNING
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
        await listener.tick()  # STARTING -> RUNNING
        await listener.stop(recursive=False)

        on_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_restart_stops_and_starts(self, mock_listener):
        """restart() should stop then start the listener."""
        await mock_listener.start(recursive=False)
        await mock_listener.tick()  # STARTING -> RUNNING

        await mock_listener.restart(recursive=False)
        await mock_listener.tick()  # STARTING -> RUNNING again

        assert mock_listener.state == ListenerState.RUNNING
        mock_listener._on_stop_fn.assert_awaited_once()
        assert mock_listener._on_start_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_ready_property(self, mock_listener):
        """ready should be True only when enabled, healthy, and running."""
        # Initially not ready (STOPPED state, not healthy)
        assert mock_listener.ready is False

        await mock_listener.start(recursive=False)
        await mock_listener.tick()  # STARTING -> RUNNING (calls on_start)
        await mock_listener.tick()  # RUNNING, on_tick sets healthy=True
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
        await mock_listener.tick()  # STARTING -> RUNNING

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
        """start(recursive=True) should start all children to STARTING state."""
        parent = mock_listener_factory(name="parent")
        child1 = mock_listener_factory(name="child1")
        child2 = mock_listener_factory(name="child2")

        parent.add_child(child1)
        parent.add_child(child2)

        await parent.start(recursive=True)

        # start() transitions to STARTING, tick() is needed for RUNNING
        assert parent.state == ListenerState.STARTING
        assert child1.state == ListenerState.STARTING
        assert child2.state == ListenerState.STARTING

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
    async def test_background_task_initially_none(self, mock_listener):
        """Background task should be None initially."""
        assert mock_listener._background_task is None

    @pytest.mark.asyncio
    async def test_start_does_not_create_background_task(self, mock_listener):
        """start() does not automatically create a background task.

        Background task creation is handled externally by whoever runs the listener loop.
        """
        await mock_listener.start(recursive=False)

        # start() only sets state to STARTING, doesn't create background task
        assert mock_listener._background_task is None

    @pytest.mark.asyncio
    async def test_stop_clears_background_task(self, mock_listener):
        """stop() should clear the background task if one exists."""
        await mock_listener.start(recursive=False)
        await mock_listener.tick()  # STARTING -> RUNNING

        await mock_listener.stop(recursive=False)

        # After stop, background task should be None
        assert mock_listener._background_task is None


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
        # Initial state is STOPPED
        assert mock_listener.log_state_dict['state'] == ListenerState.STOPPED
        assert mock_listener.log_state_dict['ready'] is False

        await mock_listener.start(recursive=False)
        await mock_listener.tick()  # STARTING -> RUNNING (calls on_start)
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


# ============================================================
# 类索引功能测试
# ============================================================

from tests.conftest import (
    MockExecutor,
    MockMarketExecutor,
    MockLimitExecutor,
    MockStrategy,
    MockTrendStrategy,
    MockMeanReversionStrategy,
)


class TestClassIndexBasic:
    """Tests for basic class index functionality."""

    def test_add_child_registers_to_index(self):
        """add_child should register the child to class index."""
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")

        root.add_child(executor)

        assert MockMarketExecutor in root._class_index
        assert len(root._class_index[MockMarketExecutor]) == 1

    def test_add_child_registers_parent_classes(self):
        """add_child should also register parent classes in MRO."""
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")

        root.add_child(executor)

        # Should be registered under both MockMarketExecutor and MockExecutor
        assert MockMarketExecutor in root._class_index
        assert MockExecutor in root._class_index

    def test_remove_child_unregisters_from_index(self):
        """remove_child should unregister from class index."""
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")

        root.add_child(executor)
        root.remove_child("executor")

        assert MockMarketExecutor not in root._class_index
        assert MockExecutor not in root._class_index

    def test_class_index_tracks_depth(self):
        """Class index should track the correct depth."""
        root = MockListener(name="root")
        level1 = MockListener(name="level1")
        level2 = MockMarketExecutor(name="level2")

        root.add_child(level1)
        level1.add_child(level2)

        # level2 is at depth 2 from root's perspective
        # But when added via level1, it should be registered at depth 2
        entries = root._class_index.get(MockMarketExecutor, [])
        assert len(entries) == 1
        _, depth = entries[0]
        assert depth == 2


class TestFindChildByClass:
    """Tests for find_child_by_class method."""

    def test_find_child_returns_first_match(self):
        """find_child_by_class should return the first matching child."""
        root = MockListener(name="root")
        executor1 = MockMarketExecutor(name="executor1")
        executor2 = MockMarketExecutor(name="executor2")

        root.add_child(executor1)
        root.add_child(executor2)

        result = root.find_child_by_class(MockMarketExecutor)

        assert result is not None
        assert isinstance(result, MockMarketExecutor)

    def test_find_child_returns_none_when_not_found(self):
        """find_child_by_class should return None when not found."""
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")

        root.add_child(executor)

        result = root.find_child_by_class(MockStrategy)

        assert result is None

    def test_find_child_with_parent_class(self):
        """find_child_by_class should find by parent class."""
        root = MockListener(name="root")
        market_executor = MockMarketExecutor(name="market_executor")

        root.add_child(market_executor)

        # Should find MockMarketExecutor when searching for MockExecutor
        result = root.find_child_by_class(MockExecutor)

        assert result is market_executor

    def test_find_child_returns_shallowest(self):
        """find_child_by_class should return the shallowest match."""
        root = MockListener(name="root")
        level1 = MockListener(name="level1")
        shallow_executor = MockMarketExecutor(name="shallow")
        deep_executor = MockMarketExecutor(name="deep")

        root.add_child(shallow_executor)
        root.add_child(level1)
        level1.add_child(deep_executor)

        result = root.find_child_by_class(MockMarketExecutor)

        # Should return the shallower one (depth 1)
        assert result.name == "shallow"


class TestFindChildrenByClass:
    """Tests for find_children_by_class method."""

    def test_find_children_returns_all_matches(self):
        """find_children_by_class should return all matching children."""
        root = MockListener(name="root")
        executor1 = MockMarketExecutor(name="executor1")
        executor2 = MockMarketExecutor(name="executor2")
        strategy = MockTrendStrategy(name="strategy")

        root.add_child(executor1)
        root.add_child(executor2)
        root.add_child(strategy)

        result = root.find_children_by_class(MockMarketExecutor)

        assert len(result) == 2
        assert all(isinstance(r, MockMarketExecutor) for r in result)

    def test_find_children_returns_empty_list_when_not_found(self):
        """find_children_by_class should return empty list when not found."""
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")

        root.add_child(executor)

        result = root.find_children_by_class(MockStrategy)

        assert result == []

    def test_find_children_includes_subclasses(self):
        """find_children_by_class should include subclass instances."""
        root = MockListener(name="root")
        market_executor = MockMarketExecutor(name="market")
        limit_executor = MockLimitExecutor(name="limit")

        root.add_child(market_executor)
        root.add_child(limit_executor)

        # Search for parent class should find both
        result = root.find_children_by_class(MockExecutor)

        assert len(result) == 2

    def test_find_children_sorted_by_depth(self):
        """find_children_by_class should return results sorted by depth."""
        root = MockListener(name="root")
        level1 = MockListener(name="level1")
        shallow = MockMarketExecutor(name="shallow")
        deep = MockMarketExecutor(name="deep")

        root.add_child(level1)
        level1.add_child(deep)
        root.add_child(shallow)

        result = root.find_children_by_class(MockMarketExecutor)

        # Shallow (depth 1) should come before deep (depth 2)
        assert result[0].name == "shallow"
        assert result[1].name == "deep"


class TestFindChildrenByClassAtDepth:
    """Tests for find_children_by_class_at_depth method."""

    def test_find_at_specific_depth(self):
        """find_children_by_class_at_depth should filter by depth."""
        root = MockListener(name="root")
        level1 = MockListener(name="level1")
        shallow = MockMarketExecutor(name="shallow")
        deep = MockMarketExecutor(name="deep")

        root.add_child(shallow)  # depth 1
        root.add_child(level1)
        level1.add_child(deep)   # depth 2

        result_depth1 = root.find_children_by_class_at_depth(MockMarketExecutor, 1)
        result_depth2 = root.find_children_by_class_at_depth(MockMarketExecutor, 2)

        assert len(result_depth1) == 1
        assert result_depth1[0].name == "shallow"

        assert len(result_depth2) == 1
        assert result_depth2[0].name == "deep"

    def test_find_at_depth_returns_empty_when_no_match(self):
        """find_children_by_class_at_depth returns empty list if no match at depth."""
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")

        root.add_child(executor)

        # No executors at depth 2
        result = root.find_children_by_class_at_depth(MockMarketExecutor, 2)

        assert result == []


class TestClassIndexWithNestedChildren:
    """Tests for class index with deeply nested children."""

    def test_add_child_with_existing_children(self):
        """Adding a child with its own children should register all."""
        root = MockListener(name="root")
        parent = MockListener(name="parent")
        child = MockMarketExecutor(name="child")
        grandchild = MockTrendStrategy(name="grandchild")

        # Build the subtree first
        child.add_child(grandchild)
        parent.add_child(child)

        # Then add to root
        root.add_child(parent)

        # All should be registered
        assert MockMarketExecutor in root._class_index
        assert MockTrendStrategy in root._class_index

    def test_remove_child_with_existing_children(self):
        """Removing a child should unregister all its descendants."""
        root = MockListener(name="root")
        parent = MockListener(name="parent")
        child = MockMarketExecutor(name="child")
        grandchild = MockTrendStrategy(name="grandchild")

        child.add_child(grandchild)
        parent.add_child(child)
        root.add_child(parent)

        # Remove parent (should remove child and grandchild too)
        root.remove_child("parent")

        assert MockMarketExecutor not in root._class_index
        assert MockTrendStrategy not in root._class_index


class TestClassIndexWeakReferences:
    """Tests for weak reference handling in class index."""

    def test_find_cleans_up_dead_references(self):
        """find methods should clean up dead weak references."""
        root = MockListener(name="root")

        # Create executor and add it
        executor = MockMarketExecutor(name="executor")
        root.add_child(executor)

        # Verify it's in the index
        assert root.find_child_by_class(MockMarketExecutor) is executor

        # Remove the child (simulating it being garbage collected)
        root.remove_child("executor")

        # Should return None now
        result = root.find_child_by_class(MockMarketExecutor)
        assert result is None


class TestClassIndexSerialization:
    """Tests for class index with pickle serialization."""

    def test_class_index_excluded_from_pickle(self):
        """_class_index should not be pickled."""
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")
        root.add_child(executor)

        state = root.__getstate__()

        assert '_class_index' not in state

    def test_class_index_rebuilt_on_setstate(self):
        """
        Class index is initialized empty after __setstate__.

        NOTE: Children are NOT restored from pickle (per cache dict pattern).
        They should be rebuilt via get_or_create when reconstructing the tree.
        This test verifies that __setstate__ properly initializes an empty
        class index (ready for children to be added later).
        """
        root = MockListener(name="root")
        executor = MockMarketExecutor(name="executor")
        root.add_child(executor)

        state = root.__getstate__()

        # Create new root and restore
        new_root = MockListener(name="new_root")
        new_root.__setstate__(state)

        # Class index should be initialized but empty (no children restored)
        # Children are rebuilt via get_or_create, not from pickle
        result = new_root.find_child_by_class(MockMarketExecutor)
        assert result is None  # No children restored from pickle

        # Verify we can add children and they appear in class index
        new_executor = MockMarketExecutor(name="new_executor")
        new_root.add_child(new_executor)
        result = new_root.find_child_by_class(MockMarketExecutor)
        assert result is not None
        assert result.name == "new_executor"
