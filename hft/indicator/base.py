"""
Indicator 指标基类

Feature 0006: Indicator 与 DataSource 统一架构

核心概念：
- BaseIndicator: 所有指标的基类，使用 HealthyDataArray 存储数据
- GlobalIndicator: 全局唯一的指标（如全局资金费率），更长过期时间
- BaseDataSource: 从 exchange 获取数据的特殊 Indicator

事件机制（通过 _event: AsyncIOEventEmitter）：
- update: 新数据写入 _data 后触发，载荷 (timestamp: float, value: T)
- ready: 从 not ready 变为 ready 时触发，载荷 ()
- error: 发生错误时触发，载荷 (error: Exception)

ready_condition 表达式变量：
- timeout: 当前时间与最新数据的时间差（秒）
- cv: 采样间隔变异系数（需要 window）
- range: 覆盖比例（需要 window）

calculate_vars 用途：
- 供 Executor.condition 表达式使用
- 供 Strategy 决策使用
- 不用于 ready_condition 求值
"""
import asyncio
import re
import time
from abc import abstractmethod
from typing import (TYPE_CHECKING, Any, Callable, Generic, Optional, TypeVar,
                    Union)

from pyee.asyncio import AsyncIOEventEmitter

from ..core.healthy_data import HealthyDataArray
from ..core.listener import Listener

if TYPE_CHECKING:
    from ..core.app.core import AppCore


T = TypeVar('T')  # 数据类型

# 默认过期时间（秒）
DEFAULT_EXPIRE_SECONDS = 300.0  # 5 分钟
GLOBAL_EXPIRE_SECONDS = 3600.0  # 1 小时


