"""
应用配置模块

定义 AppCore 的配置类，支持：
- 从缓存恢复应用状态
- 配置主循环、健康检查、日志、缓存的时间间隔
- 策略列表配置
- 可选持久化配置
- 缓存管理（守护线程定期保存 + 退出时同步保存）
"""
from os import path, makedirs, replace
import logging
import pickle
import threading
import time
from typing import ClassVar, Type, Dict, Any, Optional, TYPE_CHECKING
from pydantic import Field, ClickHouseDsn, BaseModel
from ...config.base import BaseConfig
from ..listener_cache import ListenerCache
from ..config_path import (
    ExchangeConfigPathGroup,
    StrategyConfigPath,
    ExecutorConfigPath,
)

if TYPE_CHECKING:
    from .base import AppCore

logger = logging.getLogger(__name__)


class CacheManager:
    """
    缓存管理器

    负责定期保存和恢复 Listener 状态，使用守护线程避免阻塞主线程。

    特性：
    - 守护线程定期保存缓存
    - AppCore 退出时同步保存
    - 使用 threading.RLock 保护写操作
    - 原子写入（临时文件 + 重命名）
    """

    def __init__(self, cache_file: str, interval: float = 300.0):
        """
        初始化缓存管理器

        Args:
            cache_file: 缓存文件路径
            interval: 保存间隔（秒）
        """
        self.cache_file = cache_file
        self.interval = interval
        self._lock = threading.RLock()
        self._daemon_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._listener_cache = ListenerCache()
        self._app_core: Optional['AppCore'] = None

    def start_daemon(self, app_core: 'AppCore'):
        """
        启动守护线程定期保存缓存

        Args:
            app_core: AppCore 实例
        """
        self._app_core = app_core
        self._stop_event.clear()

        self._daemon_thread = threading.Thread(
            target=self._daemon_loop,
            name="CacheDaemon",
            daemon=True
        )
        self._daemon_thread.start()
        logger.info("Cache daemon started (interval=%.1fs)", self.interval)

    def stop_daemon(self):
        """停止守护线程"""
        if self._daemon_thread and self._daemon_thread.is_alive():
            self._stop_event.set()
            self._daemon_thread.join(timeout=5.0)
            logger.info("Cache daemon stopped")

    def _daemon_loop(self):
        """守护线程主循环"""
        while not self._stop_event.is_set():
            try:
                # 等待间隔时间或停止信号
                if self._stop_event.wait(timeout=self.interval):
                    break
                # 定期保存缓存
                self.save_cache()
            except Exception as e:
                logger.error("Error in cache daemon: %s", e, exc_info=True)

    def save_cache(self):
        """
        保存缓存到磁盘（线程安全）

        使用 RLock 保护写操作，使用临时文件 + 原子重命名确保文件完整性。
        """
        if not self._app_core:
            return

        with self._lock:
            try:
                # 收集所有 Listener 状态
                cache_dict = self._listener_cache.collect(self._app_core)

                # 序列化
                data = pickle.dumps(cache_dict, protocol=pickle.HIGHEST_PROTOCOL)

                # 写入文件
                self._write_cache_file(data)

                logger.info(
                    "Cache saved to %s (%d bytes, %d listeners)",
                    self.cache_file, len(data), len(cache_dict)
                )
            except Exception as e:
                logger.error("Failed to save cache: %s", e, exc_info=True)

    def _write_cache_file(self, data: bytes):
        """
        写入缓存文件（使用临时文件 + 原子重命名）

        Args:
            data: 序列化后的数据
        """
        temp_file = self.cache_file + '.tmp'
        makedirs(path.dirname(self.cache_file), exist_ok=True)

        with open(temp_file, 'wb') as f:
            f.write(data)
            f.flush()

        # 原子重命名
        replace(temp_file, self.cache_file)

    @staticmethod
    def load_cache(cache_file: str) -> Dict[str, Dict[str, Any]]:
        """
        从缓存文件加载状态字典

        Args:
            cache_file: 缓存文件路径

        Returns:
            缓存字典 {cache_key: state_dict}

        Raises:
            RuntimeError: 如果加载失败
        """
        try:
            with open(cache_file, 'rb') as f:
                cache_dict = pickle.load(f)
            return cache_dict
        except Exception as e:
            raise RuntimeError(f"Failed to load cache from {cache_file}: {e}") from e


