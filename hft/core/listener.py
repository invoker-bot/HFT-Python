"""
监听器基类模块

监听器基类，用于实现交易策略、风控、监控等功能。

核心概念：
- 状态机：STARTING -> RUNNING -> STOPPING -> STOPPED
- 生命周期回调：on_start(), on_tick(), on_stop(), on_health_check()
- 父子关系：支持树形结构，递归操作
- 后台任务：自动管理定时执行的后台任务
- 序列化：支持 pickle 持久化
- 类索引：按类快速查找子监听器（O(1) 查找）
"""
# pylint: disable=import-outside-toplevel,protected-access
import asyncio
import logging
import time
import weakref
from abc import ABC, ABCMeta, abstractmethod
from datetime import datetime
from enum import StrEnum
from functools import cached_property, lru_cache
from typing import Coroutine, Iterator, Optional, Type, TypeVar, Any, TYPE_CHECKING

from humanfriendly import format_timespan
from rich.console import Console
from tenacity import (AsyncRetrying, RetryCallState, retry,
                      retry_if_not_exception_type, stop_after_attempt,
                      wait_fixed)

from ..plugin import pm
if TYPE_CHECKING:
    from .app.base import AppCore

# 泛型类型变量，用于类型安全的查找方法
T = TypeVar('T', bound='Listener')