class BaseIndicator(Listener, Generic[T]):
    """
    指标基类（Feature 0006 统一架构）

    特性：
    1. 使用 HealthyDataArray 存储时序数据
    2. 通过 _event 发出 update/ready/error 事件
    3. 支持 ready_condition 表达式判断就绪状态
    4. 自动过期机制（长时间未 query 自动停止）
    5. interval=None 表示事件驱动，不创建 tick task

    子类需要实现：
    - calculate_vars(direction): 返回变量字典供 Executor 使用
    """

    # 不 pickle 事件发射器
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_event")

    def __init__(
        self,
        name: str,
        window: Optional[float] = 300.0,
        ready_condition: Optional[str] = None,
        expire_seconds: float = DEFAULT_EXPIRE_SECONDS,
        interval: Optional[float] = None,
        debug: bool = False,
        debug_log_interval: Optional[float] = None,
    ):
        """
        Args:
            name: 指标名称
            window: 数据窗口大小（秒），用于 cv/range 计算。None 等价于 0（仅保留最新点）
            ready_condition: 就绪条件表达式，如 "timeout < 60 and cv < 0.8"
            expire_seconds: 过期时间（秒），无 query 后自动停止
            interval: tick 间隔，None 表示事件驱动
            debug: 是否开启调试模式，记录每次 calculate_vars 的结果
            debug_log_interval: debug 日志输出间隔（秒），None 表示每次都输出
        """
        super().__init__(name=name, interval=interval)
        # Issue 0010: 归一化 window，None -> 0
        self._window = 0.0 if window is None else float(window)
        self._ready_condition = ready_condition
        self._expire_seconds = expire_seconds
        self._debug = debug
        self._debug_log_interval = debug_log_interval
        self._last_debug_log_time: float = 0.0  # 上次 debug 日志时间

        # 数据存储（使用归一化后的 window）
        self._data: HealthyDataArray[T] = HealthyDataArray(max_seconds=self._window)

        # 过期追踪
        self._last_touch: float = time.time()

        # ready 状态追踪（用于触发 ready 事件）
        self._was_ready: bool = False

        # requires 标记：是否被 Executor 的 requires 依赖
        # 被依赖的 Indicator 需要在 on_tick() 中定期更新
        self._is_required: bool = False

        # Feature 0012: Scope 注入层级
        self.scope_level: Optional[str] = None

        # 初始化事件发射器
        self._init_event()

    def _init_event(self):
        """初始化事件发射器（pickle 恢复时也需要调用）"""
        self._event = AsyncIOEventEmitter()

    def initialize(self):
        """重写 Listener.initialize，恢复事件发射器"""
        super().initialize()
        self._init_event()

    # ============================================================
    # 数据访问
    # ============================================================

    @property
    def data(self) -> HealthyDataArray[T]:
        """数据数组"""
        return self._data

    @property
    def window(self) -> float:
        """数据窗口大小（秒）"""
        return self._window

    @property
    def cache_size(self) -> int:
        """缓存数据点数量"""
        return len(self._data)

    # ============================================================
    # 事件机制（委托给 _event）
    # ============================================================

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        """注册事件处理器"""
        self._event.on(event, handler)

    def emit(self, event: str, *args: Any) -> None:
        """发出事件"""
        self._event.emit(event, *args)

    def _emit_update(self, timestamp: float, value: T) -> None:
        """发出 update 事件，并检查 ready 状态变化"""
        self.emit("update", timestamp, value)

        # 检查 ready 状态变化
        is_ready_now = self.is_ready()
        if is_ready_now and not self._was_ready:
            self.emit("ready")
        self._was_ready = is_ready_now

    def _emit_error(self, error: Exception) -> None:
        """
        发出 error 事件

        注意：pyee 对 error 事件在无监听器时会 raise，这里兜底处理。
        """
        if self._event.listeners("error"):
            self.emit("error", error)
        else:
            # 无监听器时只记录日志，不抛出
            self.logger.error("Indicator error (no listener): %s", error)

    # ============================================================
    # 过期机制
    # ============================================================

    def touch(self) -> None:
        """更新查询时间，防止过期"""
        self._last_touch = time.time()

    def is_expired(self) -> bool:
        """是否已过期（长时间未被 query）"""
        return time.time() - self._last_touch > self._expire_seconds

    # ============================================================
    # requires 标记（Feature 0005）
    # ============================================================

    @property
    def is_required(self) -> bool:
        """是否被 Executor 的 requires 依赖"""
        return self._is_required

    def set_requires_flag(self, required: bool = True) -> None:
        """
        设置 requires 标记

        被依赖的 Indicator 需要在 on_tick() 中定期更新。
        未被依赖的 Indicator 采用 lazy 计算模式。

        Args:
            required: True 表示被依赖，False 表示不依赖
        """
        self._is_required = required
        if required:
            self.logger.debug("Indicator %s marked as required", self.name)

    def set_ready_condition(self, condition: str) -> None:
        """
        设置 ready_condition 表达式（Feature 0005）

        运行时配置，用于根据环境（测试/生产）调整 ready 判断标准。

        Args:
            condition: ready 条件表达式，如 "timeout < 60 and cv < 0.8"
        """
        self._ready_condition = condition
        self.logger.debug("Indicator %s ready_condition set to: %s", self.name, condition)

    # ============================================================
    # ready 判断
    # ============================================================

    def ready_internal(self) -> bool:
        """
        内部 ready 检查（供子类覆盖）

        默认实现：至少有 1 个数据点。
        子类可以覆盖以实现更严格的 ready 条件。

        Returns:
            True: 数据就绪
            False: 数据未就绪
        """
        return len(self._data) > 0

    def is_ready(self) -> bool:
        """
        根据 ready_condition 表达式判断是否就绪

        流程：
        1. 先调用 ready_internal() 检查数据是否足够
        2. 如果有 ready_condition，进一步求值表达式

        ready_condition 可用变量：
        - timeout: 当前时间与最新数据的时间差（秒）
        - cv: 采样间隔变异系数
        - range: 覆盖比例

        无 window 或 window <= 0 时：
        - cv = 0.0（视为采样均匀）
        - range = 1.0（视为覆盖完整）
        """
        # 1. 先检查内部 ready 状态
        if not self.ready_internal():
            return False

        # 2. 如果有 ready_condition，进一步求值
        if self._ready_condition is None:
            return True

        # 构建求值上下文
        now = time.time()
        context = {
            "timeout": self._data.timeout,
        }

        # cv 和 range 需要 window
        if self._window > 0:
            start_ts = now - self._window
            context["cv"] = self._data.get_cv(start_ts, now)
            context["range"] = self._data.get_range(start_ts, now)
        else:
            # 无 window 时使用默认值
            context["cv"] = 0.0
            context["range"] = 1.0

        # 安全求值
        try:
            result = self._safe_eval(self._ready_condition, context)
            # 确保返回 bool
            return bool(result)
        except Exception as e:
            self.logger.warning(
                "ready_condition eval failed: %s, condition=%s",
                e, self._ready_condition
            )
            return False

    def _safe_eval(self, expr: str, context: dict[str, Any]) -> Any:
        """
        安全求值表达式

        使用 simpleeval 库，显式限制可用的函数和操作符。
        只允许：比较运算、逻辑运算、基本算术运算。
        """
        from simpleeval import DEFAULT_OPERATORS, EvalWithCompoundTypes

        # 创建受限的求值器
        evaluator = EvalWithCompoundTypes(
            names=context,
            functions={},  # 禁用所有函数调用
            operators=DEFAULT_OPERATORS,  # 只允许默认操作符
        )

        try:
            return evaluator.eval(expr)
        except Exception as e:
            raise ValueError(f"Invalid expression: {expr}") from e

    # ============================================================
    # 抽象方法
    # ============================================================

    def _log_calculate_vars(self, direction: int, vars_dict: dict[str, Any]) -> None:
        """
        记录 calculate_vars 的结果（debug 模式）

        Args:
            direction: 交易方向
            vars_dict: calculate_vars 返回的变量字典
        """
        if self._debug:
            self.logger.info(
                "[DEBUG] %s calculate_vars(direction=%d): %s",
                self.name, direction, vars_dict
            )

    @abstractmethod
    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """
        计算并返回该指标提供的变量字典

        Args:
            direction: 交易方向
                - 1: 多头方向（买入开多 / 卖出平空）
                - -1: 空头方向（卖出开空 / 买入平多）

        Returns:
            变量字典，用于 Executor 的 condition 表达式求值
            例如 {"medal_edge": 0.0005, "rsi": 65.0}

        用途：
            - 供 Executor.condition 表达式使用（Feature 0005）
            - 供 Strategy 决策使用
            - 不用于 ready_condition 求值

        注意：
            - 如果开启了 debug 模式，子类应在返回前调用 self._log_calculate_vars(direction, result)
        """
        ...

    # ============================================================
    # Listener 生命周期
    # ============================================================

    async def on_tick(self) -> bool:
        """
        默认 tick 实现

        事件驱动的 indicator（interval=None）不会调用此方法
        """
        return False


