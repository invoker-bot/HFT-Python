"""
应用工厂模块

负责 Listener 实例的缓存、恢复和定期保存。
"""
import time
import logging
import pickle
import threading
from os import makedirs, path, replace
from typing import TYPE_CHECKING, Any, Dict, Optional, Type, TypeVar
from .base import AppCore
from .config import AppConfigPath

if TYPE_CHECKING:
    from .config import AppConfig
    from ...config.base import BaseConfig, BaseConfigPath
    from ..listener import Listener

logger = logging.getLogger(__name__)

T = TypeVar('T', bound='Listener')


class AppFactory:
    """
    应用工厂

    负责 Listener 实例的缓存、恢复和定期保存。

    特性：
    - get_or_create() 从缓存获取或创建 Listener 实例
    - collect() 收集 Listener 树的状态
    - 守护线程定期保存缓存
    - AppCore 退出时同步保存
    - 使用 threading.RLock 保护写操作
    - 原子写入（临时文件 + 重命名）
    """

    @staticmethod
    def build_cache_key(
        name: str,
        parent: Optional['Listener'] = None
    ) -> str:
        """
        构建缓存键

        格式："parent_key/name"

        Args:
            name: Listener 名称
            parent: 父 Listener

        Returns:
            缓存键字符串
        """
        current = name
        if parent is None:
            return current

        # 递归构建父路径
        parent_key = AppFactory.build_cache_key(parent.name, parent.parent)
        return f"{parent_key}/{current}"

    def __init__(self, app_name: str, restore_cache: bool = True):
        """
        初始化应用工厂

        Args:
            app_name: 应用名称, 也是加载路径（如 "main"）
            restore_cache: 是否从缓存恢复状态
        """
        self.app_name = app_name
        self.restore_cache = restore_cache
        self.config_cache = {}
        self.config_path = AppConfigPath(app_name)
        self.config: 'AppConfig' = self.get_or_create_config(self.config_path)  # 加载配置文件
        # 设置缓存文件路径
        self.cache_file = self.config.data_path
        # 加载缓存
        self._cache = {}
        if restore_cache:
            self.load_cache()
        self._instance_cache: Dict[str, Listener] = {}
        self._lock = threading.RLock()
        self._daemon_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._app_core: Optional['AppCore'] = None

    def get_or_create_config(self, config_path: 'BaseConfigPath') -> 'BaseConfig':
        key = path.join(config_path.class_dir, config_path.name)
        if key not in self.config_cache:
            config = config_path.load()
            self.config_cache[key] = config
        return self.config_cache[key]

    def get_or_create_configurable_instance(self, config_path: 'BaseConfigPath', parent: Optional['Listener'] = None, **kwargs):
        config = self.get_or_create_config(config_path)
        return self.get_or_create(
            listener_class=config.get_class_type(),
            name=config_path.name,
            parent=parent,
            config=config,
            **kwargs,
        )

    @property
    def interval(self) -> float:
        """获取缓存保存间隔"""
        return self.config.cache_interval

    @property
    def cache(self) -> Dict[str, Dict[str, Any]]:
        """获取缓存字典"""
        return self._cache

    def get_or_create_app_core(self) -> 'AppCore':
        """
        创建或恢复 AppCore 实例

        Returns:
            AppCore 实例
        """
        app_core = self.get_or_create_configurable_instance(self.config_path, factory=self)
        self._app_core = app_core
        return app_core

    def get_or_create(
        self,
        listener_class: Type[T],
        name: Optional[str] = None,
        parent: Optional['Listener'] = None,
        **kwargs  # 构造函数参数
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
        kwargs['name'] = name
        kwargs['parent'] = parent
        cache_key = self.build_cache_key(name, parent)
        if cache_key in self._instance_cache:
            instance = self._instance_cache[cache_key]  # 已存在实例，直接返回
        elif cache_key in self._cache:
            # 从缓存恢复
            state = self._cache[cache_key]
            state['kwargs'] = kwargs  # 将构造函数参数传入
            instance = listener_class.__new__(listener_class)
            instance.__setstate__(state)
        else:
            # 如果构造函数接受 name 参数，则传递；否则不传递
            instance = listener_class(**kwargs)
        instance.enabled = True  # 启用实例
        assert id(instance.parent) == id(parent), "Parent mismatch"
        assert parent is None or id(instance) == id(parent.children[instance.name]), "Child not registered in parent"
        self._instance_cache[cache_key] = instance
        return instance

    def start_daemon(self, app_core: 'AppCore'):
        """
        启动守护线程定期保存缓存

        Args:
            app_core: AppCore 实例
        """
        self._app_core = app_core
        self._stop_event.clear()
        if self._daemon_thread is not None:
            raise RuntimeError("Cache daemon already started")
        self._daemon_thread = threading.Thread(
            target=self._daemon_loop,
            name="CacheDaemon",
            daemon=True
        )
        self._daemon_thread.start()
        logger.info("Cache daemon started (interval=%.1fs)", self.interval)

    def stop_daemon(self):
        """停止守护线程"""
        daemon_thread = self._daemon_thread
        if daemon_thread and daemon_thread.is_alive():
            self._stop_event.set()
            daemon_thread.join(timeout=5.0)
            logger.info("Cache daemon stopped")
            self._daemon_thread = None

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

    def update_cache(self):
        """
        更新缓存字典

        增量合并当前 Listener 树的状态，并清除已过期的缓存条目。
        过期判断：cache_time 不为 None 且 time.time() - update_time > cache_time。
        """
        self._cache.update(self.collect(self._app_core))
        for key, listener in list(self._cache.items()):
            cache_time = listener.get("cache_time", None)
            update_time = listener.get("update_time", None)
            if cache_time is not None and update_time is not None:
                if time.time() - update_time > cache_time:
                    logger.debug("Cache expired for %s, removing from cache", key)
                    del self._cache[key]
        return self._cache

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
                # 收集所有 Listener 状态
                cache_dict = self.update_cache()

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
        cache_key = self.build_cache_key(listener.name, parent)

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
    def load_cache_from_file(cache_file: str) -> Dict[str, Dict[str, Any]]:
        """
        从缓存文件加载状态字典（静态方法）

        Args:
            cache_file: 缓存文件路径

        Returns:
            缓存字典 {cache_key: state_dict}
        """
        try:
            with open(cache_file, 'rb') as f:
                cache_dict = pickle.load(f)
            return cache_dict
        except FileNotFoundError:  # 文件不存在，返回空字典
            return {}

    def load_cache(self) -> Dict[str, Dict[str, Any]]:
        """
        从缓存文件加载状态字典（实例方法）

        Returns:
            缓存字典 {cache_key: state_dict}
        """
        self._cache.update(self.load_cache_from_file(self.cache_file))
        return self._cache
