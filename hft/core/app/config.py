"""
应用配置模块

定义 AppCore 的配置类，支持：
- 从缓存恢复应用状态
- 配置主循环、健康检查、日志、缓存的时间间隔
- 策略列表配置
- 可选持久化配置
- 缓存管理（守护线程定期保存 + 退出时同步保存）
"""
import logging
import pickle
import threading
from os import makedirs, path, replace
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional, Type, TypeVar
from functools import cached_property
from pydantic import BaseModel, ClickHouseDsn, Field
from ...config.base import BaseConfig
from ..config_path import (ExchangeConfigPathGroup, ExecutorConfigPath,
                           StrategyConfigPath)

if TYPE_CHECKING:
    from ..listener import Listener
    from .base import AppCore

logger = logging.getLogger(__name__)

T = TypeVar('T', bound='Listener')


def build_cache_key(
    listener_class: Type['Listener'],
    name: str,
    parent: Optional['Listener'] = None
) -> str:
    """
    构建缓存键

    格式："ClassName:name/parent_key"

    Args:
        listener_class: Listener 类
        name: Listener 名称
        parent: 父 Listener

    Returns:
        缓存键字符串
    """
    current = f"{listener_class.__name__}:{name}"
    if parent is None:
        return current

    # 递归构建父路径
    parent_key = build_cache_key(type(parent), parent.name, parent.parent)
    return f"{current}/{parent_key}"


class CacheManager:
    """
    缓存管理器

    负责 Listener 实例的缓存、恢复和定期保存。

    特性：
    - get_or_create() 从缓存获取或创建 Listener 实例
    - collect() 收集 Listener 树的状态
    - 守护线程定期保存缓存
    - AppCore 退出时同步保存
    - 使用 threading.RLock 保护写操作
    - 原子写入（临时文件 + 重命名）
    """

    def __init__(self, cache_file: str, interval: float = 300.0,
                 cache: Optional[Dict[str, Dict[str, Any]]] = None):
        """
        初始化缓存管理器

        Args:
            cache_file: 缓存文件路径
            interval: 保存间隔（秒）
            cache: 初始缓存字典（可选）
        """
        self._cache: Dict[str, Dict[str, Any]] = cache if cache is not None else {}
        self.cache_file = cache_file
        self.interval = interval
        self._lock = threading.RLock()
        self._daemon_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._app_core: Optional['AppCore'] = None

    @property
    def cache(self) -> Dict[str, Dict[str, Any]]:
        """获取缓存字典"""
        return self._cache

    def get_or_create(
        self,
        listener_class: Type[T],
        name: Optional[str] = None,
        parent: Optional['Listener'] = None,
        **kwargs
    ) -> T:
        """
        从缓存获取或创建 Listener 实例

        如果缓存中存在对应的状态，则创建实例并恢复状态；
        否则创建新实例。

        Args:
            listener_class: Listener 类
            name: Listener 名称（可选，默认使用类名）
            parent: 父 Listener
            **kwargs: 传递给构造函数的参数（仅在创建新实例时使用）

        Returns:
            Listener 实例
        """
        # 如果没有提供 name，使用类名作为默认值
        if name is None:
            name = listener_class.__name__

        cache_key = build_cache_key(listener_class, name, parent)

        if cache_key in self._cache:
            # 从缓存恢复
            state = self._cache[cache_key]
            instance = listener_class.__new__(listener_class)
            instance.__setstate__(state)
        else:
            # 创建新实例
            # 如果构造函数接受 name 参数，则传递；否则不传递
            try:
                instance = listener_class(name=name, **kwargs)
            except TypeError:
                # 构造函数不接受 name 参数，尝试不传递 name
                instance = listener_class(**kwargs)

        # 建立父子关系
        if parent is not None:
            parent.add_child(instance)

        return instance

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
            logger.warning("AppCore not set, cannot save cache")
            return

        with self._lock:
            try:
                # 收集所有 Listener 状态（使用继承的 collect 方法）
                cache_dict = self.collect(self._app_core)

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

    def collect(self, listener: 'Listener') -> Dict[str, Dict[str, Any]]:
        """
        递归收集 Listener 树的状态

        Args:
            listener: 根 Listener

        Returns:
            缓存字典 {cache_key: state_dict}
        """
        result: Dict[str, Dict[str, Any]] = {}
        self._collect_recursive(listener, None, result)
        return result

    def _collect_recursive(
        self,
        listener: 'Listener',
        parent: Optional['Listener'],
        result: Dict[str, Dict[str, Any]]
    ) -> None:
        """
        递归收集单个 Listener 及其子节点的状态

        Args:
            listener: 当前 Listener
            parent: 父 Listener（用于构建 cache key）
            result: 结果字典
        """
        # 构建缓存键
        cache_key = build_cache_key(type(listener), listener.name, parent)

        # 获取状态（不含 children）
        state = listener.__getstate__()
        result[cache_key] = state

        # 递归收集子节点
        for child in listener.children.values():
            self._collect_recursive(child, listener, result)

    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()

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

    @cached_property
    def cache_manager(self) -> CacheManager:
        """
        创建 CacheManager 实例

        Returns:
            CacheManager 实例
        """
        return CacheManager(
            cache_file=self.data_path,
            interval=self.cache_interval,
            cache=None
        )

    @classmethod
    def get_class_type(cls) -> Type["AppCore"]:
        """返回 AppCore 类型"""
        return AppCore

    def create_instance(self) -> "AppCore":
        """
        创建 AppCore 实例，支持从缓存恢复

        如果 cache_manager 存在且包含 AppCore 的缓存，则恢复；
        否则创建新实例。

        Returns:
            AppCore 实例
        """
        return self.cache_manager.get_or_create(
            AppCore,
            name="AppCore",
            parent=None,
            config=self
        )

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
                # 直接创建 CacheManager 并传入缓存字典，覆盖 @cached_property
                config.cache_manager = CacheManager(
                    cache_file=data_path,
                    interval=config.cache_interval,
                    cache=cache_dict
                )
            except Exception as e:
                logger.warning("Failed to load cache from %s: %s", data_path, e)

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
        description=(
            "全局 Scope 配置，格式: "
            "{scope_class_id: {class_name: 类名, instance_id: 实例ID, vars: [...]}}"
        )
    )

    # 调试和测试
    debug: bool = Field(False, description="调试模式，验证流程而不实际下单")
    max_duration: float | None = Field(
        None, description="最大运行时长（秒），None 表示无限运行直到策略退出"
    )

    # 通知配置
    notify_urls: list[str] = Field(
        default_factory=list,
        description=(
            "Apprise 通知 URL 列表，支持 Telegram/Discord/Slack 等，"
            "参考 https://github.com/caronc/apprise"
        )
    )
