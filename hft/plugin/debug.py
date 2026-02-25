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

    @hookimpl
    async def on_listener_start(self, listener: "Listener"):
        """
        任何 Listener 启动时调用

        Args:
            listener: Listener 实例
        """
        listener.logger.info("listener started")

    @hookimpl
    async def on_listener_stop(self, listener: "Listener"):
        """
        任何 Listener 停止时调用

        Args:
            listener: Listener 实例
        """
        listener.logger.info("listener stopped")

    @hookimpl
    async def on_app_tick(self, app):
        """
        每个 tick 循环调用

        Args:
            app: AppCore 实例
        """
        app.logger.debug("app tick")

pm.register(DebugPlugin())
