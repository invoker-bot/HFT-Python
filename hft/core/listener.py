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
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime
from enum import StrEnum
from functools import cached_property
from typing import Coroutine, Iterator, Optional, Type, TypeVar

from humanfriendly import format_timespan
from rich.console import Console
from tenacity import (AsyncRetrying, RetryCallState, retry,
                      retry_if_not_exception_type, stop_after_attempt,
                      wait_fixed)

from ..plugin import pm

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

    类属性：
    - lazy_start: 是否延迟启动（True 时不跟随父节点自动启动，需要显式调用 start()）
    """
    # pickle 排除列表：不序列化的属性
    # - _parent: 弱引用，由 get_or_create 重建
    # - _children: 由 get_or_create 重建，不保存整棵树
    # - _background_task: asyncio.Task 不可序列化
    # - _alock: asyncio.Lock 不可序列化
    # - _class_index: 由 add_child 重建
    # - root: 缓存属性，由 parent 构建
    __pickle_exclude__ = (
        "_parent",
        "_children",
        "_background_task",
        "_alock",
        "_class_index",
        "root",
        "depth",
    )

    # 延迟启动标志：True 时不跟随父节点自动启动，保持 STOPPED 状态直到显式 start()
    lazy_start: bool = False
    disable_tick: bool = False  # 是否禁用 tick 回调，关闭以禁用定时任务，节约开销

    def __init__(self, name: Optional[str] = None, interval: Optional[float] = 1.0):
        """
        初始化监听器

        Args:
            name: 监听器名称，默认使用类名
            interval: tick 间隔（秒），None 表示不创建 tick task（事件驱动）
        """
        if name is None:
            name = f"{self.__class__.__name__}"
        self.name = name
        self._interval: Optional[float] = interval  # None = 事件驱动，不创建 tick task

        # Internal state
        self._enabled = True
        self._healthy = False
        self.start_time = self.current_time

        self.initialize()

    @property
    def interval(self) -> Optional[float]:
        """获取 tick 间隔（秒），None 表示事件驱动，不创建 tick task"""
        return self._interval

    @interval.setter
    def interval(self, interval: Optional[float]):
        self._interval = interval

    def initialize(self):
        """初始化不可序列化的对象（在 __init__ 和 unpickle 时调用）"""
        self._parent: Optional[weakref.ReferenceType["Listener"]] = None
        self._alock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._state = ListenerState.STOPPED
        # 类索引: Type -> {depth: [weakref1, weakref2, ...]}
        # 只在根节点维护，用于快速按类查找子监听器
        # 优化：按深度分组，避免每次查找都排序
        self._class_index: dict[
            type, dict[int, list[weakref.ReferenceType["Listener"]]]
        ] = defaultdict(lambda: defaultdict(list))
        # children 由 get_or_create 重建，不从 pickle 恢复
        self._children: dict[str, 'Listener'] = {}

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
        children 不再从 pickle 恢复，而是通过 get_or_create 重建。
        """
        # Restore basic attributes
        self.__dict__.update(state)

        # 保存 _state，因为 initialize() 会重置它
        saved_state = self._state

        # Reinitialize non-serializable objects (including empty _children)
        self.initialize()

        # 恢复 _state
        self._state = saved_state

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
        interval: float = 0.001,
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
        # interval=None 表示事件驱动，不创建 tick task
        if self.interval is None:
            return
        bt = self._background_task
        if bt is None or bt.done():  # 没有任务或已完成
            # pylint: disable=attribute-defined-outside-init
            self._background_task = asyncio.create_task(
                self.loop_coro_in_background(self.tick, self.interval, self.stop),
                name=f"{self.name}-background-task"
            )

    async def __delete_background_task_internal(self):
        """取消后台任务的实际实现"""
        bt = self._background_task
        if bt is not None and bt.cancel():
            # 记录调用方当前的取消计数，用于区分"框架主动取消"和"调用方被取消"
            current_task = asyncio.current_task()
            cancelling_before = current_task.cancelling() if current_task else 0
            try:
                await bt  # 等待取消完成
            except asyncio.CancelledError:
                # 检查是否是调用方被取消（取消计数增加）
                cancelling_after = current_task.cancelling() if current_task else 0
                if cancelling_after > cancelling_before:
                    # 调用方被取消，重新抛出
                    raise
                # 否则是框架主动取消后台任务，正常结束
            finally:
                # pylint: disable=attribute-defined-outside-init
                self._background_task = None

    async def __update_background_task_internal(self):
        # lazy_start 且未启动的 Listener 不应创建后台任务，需显式调用 start()
        if self.disable_tick or (self.lazy_start and self._state == ListenerState.STOPPED):
            return
        if self.enabled:
            await self.__create_background_task_internal()
        else:
            await self.__delete_background_task_internal()

    async def update_background_task(self):
        """更新后台任务（重启 tick 循环）"""
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

    async def add_child_with_start(self, child: 'Listener'):
        """添加子监听器并启动"""
        self.add_child(child)
        await child.start(True)

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

    async def remove_child_with_end(self, child_name: str):
        """移除子监听器并停止"""
        child = self._children.get(child_name, None)
        if child is not None:
            await child.stop(True)
            self.remove_child(child_name)

    # ============================================================
    # 类索引相关方法
    # ============================================================

    @cached_property
    def depth(self) -> int:
        """计算当前节点相对于根节点的深度

        Returns:
            深度值（根节点 = 0，直接子节点 = 1，孙节点 = 2，等等）
        """
        if self.parent is None:
            return 0
        return self.parent.depth + 1

    def _register_to_class_index(self, listener: 'Listener', relative_depth: int):
        """
        将监听器及其所有后代注册到根节点的类索引中

        工作原理：
        1. 计算 listener 相对于根节点的绝对深度
        2. 将 listener 注册到其所有父类的索引中（按深度分组）
        3. 递归注册 listener 的所有子节点

        Args:
            listener: 要注册的监听器
            relative_depth: listener 相对于当前节点（self）的深度
                          - 1 = 直接子节点
                          - 2 = 孙节点
                          - 以此类推

        Example:
            Root (depth=0) 调用 add_child(Parent):
              -> _register_to_class_index(Parent, relative_depth=1)
              -> Parent 的 absolute_depth = 0 + 1 = 1 ✓
              -> 递归注册 Parent 的子节点 Child:
                 -> _register_to_class_index(Child, relative_depth=2)
                 -> Child 的 absolute_depth = 0 + 2 = 2 ✓
        """
        root = self.root
        index = root._class_index

        # 计算相对于根节点的绝对深度
        # base_depth = 当前节点（self）相对于根节点的深度
        # absolute_depth = listener 相对于根节点的深度
        base_depth = self.depth
        absolute_depth = base_depth + relative_depth

        # 注册该监听器本身（遍历其所有父类），按深度分组
        # 使用 MRO (Method Resolution Order) 遍历继承链
        # 例如：MarketExecutor -> BaseExecutor -> Listener -> ABC -> object
        for cls in type(listener).__mro__:
            # 停止条件：到达 Listener 基类或更上层
            if cls is Listener or cls is ABC or cls is object:
                break
            # 将弱引用添加到对应类和深度的列表中
            # 使用弱引用避免循环引用导致内存泄漏
            index[cls][absolute_depth].append(weakref.ref(listener))

        # 递归注册其子监听器
        # relative_depth + 1: 子节点比当前 listener 深一层
        for child in listener.children.values():
            self._register_to_class_index(child, relative_depth + 1)

    def _unregister_from_class_index(self, listener: 'Listener'):
        """
        从根节点的类索引中移除监听器及其所有后代

        工作原理：
        1. 遍历 listener 的所有父类
        2. 从每个类的索引中移除该 listener 的弱引用
        3. 递归移除 listener 的所有子节点

        注意：
        - 需要遍历所有深度，因为不知道 listener 在哪个深度
        - 使用 list() 创建副本避免在迭代时修改字典
        - 清理空的深度字典和类字典，保持索引整洁

        Args:
            listener: 要移除的监听器
        """
        root = self.root
        index = root._class_index

        # 移除该监听器本身
        for cls in type(listener).__mro__:
            # 停止条件：到达 Listener 基类或更上层
            if cls is Listener or cls is ABC or cls is object:
                break
            if cls in index:
                # 遍历所有深度，过滤掉该监听器的弱引用
                # 使用 list() 创建副本，避免在迭代时修改字典
                for depth in list(index[cls].keys()):
                    # 过滤条件：
                    # 1. ref() is not None: 弱引用仍然有效
                    # 2. ref() is not listener: 不是要移除的监听器
                    index[cls][depth] = [
                        ref for ref in index[cls][depth]
                        if ref() is not None and ref() is not listener
                    ]
                    # 如果该深度的列表为空，删除该深度
                    if not index[cls][depth]:
                        del index[cls][depth]
                # 如果该类的所有深度都为空，删除该类
                if not index[cls]:
                    del index[cls]

        # 递归移除其子监听器
        for child in listener.children.values():
            self._unregister_from_class_index(child)

    def _cleanup_class_index(self):
        """
        清理类索引中的无效弱引用

        工作原理：
        - 遍历所有类和深度，移除已被垃圾回收的弱引用
        - 清理空的深度字典和类字典

        调用时机：
        - 在 find_child_by_class 中，当所有引用都失效时自动调用
        - 定期清理，保持索引整洁，避免内存泄漏

        注意：
        - 使用 list() 创建副本，避免在迭代时修改字典
        """
        index = self._class_index
        for cls in list(index.keys()):
            # 遍历所有深度，过滤掉无效的弱引用
            for depth in list(index[cls].keys()):
                # ref() is not None: 弱引用仍然有效（对象未被垃圾回收）
                index[cls][depth] = [ref for ref in index[cls][depth] if ref() is not None]
                if not index[cls][depth]:
                    del index[cls][depth]
            # 如果该类的所有深度都为空，删除该类
            if not index[cls]:
                del index[cls]

    def find_child_by_class(self, cls: Type[T]) -> Optional[T]:
        """
        按类查找第一个匹配的子监听器

        从根节点的类索引中查找，O(1) 复杂度。
        返回深度最浅的第一个匹配项。

        工作原理：
        1. 从根节点的类索引中获取该类的深度映射
        2. 找到最小深度（最浅的节点）
        3. 返回该深度的第一个有效实例

        性能：
        - 查找类：O(1) - 字典查找
        - 找最小深度：O(k) - k 为深度数量，通常很小
        - 总体：O(1) 常数时间复杂度

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

        depth_map = root._class_index[cls]
        # 找到最小深度（最浅的节点优先）
        min_depth = min(depth_map.keys())

        # 返回最小深度的第一个有效实例
        for ref in depth_map[min_depth]:
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

        工作原理：
        1. 从根节点的类索引中获取该类的深度映射
        2. 按深度顺序遍历（sorted 自动排序）
        3. 收集所有有效实例

        性能：
        - 查找类：O(1) - 字典查找
        - 排序深度：O(k log k) - k 为深度数量，通常很小
        - 遍历实例：O(n) - n 为匹配数量
        - 总体：O(n) 线性时间复杂度

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

        depth_map = root._class_index[cls]
        result = []

        # 按深度顺序遍历（自动排序，浅的在前）
        for depth in sorted(depth_map.keys()):
            for ref in depth_map[depth]:
                instance = ref()
                if instance is not None:
                    result.append(instance)

        return result

    def find_children_by_class_at_depth(self, cls: Type[T], depth: int) -> list[T]:
        """
        按类和深度查找子监听器

        从根节点的类索引中查找指定深度的匹配项，O(1) 复杂度。

        工作原理：
        1. 从根节点的类索引中获取该类的深度映射
        2. 直接访问指定深度的列表（O(1) 字典查找）
        3. 收集所有有效实例

        性能：
        - 查找类：O(1) - 字典查找
        - 查找深度：O(1) - 字典查找
        - 遍历实例：O(n) - n 为该深度的匹配数量
        - 总体：O(1) 常数时间复杂度（不考虑结果数量）

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

        depth_map = root._class_index[cls]
        if depth not in depth_map:
            return []

        result = []
        for ref in depth_map[depth]:
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

    async def __start_internal(self, recursive: bool = True):
        self.enabled = True
        if not self.disable_tick:
            if self.state == ListenerState.STOPPED:
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
        self.logger.info("listener started")
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
        self.logger.info("listener stopped")
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
            # 启动子节点：STARTING 状态（on_start 中创建）或 RUNNING 状态（on_tick 中创建）
            if self.state in (ListenerState.STARTING, ListenerState.RUNNING):
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
