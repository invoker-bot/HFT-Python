"""
Trade Core Listener Base Class

监听器基类，用于实现交易策略、风控、监控等功能。
"""
import time
import uuid
import asyncio
import logging
import weakref
from datetime import datetime
from functools import cached_property
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Optional, Coroutine, Iterator
from rich.console import Console
from humanfriendly import format_timespan
from tenacity import stop_after_attempt, wait_fixed, AsyncRetrying, RetryCallState
from pyee.asyncio import AsyncIOEventEmitter


class ListenerState(StrEnum):
    """Listener 状态枚举"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# constants
RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 5


class Listener(AsyncIOEventEmitter, ABC):
    """
    交易核心监听器基类

    监听器是一个观察者，它会在每个 tick 时被调用。
    继承自 AsyncIOEventEmitter，支持事件驱动的组件间通信。

    用途：
    - 交易策略：实现具体的交易逻辑
    - 风险控制：监控仓位、风险指标
    - 数据记录：记录交易、市场数据
    - 通知系统：发送告警、通知

    事件系统：
    - 支持发射事件：await self.emit('event_name', data)
    - 支持监听事件：@self.on('event_name')
    - 可用于 Listener 之间的通信
    """

    def __init__(self, name: Optional[str] = None, interval: float = 1.0):
        """
        Initialize listener.
        """
        # Initialize EventEmitter first
        super().__init__()
        if name is None:
            name = f"{self.__class__.__name__}-{str(uuid.uuid4())}"
        self.name = name
        # Initialize Listener-specific attributes
        self._parent: Optional[weakref.ReferenceType['Listener']] = None
        self._children: dict[str, 'Listener'] = {}
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._enabled = True
        self._state: ListenerState = ListenerState.STOPPED
        self._health = False
        self._alock = asyncio.Lock()

        self.start_time = time.time()
        self.interval = interval

    @property
    def current_time(self):
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

    async def wrap_loop_fn_in_background(self, coro: Coroutine, interval: float = 0.001,
                                         finalizer: Optional[Coroutine] = None, params: Optional[dict] = None):
        if params is None:
            params = {}
        while True:
            start = time.time()
            should_finalize = False
            try:
                if not self.enabled:
                    should_finalize = True
                elif self.ready:
                    if await coro(**params):
                        should_finalize = True  # Exit if the coroutine signals completion
            except asyncio.CancelledError:
                should_finalize = True  # Allow task to be cancelled gracefully
            except Exception as e:
                self.logger.exception("Exception in background task: %s", str(e))
            finally:
                if should_finalize:
                    if finalizer is not None:
                        await finalizer()
                await asyncio.sleep(max(0, interval - (time.time() - start)))
            if should_finalize:
                break

    def add_background_task(self, task_id: str, coro: Coroutine, interval: float = 0.001, 
                            finalizer: Optional[Coroutine] = None, params: Optional[dict] = None):
        if task_id not in self._background_tasks or self._background_tasks[task_id].done():
            self._background_tasks[task_id] = asyncio.create_task(
                self.wrap_loop_fn_in_background(coro, interval, finalizer, params)
            )

    def remove_background_task(self, task_id: str):
        if task_id in self._background_tasks:
            self._background_tasks[task_id].cancel()
            self._background_tasks.pop(task_id, None)

    @property
    def state(self) -> ListenerState:
        return self._state

    @property
    def root(self) -> 'Listener':
        """
        Get the Root instance this listener is attached to.
        """
        parent = self.parent
        if parent is None:
            return self
        return parent.root

    @property
    def parent(self) -> Optional['Listener']:
        """
        Get the Parent instance this listener is attached to.
        """
        if self._parent is None:
            return None
        return self._parent()

    @parent.setter
    def parent(self, parent: Optional['Listener']):
        """Set the Parent instance this listener is attached to."""
        if parent is None:
            self._parent = None
        else:
            self._parent = weakref.ref(parent)

    @property
    def children(self) -> dict[str, 'Listener']:
        """Get the Child listeners attached to this listener."""
        return self._children
    
    def add_child(self, child: 'Listener'):
        """Add a Child listener to this listener."""
        self._children[child.name] = child
        child.parent = self

    def remove_child(self, child_name: str):
        """Remove a Child listener from this listener by name."""
        if child_name in self._children:
            self._children[child_name].parent = None
            self._children.pop(child_name, None)

    @property
    def enabled(self) -> bool:
        """Check if this listener is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        self.logger.debug("enabled status set to %s", value)

    @property
    def health(self) -> bool:
        """Get the health status of this listener."""
        return self._health

    @property
    def ready(self) -> bool:
        """
        Check if the listener is ready.

        Override this method to implement custom readiness checks.

        Returns:
            True if ready, False otherwise
        """
        return self._enabled and self._state == ListenerState.RUNNING

    async def health_check_callback(self):
        if self.enabled:
            self.add_tick_task()
        return True

    async def health_check_after(self, retry_state: RetryCallState):
        """this is called after each failed health check attempt"""
        self.logger.debug("Health check attempt %d failed", retry_state.attempt_number - 1)
        self.emit("health_check_retry")

    async def health_check(self, children: bool = True):
        self.emit("health_check")
        if children:
            for child in list(self.children.values()):
                await child.health_check(True)
        try:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT_SECONDS), 
                                               reraise=True, after=self.health_check_after):
                with attempt:
                    result = await self.health_check_callback()
                    if not result:
                        raise ValueError("returned unhealthy status")
            self._health = True
        except Exception as e:
            self._health = False
            self.logger.error("Health check failed: %s", e, exc_info=True)
            self.emit("unhealthy")

    def add_tick_task(self):
        self.add_background_task("tick", self.tick, self.interval, self.stop)

    @abstractmethod
    async def tick_callback(self) -> bool:  # return True to stop the loop
        """
        Called on each tick.
        """

    async def tick(self):
        async with self._alock:
            if self.enabled:
                await self._start_internal()
                try:
                    async for attempt in AsyncRetrying(stop=stop_after_attempt(RETRY_ATTEMPTS),
                                                       wait=wait_fixed(RETRY_WAIT_SECONDS), reraise=True):
                        with attempt:
                            await self.tick_callback()
                    self._health = True
                except Exception as e:
                    self._health = False
                    self.logger.error("Error during tick execution: %s", e, exc_info=True)
            else:
                await self._stop_internal()

    async def _start_internal(self, children: bool = False, background: bool = False):
        """Internal start logic without lock - must be called with _alock held."""
        if self._state == ListenerState.STOPPED:
            self._state = ListenerState.STARTING
            if children:
                for child in list(self.children.values()):
                    await child.start(True, background)
            try:
                await self.start_callback()
                self._state = ListenerState.RUNNING
                self.start_time = time.time()
                if background:
                    self.add_tick_task()
                self.emit("started")
            except Exception as e:
                self._state = ListenerState.ERROR
                self.logger.error("Error during start: %s", e, exc_info=True)


    async def start(self, children: bool = True, background: bool = False):
        async with self._alock:
            await self._start_internal(children, background)

    async def start_callback(self):
        """...
        """

    async def _stop_internal(self, children: bool = False):
        """Internal stop logic without lock - must be called with _alock held."""
        if self._state == ListenerState.RUNNING:
            self._state = ListenerState.STOPPING
            if children:
                for child in list(self.children.values()):
                    await child.stop(True)
            for task_id in list(self._background_tasks.keys()):
                self.remove_background_task(task_id)
            try:
                async for attempt in AsyncRetrying(stop=stop_after_attempt(RETRY_ATTEMPTS),
                                                   wait=wait_fixed(RETRY_WAIT_SECONDS), reraise=True):
                    with attempt:
                        await self.stop_callback()
                self._state = ListenerState.STOPPED
                self.emit("stopped")
            except Exception as e:
                self._state = ListenerState.ERROR
                self.logger.error("Error during stop: %s", e, exc_info=True)

    async def stop(self, children: bool = True):
        async with self._alock:
            await self._stop_internal(children)

    async def stop_callback(self):
        """...
        """

    async def restart(self, children: bool = True, background: bool = True):
        await self.stop(children)
        async with self._alock:
            self._state = ListenerState.STOPPED
        await self.start(children, background)

    @property
    def logger_name(self) -> str:
        """Return a short title for logging purposes."""
        return self.__class__.__name__

    def log_state(self, console: Console, recursive: bool = True):
        """Log the current state of the listener to the provided console."""
        if recursive:
            for child in list(self.children.values()):
                child.log_state(console, True)

    @cached_property
    def logger(self) -> logging.Logger:
        """Return a logger instance for this listener."""
        return logging.getLogger(self.logger_name)

    @property
    def state_dict(self) -> dict:
        return {
            'enabled': self.enabled,
            'ready': self.ready,
            'healthy': self.health,
            'state': self.state,
            'parent': self.parent.logger_name if self.parent else None,
            'children': len(self.children),
            'task_count': len(self._background_tasks),
            'uptime': self.uptime,
        }

    @property
    def id(self) -> str:
        """Return a unique identifier for this listener instance."""
        return f"{self.__class__.__name__}-{id(self)}"

    def __iter__(self) -> Iterator['Listener']:
        """Iterate over self and all descendant listeners (depth-first)."""
        yield self
        for child in self.children.values():
            yield from child

    def __getstate__(self) -> dict:
        """
        Get serializable state for pickling.

        Excludes non-serializable objects (locks, tasks, weakrefs, logger).
        Recursively includes all children's state.
        """
        return {
            'name': self.name,
            'interval': self.interval,
            'start_time': self.start_time,
            '_enabled': self._enabled,
            '_state': self._state,
            '_health': self._health,
            '_children': {name: child.__getstate__() for name, child in self._children.items()},
        }

    def __setstate__(self, state: dict):
        """
        Restore state from pickled data.

        Reinitializes non-serializable objects (locks, tasks, weakrefs).
        Recursively restores all children's state.
        """
        # Restore basic attributes
        self.name = state['name']
        self.interval = state['interval']
        self.start_time = state['start_time']
        self._enabled = state['_enabled']
        self._state = state['_state']
        self._health = state['_health']

        # Reinitialize non-serializable objects
        self._parent = None
        self._background_tasks = {}
        self._alock = asyncio.Lock()

        # Restore children (note: subclasses must handle actual child reconstruction)
        self._children = state.get('_children', {})