class GlobalIndicator(BaseIndicator[T]):
    """
    全局唯一的指标

    特点：
    - 更长的过期时间（默认 1 小时）
    - 不绑定特定交易对
    - 例如：全局资金费率、市场情绪指标

    查询时 exchange_class 和 symbol 传 None
    """

    def __init__(
        self,
        name: str,
        window: float = 300.0,
        ready_condition: Optional[str] = None,
        expire_seconds: float = GLOBAL_EXPIRE_SECONDS,
        interval: Optional[float] = None,
        debug: bool = False,
        debug_log_interval: Optional[float] = None,
    ):
        super().__init__(
            name=name,
            window=window,
            ready_condition=ready_condition,
            expire_seconds=expire_seconds,
            interval=interval,
            debug=debug,
            debug_log_interval=debug_log_interval,
        )


class BaseDataSource(BaseIndicator[T]):
    """
    数据源基类

    从 exchange 获取数据的特殊 Indicator，支持 watch/fetch 两种模式。

    子类需要实现：
    - _watch(): WebSocket 订阅模式
    - _fetch(): REST API 轮询模式
    - calculate_vars(direction): 返回变量字典
    """

    __pickle_exclude__ = (*BaseIndicator.__pickle_exclude__, "_watch_task")

    def __init__(
        self,
        name: str,
        exchange_class: str,
        symbol: str,
        window: float = 300.0,
        ready_condition: Optional[str] = None,
        expire_seconds: float = DEFAULT_EXPIRE_SECONDS,
        interval: Optional[float] = None,
        mode: str = "watch",
        debug: bool = False,
        debug_log_interval: Optional[float] = None,
    ):
        """
        Args:
            name: 数据源名称
            exchange_class: 交易所类名（如 "okx"）
            symbol: 交易对（如 "BTC/USDT:USDT"）
            window: 数据窗口大小（秒）
            ready_condition: 就绪条件表达式
            expire_seconds: 过期时间（秒）
            interval: tick 间隔，None 表示事件驱动
            mode: 数据获取模式，"watch" 或 "fetch"
            debug: 是否开启调试模式
            debug_log_interval: debug 日志输出间隔（秒）
        """
        super().__init__(
            name=name,
            window=window,
            ready_condition=ready_condition,
            expire_seconds=expire_seconds,
            interval=interval,
            debug=debug,
            debug_log_interval=debug_log_interval,
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._mode = mode
        self._watch_task: Optional[asyncio.Task] = None

    @property
    def exchange_class(self) -> str:
        """交易所类名"""
        return self._exchange_class

    @property
    def symbol(self) -> str:
        """交易对"""
        return self._symbol

    @property
    def mode(self) -> str:
        """数据获取模式"""
        return self._mode

    @property
    def exchange(self):
        """
        获取交易所实例

        通过 root.exchange_group 获取对应的交易所实例。
        """
        if self.root is None:
            return None
        exchange_group = getattr(self.root, 'exchange_group', None)
        if exchange_group is None:
            return None
        return exchange_group.get_exchange_by_class(self._exchange_class)

    @abstractmethod
    async def _watch(self) -> None:
        """
        WebSocket 订阅模式

        子类实现，通过 WebSocket 订阅数据更新。
        收到数据后调用 self._data.append(timestamp, value)
        并调用 self._emit_update(timestamp, value)
        """
        ...

    @abstractmethod
    async def _fetch(self) -> None:
        """
        REST API 轮询模式

        子类实现，通过 REST API 获取数据。
        获取数据后调用 self._data.append(timestamp, value)
        并调用 self._emit_update(timestamp, value)
        """
        ...

    async def on_start(self):
        """启动时根据模式选择数据获取方式"""
        await super().on_start()
        if self._mode == "watch":
            # watch 模式：启动后台 watch 任务
            self._watch_task = asyncio.create_task(self._run_watch())
        # fetch 模式由 tick 驱动

    async def _run_watch(self) -> None:
        """运行 watch 任务，带重连逻辑"""
        while True:
            try:
                await self._watch()
            except asyncio.CancelledError:
                self.logger.debug("watch task cancelled")
                break
            except Exception as e:
                self._emit_error(e)
                self.logger.error("watch failed: %s, reconnecting...", e)
                await asyncio.sleep(1.0)  # 重连间隔

    async def on_stop(self):
        """停止时取消 watch 任务"""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                # 捕获其他异常，确保 stop 链路干净
                self.logger.debug("watch task exception on stop: %s", e)
            self._watch_task = None
        await super().on_stop()

    async def on_tick(self) -> bool:
        """
        tick 回调

        fetch 模式下调用 _fetch 获取数据
        """
        if self._mode == "fetch":
            try:
                await self._fetch()
            except Exception as e:
                self._emit_error(e)
                self.logger.error("fetch failed: %s", e)
        return False

