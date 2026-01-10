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
import time
import asyncio
import logging
import weakref
from collections import defaultdict
from datetime import datetime
from functools import cached_property
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Optional, Coroutine, Iterator, TypeVar, Type
from rich.console import Console
from humanfriendly import format_timespan
from tenacity import retry, stop_after_attempt, wait_fixed, AsyncRetrying, RetryCallState, retry_if_not_exception_type

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
    # FINISHED = "finished"   # 任务完成（正常退出）
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
    __pickle_exclude__ = ("_parent", "_background_task", "_alock", "_class_index", "root")

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
        self.interval = interval  # may update from config

        # Internal state
        self._enabled = True
        self._healthy = False
        self.start_time = self.current_time
        self._children: dict[str, 'Listener'] = {}

        self.initialize()

    def initialize(self):  # 这里面初始化不可序列化的对象
        self._parent: Optional[weakref.ReferenceType['Listener']] = None
        self._alock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._state = ListenerState.STOPPED
        # 类索引: Type -> list[(weakref, depth)]
        # 只在根节点维护，用于快速按类查找子监听器
        self._class_index: dict[type, list[tuple[weakref.ReferenceType['Listener'], int]]] = defaultdict(list)

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
        恢复子监听器的父引用和类索引。
        """
        # Restore basic attributes
        self.__dict__.update(state)

        # Reinitialize non-serializable objects
        self.initialize()
        # Restore children (note: subclasses must handle actual child reconstruction)
        for child in self._children.values():
            child.parent = self
            # 重建类索引
            self._register_to_class_index(child, relative_depth=1)
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

    async def __create_background_task_internal(self):
        bt = self._background_task
        if bt is None or bt.done():  # 没有任务或已完成
            self._background_task = asyncio.create_task(
                self.loop_coro_in_background(self.tick, self.interval, self.stop),
                name=f"{self.name}-background-task"
            )

    async def __delete_background_task_internal(self):
        """取消后台任务的实际实现"""
        bt = self._background_task
        if bt is not None and bt.cancel():
            await bt  # 等待取消完成
            self._background_task = None

    async def __update_background_task_internal(self):
        if self.enabled:
            await self.__create_background_task_internal()
        else:
            await self.__delete_background_task_internal()

    async def update_background_task(self):
        async with self._alock:
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
    def root(self) -> 'Listener':
        """获取根监听器（向上遍历到顶层，缓存结果）"""
        parent = self.parent
        if parent is None:
            return self
        return parent.root

    def _clear_root_cache(self):
        """清除 root 缓存（在 parent 变化时调用）"""
        if 'root' in self.__dict__:
            del self.__dict__['root']
        # 递归清除子节点的缓存
        for child in self._children.values():
            child._clear_root_cache()

    @property
    def parent(self) -> Optional['Listener']:
        """获取父监听器"""
        if self._parent is None:
            return None
        return self._parent()

    @parent.setter
    def parent(self, parent: Optional['Listener']):
        """设置父监听器"""
        self._clear_root_cache()  # 清除 root 缓存
        if parent is None:
            self._parent = None
        else:
            self._parent = weakref.ref(parent)

    @property
    def children(self) -> dict[str, 'Listener']:
        """获取子监听器字典"""
        return self._children

    def add_child(self, child: 'Listener'):
        """
        添加子监听器

        同时更新根节点的类索引，以支持按类快速查找。

        Args:
            child: 要添加的子监听器
        """
        self._children[child.name] = child
        child.parent = self
        # 更新类索引
        self._register_to_class_index(child, relative_depth=1)

    def remove_child(self, child_name: str):
        """
        移除子监听器

        同时从根节点的类索引中移除。

        Args:
            child_name: 要移除的子监听器名称
        """
        if child_name in self._children:
            child = self._children[child_name]
            # 从类索引中移除
            self._unregister_from_class_index(child)
            child.parent = None
            self._children.pop(child_name, None)

    # ============================================================
    # 类索引相关方法
    # ============================================================

    def _get_depth_from_root(self) -> int:
        """
        计算当前节点相对于根节点的深度

        Returns:
            深度值（根节点 = 0，直接子节点 = 1，孙节点 = 2，等等）
        """
        depth = 0
        node = self
        while node.parent is not None:
            depth += 1
            node = node.parent
        return depth

    def _register_to_class_index(self, listener: 'Listener', relative_depth: int):
        """
        将监听器及其所有后代注册到根节点的类索引中

        Args:
            listener: 要注册的监听器
            relative_depth: 相对于当前节点的深度（1 = 直接子节点）
        """
        root = self.root
        index = root._class_index

        # 计算相对于根节点的绝对深度
        base_depth = self._get_depth_from_root()
        absolute_depth = base_depth + relative_depth

        # 注册该监听器本身（遍历其所有父类）
        for cls in type(listener).__mro__:
            if cls is Listener or cls is ABC or cls is object:
                break
            index[cls].append((weakref.ref(listener), absolute_depth))

        # 递归注册其子监听器
        for child in listener.children.values():
            self._register_to_class_index(child, relative_depth + 1)

    def _unregister_from_class_index(self, listener: 'Listener'):
        """
        从根节点的类索引中移除监听器及其所有后代

        Args:
            listener: 要移除的监听器
        """
        root = self.root
        index = root._class_index

        # 移除该监听器本身
        for cls in type(listener).__mro__:
            if cls is Listener or cls is ABC or cls is object:
                break
            if cls in index:
                # 过滤掉该监听器的弱引用
                index[cls] = [
                    (ref, d) for ref, d in index[cls]
                    if ref() is not None and ref() is not listener
                ]
                # 如果列表为空，删除该键
                if not index[cls]:
                    del index[cls]

        # 递归移除其子监听器
        for child in listener.children.values():
            self._unregister_from_class_index(child)

    def _cleanup_class_index(self):
        """
        清理类索引中的无效弱引用

        在查找时自动调用，移除已被垃圾回收的实例。
        """
        index = self._class_index
        for cls in list(index.keys()):
            # 过滤掉已效的弱引用
            index[cls] = [(ref, d) for ref, d in index[cls] if ref() is not None]
            if not index[cls]:
                del index[cls]

    def find_child_by_class(self, cls: Type[T]) -> Optional[T]:
        """
        按类查找第一个匹配的子监听器

        从根节点的类索引中查找，O(1) 复杂度。
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
        root = self.root
        if cls not in root._class_index:
            return None

        entries = root._class_index[cls]
        # 按深度排序，返回最浅的
        entries.sort(key=lambda x: x[1])

        for ref, _ in entries:
            instance = ref()
            if instance is not None:
                return instance

        # 如果所有引用都失效了，清理索引
        root._cleanup_class_index()
        return None

    def find_children_by_class(self, cls: Type[T]) -> list[T]:
        """
        按类查找所有匹配的子监听器

        从根节点的类索引中查找，O(n) 复杂度（n = 匹配数量）。
        返回按深度排序的列表（浅的在前）。

        Args:
            cls: 要查找的类

        Returns:
            匹配的监听器实例列表，按深度排序

        Example:
            strategies = app.find_children_by_class(BaseStrategy)
            for strategy in strategies:
                print(strategy.name)
        """
        root = self.root
        if cls not in root._class_index:
            return []

        entries = root._class_index[cls]
        # 按深度排序
        entries.sort(key=lambda x: x[1])

        result = []
        valid_entries = []
        for ref, depth in entries:
            instance = ref()
            if instance is not None:
                result.append(instance)
                valid_entries.append((ref, depth))

        # 更新索引，移除失效的引用
        if len(valid_entries) != len(entries):
            root._class_index[cls] = valid_entries
            if not valid_entries:
                del root._class_index[cls]

        return result

    def find_children_by_class_at_depth(self, cls: Type[T], depth: int) -> list[T]:
        """
        按类和深度查找子监听器

        Args:
            cls: 要查找的类
            depth: 指定的深度（1 = 直接子节点，2 = 孙节点，等等）

        Returns:
            匹配的监听器实例列表

        Example:
            # 只获取直接子节点中的策略
            direct_strategies = app.find_children_by_class_at_depth(BaseStrategy, 1)
        """
        root = self.root
        if cls not in root._class_index:
            return []

        result = []
        for ref, d in root._class_index[cls]:
            if d == depth:
                instance = ref()
                if instance is not None:
                    result.append(instance)

        return result

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
                # if child.state == ListenerState.FINISHED:
                #     await child.delete_background()
                #     self.remove_child(child.name)
        try:
            # self.logger.info("Performing health check")
            # self.logger.info("Health check: running state is %s", self.state)
            async for attempt in AsyncRetrying(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT_SECONDS),
                                               reraise=True, retry_error_callback=self.on_health_check_error, 
                                               retry=retry_if_not_exception_type((asyncio.CancelledError, KeyboardInterrupt))):
                with attempt:
                    result = await self.on_health_check()
            if not result:
                raise ValueError("returned unhealthy status")
            self._healthy = True
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

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT_SECONDS), reraise=True, 
           retry=retry_if_not_exception_type((asyncio.CancelledError, KeyboardInterrupt)))
    async def __tick_internal(self) -> bool:
        """
        内部 tick 实现（带重试机制）

        状态机核心逻辑，根据当前状态执行相应操作。
        """
        try:
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
                    if self.enabled:
                        self.state = ListenerState.STARTING
                # ListenerState.ERROR:
                #     if self.enabled
        except Exception as e:
            self._healthy = False
            self.logger.error("Error during tick execution: %s", e, exc_info=True)
        return self.state in (ListenerState.STOPPED, ListenerState.STOPPING)

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
            if self.state == ListenerState.STOPPED:
                self.state = ListenerState.STARTING
            # else:
            #     self.logger.warning("Start called but listener not stopped")
            # await self.__tick_internal()
            # await self.__update_background_task_internal()
        if recursive:
            for child in list(self.children.values()):
                await child.start(True)

    async def on_start(self):
        """启动回调，子类可覆盖实现初始化逻辑"""
        self.logger.info("listener started")

    # async def set_stop(self, recursive: bool = True):
    #     self.enabled = False
    #     if recursive:
    #         for child in list(self.children.values()):
    #             await child.set_stop(True)

    async def __stop_internal(self, recursive: bool = True):
        """stop() 的实际实现，被 shield 保护"""
        # async with self._alock:
        await self.__delete_background_task_internal()
        if recursive:
            for child in list(self.children.values()):
                await child.stop(True)
        self.enabled = False
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


