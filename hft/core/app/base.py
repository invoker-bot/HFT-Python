"""
应用核心模块

AppCore 是整个 HFT 系统的入口，负责：
- 管理所有子监听器的生命周期
- 运行主循环并处理异常
- 协调健康检查、状态日志、缓存等功能

核心组件（按初始化顺序）：
1. ExchangeGroup - 交易所连接管理
2. IndicatorGroup - 指标管理（Feature 0006/0007）
3. StrategyGroup - 策略管理
4. Executor - 交易执行器

辅助组件：
- UnhealthyRestartListener - 自动重启不健康的监听器
- StateLogListener - 定期输出状态日志
- CacheListener - 定期保存应用状态到磁盘

退出流程（级联退出机制）：
1. Strategy.on_tick() 返回 True -> 策略完成，从 StrategyGroup 中移除
2. StrategyGroup.is_finished 变为 True -> StrategyGroup.on_tick() 返回 True
3. AppCore.on_tick() 检测到策略组完成 -> 返回 True -> 程序正常退出
"""
# pylint: disable=import-outside-toplevel,protected-access
import asyncio
from functools import cached_property
from typing import TYPE_CHECKING, Optional, Union
from ...exchange.group import ExchangeGroup
from ...executor.base import BaseExecutor
from ...indicator.base import BaseIndicator, DEFAULT_DISABLE_SECONDS
from ...indicator.group import IndicatorGroup
from ...plugin import pm
from ..scope.manager import ScopeManager
from ..scope.vm import VirtualMachine
from ..listener import Listener
from .listeners import StateLogListener, UnhealthyRestartListener
from .notify import NotifyService

if TYPE_CHECKING:
    from .config import AppConfig
    from .factory import AppFactory
    from ..scope.base import FlowScopeNode


