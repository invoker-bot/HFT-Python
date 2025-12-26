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
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Optional, Coroutine, Iterator
from rich.console import Console
from humanfriendly import format_timespan
from tenacity import retry, stop_after_attempt, wait_fixed, AsyncRetrying, RetryCallState


class ListenerState(StrEnum):
    """Listener 状态枚举"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FINISHED = "finished"  # for tasks that complete successfully, 说明这个可以正常退出
    ERROR = "error"


# constants
RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 5


class Listener(ABC):
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
    __pickle_exclude__ = ("_parent", "_background_task", "_alock")

    def __init__(self, name: Optional[str] = None, interval: float = 1.0):
        """
        Initialize listener.
        """
        # Initialize EventEmitter first
        if name is None:
            name = f"{self.__class__.__name__}"
        self.name = name
        self.interval = interval

        # Initialize Listener-specific attributes
        self._parent: Optional[weakref.ReferenceType['Listener']] = None
        self._children: dict[str, 'Listener'] = {}
        self._background_task: Optional[asyncio.Task] = None

        # Internal state
        self._enabled = True
        self._state: ListenerState = ListenerState.STARTING  # build-in state
        self._healthy = False
        self._alock = asyncio.Lock()

        self.start_time = self.current_time

    def __getstate__(self) -> dict:
        """
        Get serializable state for pickling.

        Excludes non-serializable objects (locks, tasks, weakrefs, logger).
        Recursively includes all children's state.
        """
        return {k: v for k, v in self.__dict__.items() if k not in self.__pickle_exclude__}

    def __setstate__(self, state: dict):
        """
        Restore state from pickled data.

        Reinitializes non-serializable objects (locks, tasks, weakrefs).
        Recursively restores all children's state.
        """
        # Restore basic attributes
        self.__dict__.update(state)

        # Reinitialize non-serializable objects
        self._parent = None
        self._alock = asyncio.Lock()
        self._background_task = None

        # Restore children (note: subclasses must handle actual child reconstruction)
        for child in self._children.values():
            child.parent = self

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

    async def loop_coro_in_background(self, coro: Coroutine, interval: float = 0.001,
                                      finalizer: Optional[Coroutine] = None, params: Optional[dict] = None):
        if params is None:
            params = {}
        while True:
            start = time.time()
            should_finalize = False
            try:
                if await coro(**params):
                    should_finalize = True  # Exit if the coroutine signals completion: return True
            except asyncio.CancelledError:
                should_finalize = True  # Allow task to be cancelled gracefully
            except Exception as e:
                self.logger.exception("Exception in background task: %s", str(e))
            finally:
                if not should_finalize:
                    await asyncio.sleep(max(0, interval - (time.time() - start)))
            if should_finalize:
                if finalizer is not None:
                    await finalizer()
                break

    def update_background(self):
        bt = self._background_task
        if bt is None or bt.done():  # no existing task or already completed
            self._background_task = asyncio.create_task(
                self.loop_coro_in_background(self.tick, self.interval, self.stop),
                name=f"{self.name}-background-task"
            )

    def delete_background(self):
        bt = self._background_task
        if bt is not None:
            bt.cancel()
            # wait for task to be cancelled and to execute stop finalizer
            self._background_task = None

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
    def healthy(self) -> bool:
        """Get the health status of this listener."""
        return self._healthy

    @property
    def ready(self) -> bool:
        """
        Check if the listener is ready.

        Override this method to implement custom readiness checks.

        Returns:
            True if ready, False otherwise
        """
        return self.enabled and self.healthy and self._state == ListenerState.RUNNING

    async def on_health_check(self):
        return True

    async def on_health_check_error(self, retry_state: RetryCallState):
        """this is called after each failed health check attempt"""
        self.logger.warning("Health check attempt %d failed", retry_state.attempt_number - 1)

    async def health_check(self, recursive: bool = True):
        if recursive:
            for child in list(self.children.values()):
                await child.health_check(True)
                if child.state == ListenerState.FINISHED:
                    child.delete_background()
                    self.remove_child(child.name)
        try:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT_SECONDS),
                                               reraise=True, after=self.on_health_check_error):
                with attempt:
                    result = await self.on_health_check()
                    if not result:
                        raise ValueError("returned unhealthy status")
            if self.enabled:
                self.update_background()
            self._healthy = True
        except Exception as e:
            self._healthy = False
            self.logger.error("Health check failed: %s", e, exc_info=True)

    @abstractmethod
    async def on_tick(self) -> bool:  # return True to stop the loop
        """
        Called on each tick.
        """

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT_SECONDS), reraise=True)
    async def __tick_internal(self) -> bool:
        try:
            match self._state:
                case ListenerState.STARTING:
                    if self.enabled:
                        await self.on_start()
                        self.start_time = self.current_time
                        self._state = ListenerState.RUNNING
                    else:
                        self._state = ListenerState.STOPPED
                case ListenerState.RUNNING:
                    if self.enabled:
                        try:
                            result = await self.on_tick()
                            self._healthy = True
                            if result:  # task signaled completion
                                self._state = ListenerState.STOPPING
                                await self.on_stop()
                                self._state = ListenerState.FINISHED
                        except asyncio.CancelledError:
                            self._state = ListenerState.STOPPING
                    else:
                        self._state = ListenerState.STOPPING
                    if self._state == ListenerState.STOPPING:  # disabled or stopping
                        await self.on_stop()
                        self._state = ListenerState.STOPPED
                case ListenerState.STOPPING:
                    await self.on_stop()
                    self._state = ListenerState.STOPPED
                case ListenerState.STOPPED:
                    if self.enabled:
                        self._state = ListenerState.STARTING
                # ListenerState.FINISHED | ListenerState.ERROR:
                #     if self.enabled
        except Exception as e:
            self._healthy = False
            self.logger.error("Error during tick execution: %s", e, exc_info=True)

    async def tick(self):
        async with self._alock:
            return await self.__tick_internal()

    async def start(self, recursive: bool = True):
        async with self._alock:
            self.enabled = True
            if self._state == ListenerState.STOPPED:
                self._state = ListenerState.STARTING
            else:
                self.logger.warning("Start called but listener not stopped")
        await self.tick()
        self.update_background()
        if recursive:
            for child in list(self.children.values()):
                await child.start(True)
            # await self.__start_internal(recursive)

    async def on_start(self):
        """...
        """

    async def stop(self, recursive: bool = True):
        if recursive:
            for child in list(self.children.values()):
                await child.stop(True)
        async with self._alock:
            self.enabled = False
            if self._state == ListenerState.STARTING:
                self._state = ListenerState.STOPPED
            elif self._state == ListenerState.RUNNING:
                self._state = ListenerState.STOPPING
            else:
                self.logger.warning("Stop called but listener not running")
        self.delete_background()
        await self.tick()

    async def on_stop(self):
        """...
        """

    async def restart(self, recursive: bool = True):
        await self.stop(recursive)
        assert self._state == ListenerState.STOPPED, "Listener must be stopped before restarting"
        await self.start(recursive)

    @property
    def logger_name(self) -> str:
        """Return a short title for logging purposes."""
        return self.name

    @cached_property
    def logger(self) -> logging.Logger:
        """Return a logger instance for this listener."""
        return logging.getLogger(self.logger_name)

    def log_state(self, console: Console, recursive: bool = True):
        """Log the current state of the listener to the provided console."""
        if recursive:
            for child in list(self.children.values()):
                child.log_state(console, True)

    @property
    def log_state_dict(self) -> dict:
        return {
            'enabled': self.enabled,
            'ready': self.ready,
            'healthy': self.healthy,
            'state': self.state,
            'uptime': self.uptime,
        }

    @property
    def id(self) -> str:
        """Return a unique identifier for this listener instance."""
        return f"{self.__class__.__name__}-{id(self)}"

    def __iter__(self) -> Iterator['Listener']:
        """Iterate over self and all descendant listeners (depth-first)."""
        for child in self.children.values():
            yield from child
        yield self
