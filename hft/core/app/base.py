"""
应用核心模块

AppCore 是整个 HFT 系统的入口，负责：
- 管理所有子监听器的生命周期
- 运行主循环并处理异常
- 协调健康检查、状态日志、缓存等功能
"""
import asyncio
from functools import cached_property
from typing import Optional, TYPE_CHECKING
from ...data.database import ClickHouseDatabase
from ...exchange.group import ExchangeGroups
from ..listener import Listener
from .listeners import UnhealthyRestartListener, StateLogListener, CacheListener
if TYPE_CHECKING:
    from .config import AppConfig


class AppCore(Listener):
    """
    应用核心类

    作为所有监听器的根节点，管理整个应用的生命周期。
    默认包含三个子监听器：
    - UnhealthyRestartListener: 自动重启不健康的监听器
    - StateLogListener: 定期输出状态日志
    - CacheListener: 定期保存应用状态到磁盘
    """

    __pickle_exclude__ = (*Listener.__pickle_exclude__, "database")

    def __init__(self, config: "AppConfig"):
        """
        初始化应用核心

        Args:
            config: 应用配置对象
        """
        super().__init__(interval=config.interval)
        self.config = config
        self.add_child(UnhealthyRestartListener(interval=config.health_check_interval))
        self.add_child(StateLogListener(interval=config.log_interval))
        self.add_child(CacheListener(interval=config.cache_interval))
        self.exchange_groups = ExchangeGroups()
        self.add_child(self.exchange_groups)

    @cached_property
    def database(self):
        """
        获取 ClickHouse 数据库连接

        从 database_url 解析连接参数并创建 ClickHouseDatabase 实例。
        URL 格式: clickhouse://user:password@host:port/database
        """
        url = self.config.database_url
        return ClickHouseDatabase(str(url))

    def loop(self):
        """同步启动主循环（阻塞）"""
        self.logger.info("Starting AppCore loop")
        asyncio.run(self.run_ticks(-1))

    def on_reload(self, state):
        super().on_reload(state)
        self.config.instance = self
        self.interval = self.config.interval

    async def on_start(self):
        await super().on_start()
        await self.database.init()

    async def on_tick(self):
        """主循环回调，子类可覆盖实现具体逻辑"""
        # TODO: 根据策略组确定是否停止

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
                    await asyncio.sleep(max(0, loop_start + self.interval - self.current_time))
                except asyncio.CancelledError:
                    self.logger.info("AppCore loop cancelled")
                    break
        finally:
            if finalize:
                await self.stop(True)
