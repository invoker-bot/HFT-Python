"""
应用配置模块

定义 AppCore 的配置类，支持：
- 从缓存恢复应用状态
- 配置主循环、健康检查、日志、缓存的时间间隔
- 策略列表配置
"""
from os import path
import logging
from typing import ClassVar, Type
from pydantic import Field, ClickHouseDsn
from ...config.base import BaseConfig
from .base import AppCore
from .listeners import CacheListener

logger = logging.getLogger(__name__)


class AppConfig(BaseConfig[AppCore]):
    """
    应用核心配置类

    Attributes:
        interval: 主循环间隔（秒）
        health_check_interval: 健康检查间隔（秒）
        log_interval: 状态日志间隔（秒）
        cache_interval: 缓存保存间隔（秒）
        strategies: 策略配置路径列表
    """
    class_name: ClassVar[str] = "app"
    data_dir: ClassVar[str] = "data/app"
    class_dir: ClassVar[str] = "conf/app"

    @property
    def data_path(self) -> str:
        """获取数据缓存文件路径"""
        return path.join(self.data_dir, f"{self.path}.pkl")

    @classmethod
    def get_class_type(cls) -> Type[AppCore]:
        """返回 AppCore 类型"""
        return AppCore

    @classmethod
    def load_from_path(cls, app: str) -> "AppConfig":
        """
        获取或恢复应用实例
     
        如果存在缓存文件，从缓存恢复；否则创建新实例。
        """
        data_path = path.join(cls.data_dir, f"{app}.pkl")
        if path.exists(data_path):
            app: 'AppCore' = CacheListener.load_cache(data_path)
            # logger.info("load app from cache: %s", data_path)
            return app.config
        else:
            config = cls.load(app)
            logger.info("create new app: %s", app)
            return config

    interval: float = Field(1.0, description="主循环间隔（秒）")
    health_check_interval: float = Field(60.0, description="健康检查间隔（秒）")
    log_interval: float = Field(120.0, description="状态日志间隔（秒）")
    cache_interval: float = Field(300.0, description="缓存保存间隔（秒）")
    strategies: list[str] = Field(description="策略配置路径列表")
    database_url: ClickHouseDsn = Field(..., description="ClickHouse 数据库连接 URL")
    exchanges: list[str] = Field(description="交易所配置路径列表")