class PersistConfig(BaseModel):
    """
    持久化配置

    控制哪些数据类型需要保存到 ClickHouse。
    默认全部启用，大数据量的 trades 和 orderbook 可以关闭。
    """
    order_bill: bool = Field(True, description="订单账单")
    funding_rate_bill: bool = Field(True, description="资金费率账单")
    balance_usd: bool = Field(True, description="账户余额快照")
    positions: bool = Field(True, description="持仓快照")
    balances: bool = Field(True, description="余额明细")
    ohlcv: bool = Field(True, description="K线数据")
    ticker: bool = Field(True, description="Ticker数据")
    trades: bool = Field(False, description="成交记录（数据量大，默认关闭）")
    order_book: bool = Field(False, description="订单簿（数据量大，默认关闭）")


class AppConfig(BaseConfig["AppCore"]):
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

    def get_cache_manager(self) -> CacheManager:
        """
        创建缓存管理器实例

        Returns:
            CacheManager 实例
        """
        return CacheManager(
            cache_file=self.data_path,
            interval=self.cache_interval
        )

    @classmethod
    def get_class_type(cls) -> Type["AppCore"]:
        """返回 AppCore 类型"""
        from .base import AppCore
        return AppCore

    def create_instance(self) -> "AppCore":
        """
        创建 AppCore 实例，支持从缓存恢复

        如果 _cache_dict 存在且包含 AppCore 的缓存，则恢复；
        否则创建新实例。

        Returns:
            AppCore 实例
        """
        from .base import AppCore
        from ..listener_cache import get_or_create

        cache_dict = getattr(self, '_cache_dict', {})

        # 使用 get_or_create 恢复或创建 AppCore
        # 注意：AppCore 没有 parent，所以 parent=None
        app_core = get_or_create(
            cache_dict,
            AppCore,
            name="AppCore",
            parent=None,
            config=self
        )

        return app_core

    @classmethod
    def load_from_path(cls, app: str, restore_cache: bool = True) -> "AppConfig":
        """
        加载应用配置并可选地恢复缓存

        Args:
            app: 应用配置路径（如 "app"）
            restore_cache: 是否从缓存恢复 AppCore 状态（默认 True）

        Returns:
            AppConfig 实例
        """
        config = cls.load(app)
        logger.info("Loaded app config: %s", app)

        # 尝试加载缓存
        data_path = path.join(cls.data_dir, f"{app}.pkl")
        if restore_cache and path.exists(data_path):
            try:
                cache_dict = CacheManager.load_cache(data_path)
                logger.info("Loaded cache from %s (%d listeners)", data_path, len(cache_dict))
                # 将缓存字典存储到 config 中，供 AppCore 使用
                config._cache_dict = cache_dict
            except Exception as e:
                logger.warning("Failed to load cache from %s: %s", data_path, e)
                config._cache_dict = {}
        else:
            config._cache_dict = {}

        return config

    interval: float = Field(1.0, description="主循环间隔（秒）")
    health_check_interval: float = Field(60.0, description="健康检查间隔（秒）")
    log_interval: float = Field(120.0, description="状态日志间隔（秒）")
    cache_interval: float = Field(300.0, description="缓存保存间隔（秒）")

    # 使用配置路径引用
    exchanges: ExchangeConfigPathGroup = Field(description="交易所配置路径组")
    strategy: StrategyConfigPath = Field(description="策略配置路径")
    executor: ExecutorConfigPath = Field(description="执行器配置路径")

    database_url: ClickHouseDsn | None = Field(None, description="ClickHouse 数据库连接 URL（可选）")
    persist: PersistConfig = Field(default_factory=PersistConfig, description="持久化配置")

    # Indicator 配置（Feature 0006）
    indicators: dict[str, dict] = Field(
        default_factory=dict,
        description="指标配置，格式: {indicator_id: {class: 类名, params: {...}}}"
    )

    # Scope 配置（Feature 0012）
    scopes: dict[str, dict] = Field(
        default_factory=dict,
        description="全局 Scope 配置，格式: {scope_class_id: {class_name: 类名, instance_id: 实例ID, vars: [...]}}"
    )

    # 调试和测试
    debug: bool = Field(False, description="调试模式，验证流程而不实际下单")
    max_duration: float | None = Field(None, description="最大运行时长（秒），None 表示无限运行直到策略退出")

    # 通知配置
    notify_urls: list[str] = Field(
        default_factory=list,
        description="Apprise 通知 URL 列表，支持 Telegram/Discord/Slack 等，参考 https://github.com/caronc/apprise"
    )
