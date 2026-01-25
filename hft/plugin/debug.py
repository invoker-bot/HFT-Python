from typing import TYPE_CHECKING
from .base import pm, hookimpl, PluginBase
if TYPE_CHECKING:
    from ..core.listener import Listener


class DebugPlugin(PluginBase):
    """
    调试插件

    提供基本的调试功能，如日志记录和状态输出。
    """

    name: str = "debug_plugin"

    # def __init__(self):
    #     """
    #     初始化调试插件
    #     """
    #     self.config = {}
    @hookimpl
    def on_listener_start(self, listener: "Listener"):
        """
        任何 Listener 启动时调用

        Args:
            listener: Listener 实例
        """
        listener.logger.info("listener started")

    @hookimpl
    def on_listener_stop(self, listener: "Listener"):
        """
        任何 Listener 停止时调用

        Args:
            listener: Listener 实例
        """
        listener.logger.info("listener stopped")


pm.register(DebugPlugin())
