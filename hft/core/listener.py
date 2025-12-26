"""
Trade Core Listener Base Class

监听器基类，用于实现交易策略、风控、监控等功能。
"""
import time
import asyncio
import logging
import weakref
from datetime import datetime
from functools import cached_property
from enum import StrEnum
from typing import Optional, Callable, Coroutine, Iterator, Any
from rich.console import Console
from humanfriendly import format_timespan
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed, RetryCallState


class ListenerState(StrEnum):
    """Listener 状态枚举"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FINISHED = "finished"  # for tasks that complete successfully
    ERROR = "error"


# constants
RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 0.5


class Listener:
    """
    交易核心监听器基类

    监听器是一个观察者，它会在每个 tick 时被调用。

    用途：
    - 交易策略：实现具体的交易逻辑
    - 风险控制：监控仓位、风险指标
    - 数据记录：记录交易、市场数据
    - 通知系统：发送告警、通知
    - 可用于 Listener 之间的通信
    """
    __pickle_exclude__ = ("_parent", "_background_tasks", "_alock", "_event_handlers")

    def __init__(self, name: Optional[str] = None, interval: float = 1.0):
        """
        Initialize listener.
        """
        if name is None:
            name = f"{self.__class__.__name__}"
        self.name = name
        self.interval = interval

        # Initialize Listener-specific attributes
        self._parent: Optional[weakref.ReferenceType['Listener']] = None
        self._children: dict[str, 'Listener'] = {}
        self._background_tasks: dict[str, asyncio.Task] = {}

        # Internal state
        self._enabled = True
        self._state: ListenerState = ListenerState.STOPPED
        self._health = False
        self._alock = asyncio.Lock()

        # Event handlers
        self._event_handlers: dict[str, list[Callable]] = {}

        self.start_time = self.current_time

    def __getstate__(self) -> dict:
        """
        Get serializable state for pickling.

        Excludes non-serializable objects (locks, tasks, weakrefs, logger).
        Recursively includes all children's state.
        """
        state = {k: v for k, v in self.__dict__.items() if k not in self.__pickle_exclude__}
        # Recursively serialize children
        state['_children'] = {name: child.__getstate__() for name, child in self._children.items()}
        return state

    def __setstate__(self, state: dict):
        """
        Restore state from pickled data.

        Reinitializes non-serializable objects (locks, tasks, weakrefs).
        """
        self.__dict__.update(state)

        # Reinitialize non-serializable objects
        self._parent = None
        self._alock = asyncio.Lock()
        self._background_tasks = {}
        self._event_handlers = {}

        # Restore children's parent reference
        for child in self._children.values():
            if isinstance(child, dict):
                # Child was serialized as dict, needs reconstruction
                pass
            else:
                child._parent = weakref.ref(self)

    @property
    def current_time(self) -> float:
        return time.time()

    @property
    def uptime(self) -> float:
        if self._state != ListenerState.RUNNING:
            return 0.0
        return max(0.0, self.current_time - self.start_time)

    def to_date_string(self, timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def to_duration_string(self, seconds: float) -> str:
        return format_timespan(seconds)

    # ==================== Event System ====================

    def on(self, event: str) -> Callable:
        """Decorator to register an event handler."""
        def decorator(func: Callable) -> Callable:
            if event not in self._event_handlers:
                self._event_handlers[event] = []
            self._event_handlers[event].append(func)
            return func
        return decorator

    def emit(self, event: str, *args, **kwargs) -> None:
        """Emit an event to all registered handlers."""
        if event in self._event_handlers:
            for handler in self._event_handlers[event]:
                try:
                    handler(*args, **kwargs)
                except Exception as e:
                    self.logger.warning(f"Event handler error for '{event}': {e}")

    # ==================== State Properties ====================

    @property
    def state(self) -> ListenerState:
        return self._state

    @property
    def enabled(self) -> bool:
        """Check if this listener is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        self.logger.debug("enabled status set to %s", value)

    @property
    def healthy(self) -> bool:
        """Get the health status of this listener."""
        return self._health

    @property
    def ready(self) -> bool:
        """
        Check if the listener is ready.

        Returns:
            True if enabled and running, False otherwise
        """
        return self.enabled and self._state == ListenerState.RUNNING

    @property
    def state_dict(self) -> dict:
        """Return current state as a dictionary."""
        return {
            'enabled': self.enabled,
            'ready': self.ready,
            'healthy': self.healthy,
            'state': self.state,
            'parent': self.parent.name if self.parent else None,
            'children': len(self._children),
            'task_count': len(self._background_tasks),
            'uptime': self.uptime,
        }

    # ==================== Parent-Child Relationships ====================

    @property
    def root(self) -> 'Listener':
        """Get the root Listener instance."""
        parent = self.parent
        if parent is None:
            return self
        return parent.root

    @property
    def parent(self) -> Optional['Listener']:
        """Get the parent Listener instance."""
        if self._parent is None:
            return None
        return self._parent()

    @parent.setter
    def parent(self, parent: Optional['Listener']) -> None:
        """Set the parent Listener instance."""
        if parent is None:
            self._parent = None
        else:
            self._parent = weakref.ref(parent)

    @property
    def children(self) -> dict[str, 'Listener']:
        """Get the child listeners."""
        return self._children

    def add_child(self, child: 'Listener') -> None:
        """Add a child listener."""
        self._children[child.name] = child
        child.parent = self

    def remove_child(self, child_name: str) -> None:
        """Remove a child listener by name."""
        if child_name in self._children:
            self._children[child_name].parent = None
            self._children.pop(child_name, None)

    def __iter__(self) -> Iterator['Listener']:
        """Iterate over self and all descendant listeners (depth-first)."""
        for child in self.children.values():
            yield from child
        yield self

    # ==================== Background Tasks ====================

    def add_background_task(
        self,
        name: str,
        coro_func: Callable[[], Coroutine],
        interval: float = 1.0,
        finalizer: Optional[Callable[[], Coroutine]] = None,
    ) -> None:
        """Add a background task that runs periodically."""
        if name in self._background_tasks:
            self.remove_background_task(name)

        async def task_loop():
            while True:
                start = time.time()
                should_stop = False
                try:
                    result = await coro_func()
                    if result is True:  # Signal to stop
                        should_stop = True
                except asyncio.CancelledError:
                    should_stop = True
                except Exception as e:
                    self.logger.exception("Exception in background task '%s': %s", name, e)

                if should_stop:
                    if finalizer is not None:
                        try:
                            await finalizer()
                        except Exception as e:
                            self.logger.exception("Exception in finalizer for '%s': %s", name, e)
                    break

                elapsed = time.time() - start
                await asyncio.sleep(max(0, interval - elapsed))

        task = asyncio.create_task(task_loop(), name=f"{self.name}-{name}")
        self._background_tasks[name] = task

    def remove_background_task(self, name: str) -> None:
        """Remove and cancel a background task."""
        if name in self._background_tasks:
            task = self._background_tasks.pop(name)
            task.cancel()

    def remove_all_background_tasks(self) -> None:
        """Remove all background tasks."""
        for name in list(self._background_tasks.keys()):
            self.remove_background_task(name)

    # ==================== Lifecycle Callbacks ====================

    async def start_callback(self) -> None:
        """Called when the listener starts. Override in subclasses."""
        pass

    async def stop_callback(self) -> None:
        """Called when the listener stops. Override in subclasses."""
        pass

    async def tick_callback(self) -> Optional[bool]:
        """
        Called on each tick. Override in subclasses.

        Returns:
            True to signal completion (stop the listener), None/False to continue
        """
        pass

    async def health_check_callback(self) -> bool:
        """
        Called during health check. Override in subclasses.

        Returns:
            True if healthy, False otherwise
        """
        return True

    async def health_check_after(self, retry_state: RetryCallState) -> None:
        """Called after each health check attempt (including failures)."""
        if retry_state.attempt_number > 1:
            self.logger.warning("Health check attempt %d failed", retry_state.attempt_number - 1)

    # ==================== Lifecycle Methods ====================

    async def start(self, children: bool = True, background: bool = False) -> None:
        """
        Start the listener.

        Args:
            children: Whether to start child listeners
            background: Whether to start background tick task
        """
        async with self._alock:
            if self._state == ListenerState.RUNNING:
                return  # Already running

            if self._state != ListenerState.STOPPED:
                self.logger.warning("Start called but listener in state %s", self._state)
                return

            self._state = ListenerState.STARTING
            try:
                await self.start_callback()
                self.start_time = self.current_time
                self._state = ListenerState.RUNNING
                self._health = True
                self.emit('started')
            except Exception as e:
                self._state = ListenerState.ERROR
                self.logger.error("Error during start: %s", e, exc_info=True)
                return

        if background:
            self.add_background_task("tick", self.tick, self.interval)

        if children:
            for child in list(self._children.values()):
                await child.start(children=True, background=background)

    async def stop(self, children: bool = True) -> None:
        """
        Stop the listener.

        Args:
            children: Whether to stop child listeners
        """
        if children:
            for child in list(self._children.values()):
                await child.stop(children=True)

        async with self._alock:
            if self._state == ListenerState.STOPPED:
                return  # Already stopped

            if self._state not in (ListenerState.RUNNING, ListenerState.STARTING):
                self.logger.warning("Stop called but listener in state %s", self._state)
                return

            self._state = ListenerState.STOPPING
            try:
                await self.stop_callback()
                self._state = ListenerState.STOPPED
                self.emit('stopped')
            except Exception as e:
                self._state = ListenerState.ERROR
                self.logger.error("Error during stop: %s", e, exc_info=True)

        self.remove_all_background_tasks()

    async def restart(self, children: bool = True, background: bool = False) -> None:
        """Restart the listener."""
        await self.stop(children=children)
        await self.start(children=children, background=background)

    async def tick(self) -> Optional[bool]:
        """Execute a single tick."""
        if self._state != ListenerState.RUNNING:
            return None

        try:
            result = await self.tick_callback()
            self._health = True
            if result is True:
                # Task signaled completion
                async with self._alock:
                    self._state = ListenerState.STOPPING
                    await self.stop_callback()
                    self._state = ListenerState.FINISHED
                return True
        except Exception as e:
            self._health = False
            self.logger.error("Error during tick: %s", e, exc_info=True)

        return None

    async def health_check(self, children: bool = True) -> None:
        """
        Perform health check.

        Args:
            children: Whether to check child listeners
        """
        if children:
            for child in list(self._children.values()):
                await child.health_check(children=True)
                if child.state == ListenerState.FINISHED:
                    child.remove_all_background_tasks()
                    self.remove_child(child.name)

        self.emit('health_check')

        if self._state != ListenerState.RUNNING:
            return

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(RETRY_ATTEMPTS),
                wait=wait_fixed(RETRY_WAIT_SECONDS),
                reraise=True,
                after=self.health_check_after,
            ):
                with attempt:
                    result = await self.health_check_callback()
                    if not result:
                        raise ValueError("Health check returned unhealthy status")

            self._health = True
        except Exception as e:
            self._health = False
            self.emit('unhealthy')
            self.logger.error("Health check failed: %s", e, exc_info=True)

    # ==================== Logging ====================

    @property
    def logger_name(self) -> str:
        """Return a short title for logging purposes."""
        return self.name

    @cached_property
    def logger(self) -> logging.Logger:
        """Return a logger instance for this listener."""
        return logging.getLogger(self.logger_name)

    @property
    def id(self) -> str:
        """Return a unique identifier for this listener instance."""
        return f"{self.__class__.__name__}-{id(self)}"

    # ==================== Display ====================

    def print_tree(self, console: Optional[Console] = None, prefix: str = "", is_last: bool = True, depth: int = 0, max_depth: int = 10) -> None:
        """
        Print the listener tree in a directory-like format.

        Args:
            console: Rich console to print to
            prefix: Current line prefix
            is_last: Whether this is the last child
            depth: Current depth
            max_depth: Maximum depth to display
        """
        if console is None:
            console = Console()

        if depth > max_depth:
            return

        # Determine the connector
        if depth == 0:
            connector = "📦 "
        else:
            connector = "└── " if is_last else "├── "

        # State indicator
        if self._state == ListenerState.RUNNING:
            state_icon = "[green]●[/green]"
        elif self._state == ListenerState.STOPPED:
            state_icon = "[dim]○[/dim]"
        elif self._state == ListenerState.ERROR:
            state_icon = "[red]✗[/red]"
        else:
            state_icon = "[yellow]◐[/yellow]"

        # Health indicator
        health_icon = "[green]♥[/green]" if self._health else "[red]♡[/red]"

        # Print this node
        console.print(f"{prefix}{connector}{state_icon} {self.name} {health_icon}")

        # Prepare prefix for children
        if depth == 0:
            child_prefix = ""
        else:
            child_prefix = prefix + ("    " if is_last else "│   ")

        # Print children
        children_list = list(self._children.values())
        for i, child in enumerate(children_list):
            is_last_child = (i == len(children_list) - 1)
            child.print_tree(console, child_prefix, is_last_child, depth + 1, max_depth)
