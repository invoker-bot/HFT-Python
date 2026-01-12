"""
应用核心模块

AppCore 是整个 HFT 系统的入口，负责：
- 管理所有子监听器的生命周期
- 运行主循环并处理异常
- 协调健康检查、状态日志、缓存等功能

核心组件（按初始化顺序）：
1. ExchangeGroup - 交易所连接管理
2. DataSourceGroup - 市场数据源管理
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
import asyncio
from functools import cached_property
from typing import Optional, TYPE_CHECKING
from ...database.client import ClickHouseDatabase
from ...exchange.group import ExchangeGroup
from ...datasource.group import DataSourceGroup
from ...strategy.group import StrategyGroup
from ...executor.config import BaseExecutorConfig
from ...executor.base import BaseExecutor
from ..listener import Listener
from .listeners import UnhealthyRestartListener, StateLogListener, CacheListener
from .notify import NotifyService

if TYPE_CHECKING:
    from .config import AppConfig


class AppCore(Listener):
    """
    应用核心类

    作为所有监听器的根节点，管理整个应用的生命周期。

    组件结构：
        AppCore
        ├── UnhealthyRestartListener  # 健康检查与自动重启
        ├── StateLogListener          # 状态日志输出
        ├── CacheListener             # 状态持久化
        ├── ExchangeGroup            # 交易所连接
        │   └── [各交易所实例...]
        ├── DataSourceGroup           # 市场数据源
        │   └── [各数据源实例...]
        ├── StrategyGroup             # 策略组
        │   └── [各策略实例...]
        └── Executor                  # 交易执行器

    数据流（轮询模式）：
        Executor.on_tick()
            -> StrategyGroup.get_aggregated_targets()
                -> 遍历所有 Strategy.get_target_positions_usd()
                -> 聚合（position sum, speed 加权平均）
            -> Executor 计算当前仓位与目标的差值
            -> 执行交易
    """

    __pickle_exclude__ = (*Listener.__pickle_exclude__, "database", "notify")

    def __init__(self, config: "AppConfig"):
        """
        初始化应用核心

        Args:
            config: 应用配置对象
        """
        super().__init__(interval=config.interval)
        self.config = config

        # === 通知服务 ===
        self.notify = NotifyService(self)

        # === 辅助监听器 ===
        self.add_child(UnhealthyRestartListener(interval=config.health_check_interval))
        self.add_child(StateLogListener(interval=config.log_interval))
        self.add_child(CacheListener(interval=config.cache_interval))

        # === 核心组件 ===
        # 1. 交易所连接管理
        self.exchange_group = ExchangeGroup()
        self.add_child(self.exchange_group)

        # 2. 市场数据源管理
        self.datasource_group = DataSourceGroup()
        self.add_child(self.datasource_group)

        # 3. 策略组
        self.strategy_group = StrategyGroup()
        self.add_child(self.strategy_group)

        # 4. 交易执行器（从配置加载）
        executor_config = BaseExecutorConfig.load(config.executor)
        self.executor: BaseExecutor = executor_config.instance
        self.add_child(self.executor)

    @cached_property
    def database(self) -> ClickHouseDatabase | None:
        """
        获取 ClickHouse 数据库连接

        从 database_url 解析连接参数并创建 ClickHouseDatabase 实例。
        URL 格式: clickhouse://user:password@host:port/database

        Returns:
            ClickHouseDatabase 实例，如果未配置 database_url 则返回 None
        """
        url = self.config.database_url
        if url is None:
            return None
        return ClickHouseDatabase(str(url))

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
                           duration if duration > 0 else float('inf'))
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

    def on_reload(self, state):
        super().on_reload(state)
        self.config.instance = self
        self.interval = self.config.interval

    async def on_start(self):
        await super().on_start()
        # 只有配置了数据库才初始化
        if self.config.database_url:
            await self.database.init()
        for child in list(self.children.values()):
            if not child.lazy_start:
                child.enabled = True

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
        # 检查策略组是否已完成
        if self.strategy_group.is_finished:
            self.logger.info("StrategyGroup finished, AppCore exiting")
            return True
        return False

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
                    for child in list(self):
                        await child.update_background_task()
                    # print("on tick:", self.interval)
                    # for child in list(self):
                    #     await child.update_background_task()  # make sure background tasks are updated
                    await asyncio.sleep(max(0, loop_start + self.interval - self.current_time))
                except asyncio.CancelledError:
                    self.logger.info("AppCore loop cancelled")
                    break
        finally:
            if finalize:
                await self.stop(True)