class GroupListener(Listener):
    """
    动态子节点管理的 Listener 基类

    适用于需要根据配置动态创建/删除子节点的场景，如：
    - ExchangeBalanceListener: 根据 ccxt_instances 创建多个 WatchListener
    - DataSourceGroup: 根据请求动态创建 DataSource

    特点：
    - 自身可以 pickle，但不保存 children（children 在启动时重建）
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

    # 不 pickle children，启动时重建
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_children", "children")

    def on_save(self):
        """保存时排除 children"""
        d = super().on_save()
        d["_children"] = {}
        return d

    def sync_children_params(self) -> dict[str, any]:
        """
        返回需要的 children 参数字典

        子类必须实现此方法，返回 {name: param} 字典。
        框架会根据这个字典自动同步 children。

        Returns:
            {child_name: create_param} 字典
        """
        return {}

    def create_dynamic_child(self, name: str, param: any) -> 'Listener':
        """
        根据参数创建动态 child

        子类必须实现此方法。

        Args:
            name: child 名称
            param: 创建参数（来自 sync_children_params）

        Returns:
            新创建的 Listener 实例
        """
        raise NotImplementedError("Subclass must implement create_dynamic_child")

    async def _sync_children(self) -> tuple[int, int]:
        """
        同步 children：创建缺少的，删除多余的

        Returns:
            (created_count, removed_count)
        """
        target_params = self.sync_children_params()
        target_names = set(target_params.keys())
        current_names = set(self._children.keys())

        created = 0
        removed = 0

        # 删除多余的 children
        for name in current_names - target_names:
            child = self._children[name]
            await child.stop()
            self.remove_child(name)
            removed += 1
            self.logger.debug("Removed dynamic child: %s", name)

        # 创建缺少的 children
        for name in target_names - current_names:
            param = target_params[name]
            child = self.create_dynamic_child(name, param)
            self.add_child(child)
            if self.state == ListenerState.RUNNING:
                await child.start()
            created += 1
            self.logger.debug("Created dynamic child: %s", name)

        return created, removed

    async def on_start(self):
        """启动时同步 children"""
        await super().on_start()
        await self._sync_children()

    async def on_tick(self) -> bool:
        """每次 tick 同步 children"""
        await self._sync_children()
        return False

    async def on_stop(self):
        """停止时清理所有 children"""
        for child in list(self._children.values()):
            await child.stop()
            self.remove_child(child.name)
        await super().on_stop()