class ListenerState(StrEnum):
    """
    监听器状态枚举

    状态转换：
    STARTING -> RUNNING -> STOPPING -> STOPPED
                    |                      ^
                    v                      |
                  ERROR (错误)             |
                    |                      |
                    +----------------------+
    """
    STARTING = "starting"   # 启动中
    RUNNING = "running"     # 运行中
    STOPPING = "stopping"   # 停止中
    STOPPED = "stopped"     # 已停止
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

    类属性：
    - lazy_start: 是否延迟启动（True 时不跟随父节点自动启动，需要显式调用 start()）
    """
    # pickle 排除列表：不序列化的属性
    # - _parent: 弱引用，由 get_or_create 重建
    # - _children: 由 get_or_create 重建，不保存整棵树
    # - _background_task: asyncio.Task 不可序列化
    # - _alock: asyncio.Lock 不可序列化
    # - root: 缓存属性，由 parent 构建
    __pickle_exclude__ = {
        "_parent",
        "_children",
        "_background_task",
        "_alock",
        "_ulock",
        '_state',
        "root",
        "depth",
        "kwargs",
    }

    # 延迟启动标志：True 时不跟随父节点自动启动，保持 STOPPED 状态直到显式 start()
    lazy_start: bool = False
    disable_tick: bool = False  # 是否禁用 tick 回调，关闭以禁用定时任务，节约开销
    # 缓存过期时间（秒）：超过此时间未 tick 的 Listener 会从磁盘缓存中清除
    # None 表示永不过期
    cache_time: Optional[float] = None

    def __init__(self, **kwargs):
        """
        初始化监听器

        Args:
            name: 监听器名称，默认使用类名
            interval: tick 间隔（秒），None 表示不创建 tick task（事件驱动）
        """
        self.name = kwargs.get("name", self.__class__.__name__)
        self._interval: Optional[float] = kwargs.get("interval", 1.0)  # None = 事件驱动，不创建 tick task

        # Internal state
        self._enabled = True
        self._healthy = False
        self._healthy_since = time.time()
        self.start_time = self.current_time
        self.update_time = self.current_time  # 最近一次 tick 的时间，用于缓存过期判断
        self.finished = False  # 任务完成标志，有此标记的 Listener 不会被重启
        self._auto_disable_start_time = self.current_time
        self._auto_disable_duration: Optional[float] = None  # 自动禁用时长（秒），None 表示不启用
        self.initialize(**kwargs)

    @property
    def auto_disable_duration(self) -> Optional[float]:
        """获取自动禁用时长（秒），None 表示不启用"""
        return self._auto_disable_duration

    @auto_disable_duration.setter
    def auto_disable_duration(self, value: Optional[float]):
        self._auto_disable_duration = value
        self._auto_disable_start_time = self.current_time
        if value is not None:
            self._enabled = True  # 启用监听器

    @property
    def interval(self) -> Optional[float]:
        """获取 tick 间隔（秒），None 表示事件驱动，不创建 tick task"""
        return self._interval

    @interval.setter
    def interval(self, interval: Optional[float]):
        self._interval = interval

    @property
    def healthy_interval(self) -> Optional[float]:
        return None

    def initialize(self, **kwargs):
        """初始化不可序列化的对象（在 __init__ 和 unpickle 时调用）"""
        self._alock = asyncio.Lock()
        self._ulock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._state = ListenerState.STOPPED
        # children 由 get_or_create 重建，不从 pickle 恢复
        self._children: dict[str, 'Listener'] = kwargs.get("children", {})
        _parent = kwargs.get("parent", None)
        if _parent is None:
            self._parent = None
        else:
            self._parent = weakref.ref(_parent)
            _parent.children[self.name] = self
            self.root._clear_class_cache()
        self._clear_root_cache()


    def __getstate__(self) -> dict:
        """
        获取可序列化的状态（用于 pickle）

        排除不可序列化的对象（锁、任务、弱引用）。
        """
        saved = self.on_save()
        state = {k: v for k, v in self.__dict__.items() if k not in self.__pickle_exclude__}
        state["cache_time"] = self.cache_time
        state.update(saved)
        return state

    def __setstate__(self, state: dict):
        """
        从序列化数据恢复状态

        重新初始化不可序列化的对象（锁、任务、弱引用）。
        children 不再从 pickle 恢复，而是通过 get_or_create 重建。
        """
        # Restore basic attributes
        self.__dict__.update(state)

        # Reinitialize non-serializable objects (including empty _children)
        kwargs = state.get('kwargs', {})
        self.initialize(**kwargs)

        # NOTE: children 现在不保存了，由 get_or_create 机制重建
        # 子类可以在 on_reload 中手动重建 children
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

    async def loop_coro_in_background(
        self,
        coro: Coroutine,
        finalizer: Optional[Coroutine] = None,
        params: Optional[dict] = None,
    ):
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
                self._healthy_since = time.time()  # Mark as healthy on successful execution
            except asyncio.CancelledError:
                should_finalize = True  # Allow task to be cancelled gracefully
            except Exception as e:
                self.logger.exception("Exception in background task: %s", str(e))
            finally:
                if not should_finalize:
                    await asyncio.sleep(max(0, self.interval - (time.time() - start)))
            if should_finalize:
                if finalizer is not None:
                    await finalizer()
                break

    async def __finalize_background_task(self):
        """
        后台任务完成时的清理回调

        当后台任务自然结束时（tick 返回 True），执行清理逻辑。
        注意：此方法在后台任务内部被调用，不应尝试取消任务本身。
        """
        # 标记任务已完成
        self._background_task = None

        # 如果是因为任务完成（非手动停止），则执行停止逻辑
        if self.enabled:
            self.enabled = False
            if not self.disable_tick:
                # 触发状态转换到 STOPPING
                if self.state == ListenerState.RUNNING:
                    self.state = ListenerState.STOPPING
                    await self.__tick_internal()

    async def __create_background_task_internal(self):
        # interval=None 表示事件驱动，不创建 tick task
        if self.interval is None:
            return
        bt = self._background_task
        if bt is None or bt.done():  # 没有任务或已完成
            self._background_task = asyncio.create_task(
                self.loop_coro_in_background(
                    self.tick,
                    self.__finalize_background_task  # 使用专门的清理方法
                ),
                name=f"{self.name}-background-task"
            )

    async def __delete_background_task_internal(self):
        """
        取消后台任务的实际实现

        注意：如果当前代码正在后台任务内部执行，不会尝试取消自己。
        """
        bt = self._background_task
        if bt is None:
            return

        # 检查是否在后台任务内部调用（避免取消自己）
        current_task = asyncio.current_task()
        if bt is current_task:
            # 在后台任务内部，只清空引用，让任务自然结束
            self._background_task = None
            return

        # 从外部取消后台任务
        if bt.cancel():
            # 记录调用方当前的取消计数，用于区分"框架主动取消"和"调用方被取消"
            cancelling_before = current_task.cancelling() if current_task else 0
            try:
                await asyncio.wait_for(bt, timeout=30)  # 等待取消完成，设置超时时间
            except asyncio.TimeoutError:
                self.logger.warning("Timeout while cancelling background task")
            except asyncio.CancelledError:
                # 检查是否是调用方被取消（取消计数增加）
                cancelling_after = current_task.cancelling() if current_task else 0
                if cancelling_after > cancelling_before:
                    # 调用方被取消，重新抛出
                    raise
                # 否则是框架主动取消后台任务，正常结束
            finally:
                self._background_task = None

    async def __update_background_task_internal(self):
        # lazy_start 且未启动的 Listener 不应创建后台任务，需显式调用 start()
        if self.disable_tick or (self.lazy_start and self._state == ListenerState.STOPPED):
            return
        if self.enabled and not self.finished:
            # print("create", self.name)
            await self.__create_background_task_internal()
        else:
            await self.__delete_background_task_internal()

    async def update_background_task(self):
        """更新后台任务（重启 tick 循环）"""
        async with self._ulock:
            await self.__update_background_task_internal()

    @property
    def state(self) -> ListenerState:
        """获取当前状态"""
        return self._state

    @state.setter
    def state(self, value: ListenerState):
        """设置当前状态"""
        self._state = value

    @cached_property
    def root(self) -> 'AppCore':
        """获取根监听器（向上遍历到顶层，缓存结果）"""
        parent = self.parent
        if parent is None:
            return self
        return parent.root

    def _clear_root_cache(self):
        """清除 root 缓存（在 parent 变化时调用）"""
        self.__dict__.pop("root", None)
        self.__dict__.pop("depth", None)
        # 递归清除子节点的缓存
        for child in self._children.values():
            child._clear_root_cache()

    @property
    def parent(self) -> Optional['Listener']:
        """获取父监听器"""
        if self._parent is None:
            return None
        return self._parent()

    def _set_parent(self, parent: Optional['Listener']):
        """设置父监听器"""
        if parent is None:
            self._parent = None
        else:
            self._parent = weakref.ref(parent)
        self._clear_root_cache()

    @parent.setter
    def parent(self, parent: Optional['Listener']):
        """设置父监听器"""
        assert self.parent is None, "Cannot set parent: already has a parent"
        if parent is not None:
            parent.add_child(self)
        else:
            self._set_parent(None)

    @property
    def children(self) -> dict[str, 'Listener']:
        """获取子监听器字典"""
        return self._children

    def add_child(self, child: 'Listener'):
        """
        添加子监听器

        树结构变动时，清理根节点的查找缓存。

        Args:
            child: 要添加的子监听器
        """
        assert child.parent is None, "Cannot add child: already has a parent"
        self._children[child.name] = child
        child._set_parent(self)
        self.root._clear_class_cache()

    async def add_child_with_start(self, child: 'Listener'):
        """添加子监听器并启动"""
        self.add_child(child)
        await child.start(True)

    def remove_child(self, child_name: str):
        """
        移除子监听器

        树结构变动时，清理根节点的查找缓存。

        Args:
            child_name: 要移除的子监听器名称
        """
        if child_name in self._children:
            child = self._children[child_name]
            child._set_parent(None)
            self._children.pop(child_name, None)
            # 清理根节点的查找缓存
            self.root._clear_class_cache()

    async def remove_child_with_end(self, child_name: str):
        """移除子监听器并停止"""
        child = self._children.get(child_name, None)
        if child is not None:
            await child.stop(True)
            self.remove_child(child_name)

    # ============================================================
    # 类查找相关方法（使用 lru_cache 缓存结果）
    # ============================================================

    def _clear_class_cache(self):
        """
        清理类查找缓存

        在树结构变动时调用（add_child / remove_child）。
        """
        # 清理所有查找方法的缓存
        self.find_child_by_class_at_node.cache_clear()
        self.find_children_by_class_at_node.cache_clear()

    def find_child_by_class(self, cls: Type[T]) -> Optional[T]:
        """
        按类查找第一个匹配的子监听器（从根节点开始递归查找）

        使用 lru_cache 缓存结果，树变动时自动清理缓存。
        返回深度最浅的第一个匹配项。

        Args:
            cls: 要查找的类

        Returns:
            匹配的监听器实例，如果没有找到则返回 None

        Example:
            executor = app.find_child_by_class(MarketExecutor)
            if executor:
                executor.pause()
        """
        # 从根节点开始查找
        return self.root.find_child_by_class_at_node(cls, self.root)

    def find_children_by_class(self, cls: Type[T]) -> list[T]:
        """
        按类查找所有匹配的子监听器（从根节点开始递归查找）

        使用 lru_cache 缓存结果，树变动时自动清理缓存。

        Args:
            cls: 要查找的类

        Returns:
            匹配的监听器实例元组

        Example:
            strategies = app.find_children_by_class(BaseStrategy)
            for strategy in strategies:
                print(strategy.name)
        """
        # 从根节点开始查找
        return self.root.find_children_by_class_at_node(cls, self.root)

    @lru_cache(maxsize=1024)
    def find_child_by_class_at_node(self, cls: Type[T], node: 'Listener') -> Optional[T]:
        """
        从指定节点开始按类查找第一个匹配的子监听器

        递归查找，返回深度最浅的第一个匹配项.

        Args:
            cls: 要查找的类
            node: 开始查找的节点

        Returns:
            匹配的监听器实例，如果没有找到则返回 None

        Example:
            # 从 executor 节点开始查找 Strategy
            strategy = app.find_child_by_class_at_node(BaseStrategy, executor)
        """
        # 检查当前节点
        if isinstance(node, cls):
            return node

        # 深度优先，递归检查孙节点
        for child in node.children.values():
            result = self.find_child_by_class_at_node(cls, child)
            if result is not None:
                return result

        return None

    @lru_cache(maxsize=1024)
    def find_children_by_class_at_node(self, cls: Type[T], node: 'Listener') -> list[T]:
        """
        从指定节点开始按类查找所有匹配的子监听器

        递归查找，返回匹配的元组。

        Args:
            cls: 要查找的类
            node: 开始查找的节点

        Returns:
            匹配的监听器实例元组

        Example:
            # 从 executor 节点开始查找所有 Strategy
            strategies = app.find_children_by_class_at_node(BaseStrategy, executor)
        """
        result = []

        # 检查当前节点
        if isinstance(node, cls):
            result.append(node)

        # 递归检查所有子节点
        for child in node.children.values():
            result.extend(self.find_children_by_class_at_node(cls, child))

        return result

    @property
    def enabled(self) -> bool:
        """检查监听器是否启用"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        """设置监听器启用状态"""
        # import traceback
        # old_value = self._enabled
        self._enabled = value
        # self.logger.info("set enabled = %s", value)
        # if old_value != value:
        #     self.logger.warning("enabled status changed: %s -> %s, stack:\n%s",
        #                       old_value, value, ''.join(traceback.format_stack()))

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
        if self.healthy_interval is not None:
            if time.time() - self._healthy_since > self.healthy_interval:
                return False
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
        if not self.enabled:  # 不检查enable的
            return
        if recursive:
            for child in list(self.children.values()):
                await child.health_check(True)
        try:
            # self.logger.info("Performing health check")
            # self.logger.info("Health check: running state is %s", self.state)
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(RETRY_ATTEMPTS),
                wait=wait_fixed(RETRY_WAIT_SECONDS),
                reraise=True,
                retry_error_callback=self.on_health_check_error,
                retry=retry_if_not_exception_type(
                    (asyncio.CancelledError, KeyboardInterrupt)
                ),
            ):
                with attempt:
                    result = await self.on_health_check()
            if not result:
                raise ValueError("returned unhealthy status")
            self._healthy = True
        except (asyncio.CancelledError, KeyboardInterrupt):  # 不处理退出异常
            return
        except Exception as e:
            self._healthy = False
            self.logger.error("Health check failed: %s", e, exc_info=True)
            # 插件钩子：健康检查失败
            pm.hook.on_health_check_failed(listener=self, error=e)

    @abstractmethod
    async def on_tick(self) -> bool:
        """
        定时回调（抽象方法，子类必须实现）

        Returns:
            True 表示任务完成，将停止监听器；False 继续运行
        """

    @retry(
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=wait_fixed(RETRY_WAIT_SECONDS),
        reraise=True,
        retry=retry_if_not_exception_type((asyncio.CancelledError, KeyboardInterrupt)),
    )
    async def __tick_internal(self) -> bool:
        """
        内部 tick 实现（带重试机制）

        状态机核心逻辑，根据当前状态执行相应操作。
        """
        try:
            if self.auto_disable_duration is not None and self.current_time - self._auto_disable_start_time > self.auto_disable_duration:
                # self.logger.info("Auto disabling listener after %.2f seconds", self.auto_disable_duration)
                print("auto disable:", self.current_time, self._auto_disable_start_time, self.auto_disable_duration)
                self.enabled = False
                self.auto_disable_duration = None
            match self.state:
                case ListenerState.STARTING:
                    if self.enabled:
                        await self.on_start()
                        self.start_time = self.current_time
                        self.state = ListenerState.RUNNING
                    else:
                        self.state = ListenerState.STOPPED
                case ListenerState.RUNNING:
                    if self.enabled:
                        # try:
                        result = await self.on_tick()
                        self._healthy = True
                        if result:  # task signaled completion
                            self.enabled = False
                            self.state = ListenerState.STOPPING
                            # await asyncio.shield(self.on_stop())
                            # self.state = ListenerState.STOPPED
                            # self.state = ListenerState.FINISHED
                        # except asyncio.CancelledError:
                        #     self.state = ListenerState.STOPPING
                    else:
                        self.state = ListenerState.STOPPING
                    # if self.state == ListenerState.STOPPING:  # disabled or stopping
                    #     await asyncio.shield(self.on_stop())
                    #     self.state = ListenerState.STOPPED
                case ListenerState.STOPPING:
                    try:
                        await asyncio.shield(self.on_stop())
                    except Exception as e:
                        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
                            raise
                        self.logger.error("Error during on_stop: %s", e, exc_info=True)
                    self.state = ListenerState.STOPPED
                case ListenerState.STOPPED:
                    if self.enabled and not self.finished:
                        self.state = ListenerState.STARTING
                # ListenerState.ERROR:
                #     if self.enabled
        except Exception as e:
            self._healthy = False
            self.logger.error("Error during tick execution: %s", e, exc_info=True)
        return (not self.enabled) and self.state in (ListenerState.STOPPED, ListenerState.STOPPING)

    async def tick(self):
        """执行一次 tick（加锁保证线程安全），并更新 update_time 用于缓存过期判断"""
        async with self._alock:
            self.update_time = self.current_time
            return await self.__tick_internal()

    async def __start_internal(self, recursive: bool = True):
        self.enabled = True
        if self.finished:
            return
        if not self.disable_tick:
            if self.state == ListenerState.STOPPED:  # 目前不会处理STOPPING状态的
                self.state = ListenerState.STARTING
            # interval=None 的 Listener 不创建 tick task，需要手动触发一次 tick
            # 来完成 STARTING -> RUNNING 的状态转换（调用 on_start）
            if self.interval is None:
                await self.__tick_internal()
        if recursive:
            for child in list(self.children.values()):
                # 跳过 lazy_start 的子节点，它们需要显式调用 start()
                if child.lazy_start:
                    continue
                await child.start(True)

    async def start(self, recursive: bool = True):
        """
        启动监听器

        Args:
            recursive: 是否递归启动子监听器
        """
        async with self._alock:
            await self.__start_internal(recursive)

    async def on_start(self):
        """启动回调，子类可覆盖实现初始化逻辑"""
        # 插件钩子：Listener 启动
        pm.hook.on_listener_start(listener=self)

    async def __stop_internal(self, recursive: bool = True):
        """stop() 的实际实现，被 shield 保护"""
        # async with self._alock:
        await self.__delete_background_task_internal()
        if recursive:
            for child in list(self.children.values()):
                await child.stop(True)
        self.enabled = False
        if self.disable_tick:
            return
        match self.state:
            case ListenerState.STARTING:
                self.state = ListenerState.STOPPED
            case ListenerState.RUNNING:
                self.state = ListenerState.STOPPING
            # case _:
            #     self.logger.warning("Stop called but listener not running")
        await self.__tick_internal()

    async def __stop_private(self, recursive: bool = True):
        async with self._alock:
            await self.__stop_internal(recursive)

    async def stop(self, recursive: bool = True):
        """
        停止监听器（使用 shield 保护，防止被 CancelledError 中断）

        Args:
            recursive: 是否递归停止子监听器
        """
        try:
            await asyncio.shield(self.__stop_private(recursive))
        except asyncio.CancelledError:
            pass
            # self.logger.warning("listener stopped by cancellation")

    async def on_stop(self):
        """停止回调，子类可覆盖实现清理逻辑"""
        # 插件钩子：Listener 停止
        pm.hook.on_listener_stop(listener=self)

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

    def __hash__(self):
        return id(self)

    def __eq__(self, value):
        return id(self) == id(value)


class GroupListener(Listener, metaclass=ABCMeta):
    """
    动态子节点管理的 Listener 基类

    适用于需要根据配置动态创建/删除子节点的场景，如：
    - ExchangeBalanceListener: 根据 ccxt_instances 创建多个 WatchListener
    - DataSourceGroup: 根据请求动态创建 DataSource

    特点：
    - 通过 sync_children_params() 声明需要哪些 children
    - 自动同步：缺少的创建，多余的删除

    子类需要实现：
    - sync_children_params(): 返回 {name: param} 字典
    - create_dynamic_child(name, param): 根据参数创建 child

    Example:
        class ExchangeBalanceListener(GroupListener):
            def sync_children_params(self) -> dict[str, Any]:
                exchange = self.parent
                return {
                    f"watch-{key}": {"key": key, "type": "watch"}
                    for key in exchange.config.ccxt_instances.keys()
                }

            def create_dynamic_child(self, name: str, param: Any) -> Listener:
                if param["type"] == "watch":
                    return ExchangeBalanceWatchListener(param["key"])
    """
    @property
    def interval(self) -> float:
        """获取 tick 间隔（秒），GroupListener 默认禁用 tick 回调"""
        return 15

    @abstractmethod
    def sync_children_params(self) -> dict[str, Any]:
        """
        返回需要的 children 参数字典

        子类必须实现此方法，返回 {name: param} 字典。
        框架会根据这个字典自动同步 children。

        Returns:
            {child_name: create_param} 字典
        """

    @abstractmethod   # must using app factory method
    def create_dynamic_child(self, name: str, param: Any) -> 'Listener':
        """
        根据参数创建动态 child

        子类必须实现此方法。

        Args:
            name: child 名称
            param: 创建参数（来自 sync_children_params）

        Returns:
            新创建的 Listener 实例
        """

    def _sync_children(self) -> set[str]:
        """
        同步 children：创建缺少的，删除多余的
        """
        target_params = self.sync_children_params()
        target_names = set(target_params.keys())
        current_names = set(self._children.keys())

        # created = 0

        # 删除多余的 children
        # for name in current_names - target_names:
        #     self.remove_child_with_end(name)
        #     removed += 1
        #     self.logger.info("Removed dynamic child: %s", name)

        # 创建缺少的 children
        for name in target_names - current_names:
            param = target_params[name]
            child = self.create_dynamic_child(name, param)
            assert child.name == name, "Child name mismatch"
            assert id(child.parent) == id(self), "Child parent mismatch"
            assert id(child) == id(self._children[name]), "Child not added correctly"
            # self.add_child(child)
            # 启动子节点：STARTING 状态（on_start 中创建）或 RUNNING 状态（on_tick 中创建）
            # if self.state in (ListenerState.STARTING, ListenerState.RUNNING):
            #     child.enabled = True  # 标记为启用
            # created += 1
            self.logger.info("Created dynamic child: %s", name)

        return current_names - target_names

    async def _async_children(self):
        for name in self._sync_children():
            await self.remove_child_with_end(name)
            self.logger.info("Removed dynamic child: %s", name)

    async def on_tick(self) -> bool:
        """每次 tick 同步 children"""
        await self._async_children()
        return False

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._sync_children()