class AppCore(Listener):
    """
    应用核心类

    作为所有监听器的根节点，管理整个应用的生命周期。

    组件结构：
        AppCore
        ├── UnhealthyRestartListener  # 健康检查与自动重启
        ├── StateLogListener          # 状态日志输出
        ├── ExchangeGroup            # 交易所连接
        │   └── [各交易所实例...]
        ├── IndicatorGroup           # 指标管理（Feature 0006/0007）
        │   └── [各指标实例...]
        ├── ScopeManager             # Scope 管理器（Feature 0012）
        ├── StrategyGroup             # 策略组
        │   └── [各策略实例...]
        └── Executor                  # 交易执行器

    缓存管理：
        - AppFactory（守护线程）：定期保存 Listener 状态到磁盘
        - 退出时同步保存，确保数据不丢失

    数据流（轮询模式）：
        Executor.on_tick()
            -> StrategyGroup.get_aggregated_targets()
                -> 遍历所有 Strategy.get_target_positions_usd()
                -> 聚合（position sum, speed 加权平均）
            -> Executor 计算当前仓位与目标的差值
            -> 执行交易
    """

    __pickle_exclude__ = {*Listener.__pickle_exclude__, "database", "notify", "factory", "config",
                          "exchange_group", "indicator_group", "strategy", "executor", "scope_manager",
                          "vm"}

    def initialize(self, **kwargs):
        """
        初始化 AppCore 的子组件

        所有 add_child() 调用都在这里，支持缓存恢复时的 get_or_create 语义。
        """
        super().initialize(**kwargs)
        self.config: 'AppConfig' = kwargs['config']
        self.factory: 'AppFactory' = kwargs['factory']
        self.notify = NotifyService(self)
        # 确保 config 已设置（正常初始化时已设置，pickle 恢复时需要检查）

        # === 辅助监听器 ===
        # 使用 cache_manager.get_or_create 恢复或创建
        self.factory.get_or_create(
            UnhealthyRestartListener,
            parent=self
        )
        self.factory.get_or_create(
            StateLogListener,
            parent=self
        )

        # === 核心组件 ===
        # 1. 交易所连接管理
        self.exchange_group = self.factory.get_or_create(
            ExchangeGroup,
            parent=self
        )
        # 2. Scope 管理器/VM
        self.vm = VirtualMachine()
        self.scope_manager = self.factory.get_or_create(
            ScopeManager,
            parent=self
        )
        # 3. 交易执行器（从配置路径加载）
        executor_config = self.config.executor
        self.executor: BaseExecutor = self.factory.get_or_create_configurable_instance(
            executor_config,
            parent=self,
        )
        strategy_config = self.config.strategy
        self.strategy = self.factory.get_or_create_configurable_instance(
            strategy_config,
            parent=self
        )
        # 4. 指标管理（Feature 0006/0007）
        self.indicator_group = self.factory.get_or_create(
            IndicatorGroup,
            parent=self
        )

    @cached_property
    def database(self):   #  -> ClickHouseDatabase | None:
        """
        获取 ClickHouse 数据库连接

        从 database_url 解析连接参数并创建 ClickHouseDatabase 实例。
        URL 格式: clickhouse://user:password@host:port/database

        Returns:
            DatabaseClient 实例，如果未配置 database_url 则返回 None
        """
        if self.config.database is None:
            return None
        return self.config.database.instance

    def loop(self):
        """
        同步启动主循环（阻塞）

        运行时长由 config.max_duration 控制：
        - None: 无限运行直到策略退出
        - float: 运行指定秒数后退出
        """
        # 计算运行时长
        duration = -1 if self.config.max_duration is None else self.config.max_duration
        if self.config.debug:
            self.logger.info("Starting AppCore loop (DEBUG mode, duration=%.1fs)",
                             duration if duration > 0 else 99999999)
        else:
            self.logger.info("Starting AppCore loop")

        def exception_handler(loop, context):
            # 忽略关闭时的 CancelledError
            if "exception" in context:
                exc = context["exception"]
                if isinstance(exc, asyncio.CancelledError):
                    return
            # 其他异常正常处理
            loop.default_exception_handler(context)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(exception_handler)
        try:
            # 有限时长运行时也要正常初始化和清理
            loop.run_until_complete(self.run_ticks(duration, initialize=True, finalize=True))
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt")
        finally:
            # 取消所有待处理的任务
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            # 等待所有任务完成取消
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    @property
    def interval(self):
        return self.config.interval

    async def on_start(self):
        await super().on_start()
        # 只有配置了数据库才初始化
        if self.database is not None:
            await self.database.init()
        # if self.config.database_url:
        #     await self.database.init()
        for child in list(self.children.values()):
            if not child.lazy_start:
                child.enabled = True  # set enabled is enough, on_start will be called in background task
        # 启动缓存守护线程
        self.factory.start_daemon(self)
        # 触发插件钩子
        pm.hook.on_app_start(app=self)

    async def on_tick(self) -> bool:
        """
        主循环回调

        检查策略组是否已完成，如果是则返回 True 触发程序退出。

        退出流程：
        1. Strategy.on_tick() 返回 True -> 策略退出
        2. StrategyGroup.is_finished 变为 True -> StrategyGroup.on_tick() 返回 True
        3. AppCore.on_tick() 检测到策略组完成 -> 返回 True -> 程序退出

        Returns:
            True 如果策略组已完成，程序应该退出
        """
        # 触发插件钩子
        pm.hook.on_app_tick(app=self)
        self.logger.info("app tick:")
        # 检查策略组是否已完成
        if self.strategy.finished:
            self.logger.info("StrategyGroup finished, AppCore exiting")
            self.finished = True
            return True
        for child in list(self):
            # if isinstance(child, MedalAmountDataSource):
            if id(child) == id(self):
                continue
            # print(child.__class__.__name__, child.name, "enabled:", child.enabled)
            try:
                await asyncio.wait_for(child.update_background_task(), timeout=30)
            except asyncio.TimeoutError:
                self.logger.exception("Timeout updating background task for %s", child.name)
            # from ...indicator.datasource import MedalAmountDataSource
        return False

    # ============================================================
    # Indicator 查询接口（Feature 0006）
    # ============================================================
    def query_indicator(
        self,
        indicator: Union[str, type['BaseIndicator']],  # 要么传入class, 这种取法的indicator不需要定义
        scope: 'FlowScopeNode'
    ) -> BaseIndicator:
        """
        查询 indicator，支持 lazy 创建和自动启动

        Args:
            indicator: 指标类或配置中的指标 ID 字符串
            scope: FlowScopeNode，决定指标绑定的层级

        Returns:
            BaseIndicator 实例（调用方需自行判断 .ready 状态）
        """
        if isinstance(indicator, str):
            indicator_config = self.config.indicators[indicator]
            indicator_class = BaseIndicator.classes[indicator_config.class_name]
            params = indicator_config.params
            seconds = indicator_config.auto_disable_after_seconds
            indicator_id = f"{scope.scope.id}:{indicator}"
            if indicator_config.namespace is not None:
                params["namespace"] = indicator_config.namespace
        else:
            indicator_class = indicator
            params = {}
            seconds = DEFAULT_DISABLE_SECONDS
            indicator_id = f"{scope.scope.id}:{indicator_class.__name__}"
        indicator_instance = self.factory.get_or_create(indicator_class,
                                               name=indicator_id,
                                               parent=self.indicator_group,
                                               scope=scope,
                                               **params)
        indicator_instance.auto_disable_duration = seconds
        # int("Queried indicator:", indicator_instance, "ready:", indicator_instance.ready,
        #     "parent:", indicator_instance.parent)
        return indicator_instance  # 之后还需要判断是否ready

    async def on_stop(self):
        """停止回调，同步保存缓存并停止守护线程"""
        # 停止守护线程
        self.factory.stop_daemon()
        # 同步保存缓存（确保数据不丢失）
        self.factory.save_cache()
        # 触发插件钩子
        pm.hook.on_app_stop(app=self)
        if self.database is not None:
            await self.database.close()
        await super().on_stop()

    async def run_ticks(self, duration: float,
                        initialize: Optional[bool] = None,
                        finalize: Optional[bool] = None):
        """
        运行主循环

        Args:
            duration: 运行时长（秒），-1 表示无限循环
            initialize: 是否在开始时调用 start()，默认无限循环时为 True
            finalize: 是否在结束时调用 stop()，默认无限循环时为 True
        """
        self.logger.debug("Running %f total", self.to_duration_string(duration))

        if initialize is None:
            initialize = duration < 0
        if finalize is None:
            finalize = duration < 0
        # try:
        try:
            if initialize:
                await self.start(True)
            while duration < 0 or self.current_time - self.start_time < duration:
                try:
                    loop_start = self.current_time
                    # simple sleep interruptions
                    # for child in list(self):
                        # if isinstance(child, MedalAmountDataSource):
                    # print(child.__class__.__name__, child.name, "enabled:", child.enabled)
                    # await asyncio.wait_for(child.update_background_task(), timeout=15)
                    # print("self:", self.state, self.enabled)
                    await self.update_background_task()
                    if self.finished:  # current app is stopped
                        break
                    # print("on tick:", self.interval)
                    # for child in list(self):
                    #     await child.update_background_task()  # make sure background tasks are updated
                    # print("app sleeping for", max(0, loop_start + self.interval - self.current_time))
                    await asyncio.sleep(max(0, loop_start + self.interval - self.current_time))
                except asyncio.CancelledError:
                    self.logger.info("AppCore loop cancelled")
                    break
        finally:
            if finalize:
                await self.stop(True)
