"""
监听器基类模块

监听器基类，用于实现交易策略、风控、监控等功能。

核心概念：
- 状态机：STARTING -> RUNNING -> STOPPING -> STOPPED
- 生命周期回调：on_start(), on_tick(), on_stop(), on_health_check()
- 父子关系：支持树形结构，递归操作
- 后台任务：自动管理定时执行的后台任务
- 序列化：支持 pickle 持久化
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
from tenacity import retry, stop_after_attempt, wait_fixed, AsyncRetrying, RetryCallState, retry_if_not_exception_type


class ListenerState(StrEnum):
    """
    监听器状态枚举

    状态转换：
    STARTING -> RUNNING -> STOPPING -> STOPPED
                    |                      ^
                    v                      |
                 FINISHED (任务完成)        |
                    |                      |
                    +----------------------+
    """
    STARTING = "starting"   # 启动中
    RUNNING = "running"     # 运行中
    STOPPING = "stopping"   # 停止中
    STOPPED = "stopped"     # 已停止
    FINISHED = "finished"   # 任务完成（正常退出）
    ERROR = "error"         # 错误状态


# 常量
RETRY_ATTEMPTS = 3          # 重试次数
RETRY_WAIT_SECONDS = 5      # 重试等待时间（秒）


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
        初始化监听器

        Args:
            name: 监听器名称，默认使用类名
            interval: tick 间隔（秒）
        """
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
        self._state: ListenerState = ListenerState.STOPPED  # build-in state
        self._healthy = False
        self._alock = asyncio.Lock()

        self.start_time = self.current_time

    def __getstate__(self) -> dict:
        """
        获取可序列化的状态（用于 pickle）

        排除不可序列化的对象（锁、任务、弱引用）。
        """
        state = {k: v for k, v in self.__dict__.items() if k not in self.__pickle_exclude__}
        state.update(self.on_save())
        return state

    def __setstate__(self, state: dict):
        """
        从序列化数据恢复状态

        重新初始化不可序列化的对象（锁、任务、弱引用）。
        恢复子监听器的父引用。
        """
        # Restore basic attributes
        self.__dict__.update(state)

        # Reinitialize non-serializable objects
        self._parent = None
        self._alock = asyncio.Lock()
        self._background_task = None
        self._state = ListenerState.STOPPED
        # Restore children (note: subclasses must handle actual child reconstruction)
        for child in self._children.values():
            child.parent = self
        self.on_reload(state)

    def on_save(self):
        """子类可覆盖实现保存时的逻辑"""
        return {}

    def on_reload(self, state: dict):
        """子类可覆盖实现重新加载时的逻辑"""

    @property
    def current_time(self):
        """获取当前时间戳"""
        return time.time()

    @property
    def uptime(self) -> float:
        """获取运行时间（秒），非运行状态返回 0"""
        if self._state != ListenerState.RUNNING:
            return 0.0
        return max(0.0, self.current_time - self.start_time)

    def to_date_string(self, timestamp: float) -> str:
        """将时间戳转换为日期字符串"""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def to_duration_string(self, seconds: float) -> str:
        """将秒数转换为可读的时长字符串"""
        return format_timespan(seconds)

    async def loop_coro_in_background(self, coro: Coroutine, interval: float = 0.001,
                                      finalizer: Optional[Coroutine] = None, params: Optional[dict] = None):
        """
        在后台循环执行协程

        Args:
            coro: 要执行的协程
            interval: 执行间隔（秒）
            finalizer: 结束时执行的清理协程
            params: 传递给协程的参数
        """
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
        """创建或更新后台任务"""
        bt = self._background_task
        if bt is None or bt.done():  # 没有任务或已完成
            self._background_task = asyncio.create_task(
                self.loop_coro_in_background(self.tick, self.interval, self.stop),
                name=f"{self.name}-background-task"
            )

    async def delete_background(self):
        """取消后台任务"""
        bt = self._background_task
        if bt is not None:
            # try:
            if bt.cancel():
                try:
                    await bt
                except asyncio.CancelledError:
                    pass
            self._background_task = None

    @property
    def state(self) -> ListenerState:
        """获取当前状态"""
        return self._state

    @property
    def root(self) -> 'Listener':
        """获取根监听器（向上遍历到顶层）"""
        parent = self.parent
        if parent is None:
            return self
        return parent.root

    @property
    def parent(self) -> Optional['Listener']:
        """获取父监听器"""
        if self._parent is None:
            return None
        return self._parent()

    @parent.setter
    def parent(self, parent: Optional['Listener']):
        """设置父监听器"""
        if parent is None:
            self._parent = None
        else:
            self._parent = weakref.ref(parent)

    @property
    def children(self) -> dict[str, 'Listener']:
        """获取子监听器字典"""
        return self._children

    def add_child(self, child: 'Listener'):
        """添加子监听器"""
        self._children[child.name] = child
        child.parent = self

    def remove_child(self, child_name: str):
        """移除子监听器"""
        if child_name in self._children:
            self._children[child_name].parent = None
            self._children.pop(child_name, None)

    @property
    def enabled(self) -> bool:
        """检查监听器是否启用"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        """设置监听器启用状态"""
        self._enabled = value
        self.logger.debug("enabled status set to %s", value)

    @property
    def healthy(self) -> bool:
        """获取健康状态"""
        return self._healthy

    @property
    def ready(self) -> bool:
        """
        检查监听器是否就绪

        就绪条件：已启用 + 健康 + 运行中
        """
        return self.enabled and self.healthy and self._state == ListenerState.RUNNING

    async def on_health_check(self) -> bool:
        """健康检查回调，子类可覆盖实现自定义检查逻辑"""
        return True

    async def on_health_check_error(self, retry_state: RetryCallState):
        """健康检查失败时的回调"""
        self.logger.warning("Health check attempt %d failed", retry_state.attempt_number - 1)

    async def health_check(self, recursive: bool = True):
        """
        执行健康检查

        Args:
            recursive: 是否递归检查子监听器
        """
        if recursive:
            for child in list(self.children.values()):
                await child.health_check(True)
                if child.state == ListenerState.FINISHED:
                    await child.delete_background()
                    self.remove_child(child.name)
        try:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT_SECONDS),
                                               reraise=True, retry_error_callback=self.on_health_check_error, 
                                               retry=retry_if_not_exception_type((asyncio.CancelledError, KeyboardInterrupt))):
                with attempt:
                    result = await self.on_health_check()
            if not result:
                raise ValueError("returned unhealthy status")
            self._healthy = True
            if self.enabled:
                self.update_background()
        except Exception as e:
            self._healthy = False
            self.logger.error("Health check failed: %s", e, exc_info=True)

    @abstractmethod
    async def on_tick(self) -> bool:
        """
        定时回调（抽象方法，子类必须实现）

        Returns:
            True 表示任务完成，将停止监听器；False 继续运行
        """

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT_SECONDS), reraise=True, retry=retry_if_not_exception_type((asyncio.CancelledError, KeyboardInterrupt)))
    async def __tick_internal(self) -> bool:
        """
        内部 tick 实现（带重试机制）

        状态机核心逻辑，根据当前状态执行相应操作。
        """
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
        """执行一次 tick（加锁保证线程安全）"""
        async with self._alock:
            return await self.__tick_internal()

    async def start(self, recursive: bool = True):
        """
        启动监听器

        Args:
            recursive: 是否递归启动子监听器
        """
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

    async def on_start(self):
        """启动回调，子类可覆盖实现初始化逻辑"""
        self.logger.info("listener started")

    async def stop(self, recursive: bool = True):
        """
        停止监听器

        Args:
            recursive: 是否递归停止子监听器
        """
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
        await self.delete_background()
        await self.tick()

    async def on_stop(self):
        """停止回调，子类可覆盖实现清理逻辑"""
        self.logger.info("listener stopped")

    async def restart(self, recursive: bool = True):
        """
        重启监听器

        Args:
            recursive: 是否递归重启子监听器
        """
        await self.stop(recursive)
        assert self._state == ListenerState.STOPPED, "Listener must be stopped before restarting"
        await self.start(recursive)

    @property
    def logger_name(self) -> str:
        """获取日志器名称"""
        return self.name

    @cached_property
    def logger(self) -> logging.Logger:
        """获取日志器实例"""
        return logging.getLogger(self.logger_name)

    def log_state(self, console: Console, recursive: bool = True):
        """将状态输出到控制台（子类可覆盖实现自定义输出）"""
        if recursive:
            for child in list(self.children.values()):
                child.log_state(console, True)

    @property
    def log_state_dict(self) -> dict:
        """获取状态字典（用于日志输出）"""
        return {
            'enabled': self.enabled,
            'ready': self.ready,
            'healthy': self.healthy,
            'state': self.state,
            'uptime': self.uptime,
        }

    @property
    def id(self) -> str:
        """获取唯一标识符"""
        return f"{self.__class__.__name__}-{id(self)}"

    def __iter__(self) -> Iterator['Listener']:
        """迭代器：深度优先遍历所有子监听器和自身"""
        for child in self.children.values():
            yield from child
        yield self
