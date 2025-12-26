"""
缓存监听器模块

提供应用状态的持久化功能：
- 定期将应用状态序列化到磁盘
- 支持从缓存文件恢复应用状态
"""
import pickle
from os import makedirs, path
from functools import cached_property
from typing import TYPE_CHECKING
from .listener import Listener
if TYPE_CHECKING:
    from .app import AppCore


class CacheListener(Listener):
    """
    缓存监听器

    定期将整个应用状态（包括所有子监听器）保存到磁盘，
    支持在应用重启后从缓存恢复状态。

    缓存文件路径由 AppConfig.data_path 指定。
    """

    def __init__(self, interval: float = 300.0):
        """
        初始化缓存监听器

        Args:
            interval: 保存间隔（秒），默认 5 分钟
        """
        super().__init__(interval=interval)

    @cached_property
    def cache_file(self) -> str:
        """获取缓存文件路径"""
        root: AppCore = self.root
        return root.config.data_path

    async def on_tick(self):
        """定时回调：保存缓存"""
        self.save_cache()

    def save_cache(self):
        """
        保存当前状态到缓存文件

        使用 pickle 序列化整个监听器树。
        自动创建目录结构。
        """
        try:
            makedirs(path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'wb') as f:
                pickle.dump(self, f)
            self.logger.info("Cache saved to %s", self.cache_file)
        except Exception as e:
            self.logger.error("Failed to save cache to %s: %s", self.cache_file, e, exc_info=True)

    @classmethod
    def load_cache(cls, cache_file: str) -> "AppCore":
        """
        从缓存文件加载状态

        Args:
            cache_file: 缓存文件路径

        Returns:
            恢复的 AppCore 实例

        Raises:
            RuntimeError: 如果加载失败
        """
        try:
            with open(cache_file, 'rb') as f:
                obj = pickle.load(f)
            obj.logger.info("Cache loaded from %s", cache_file)
            return obj
        except Exception as e:
            raise RuntimeError(f"Failed to load cache from {cache_file}: {e}") from e
