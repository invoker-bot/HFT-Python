"""
不健康重启监听器模块

自动检测并重启不健康的监听器，确保系统稳定运行。
"""
from .listener import Listener


class UnhealthyRestartListener(Listener):
    """
    不健康重启监听器

    定期检查所有监听器的健康状态，自动重启不健康的监听器。
    健康检查会触发每个监听器的 on_health_check() 回调。
    """

    def __init__(self, interval: float = 120.0):
        """
        初始化不健康重启监听器

        Args:
            interval: 健康检查间隔（秒），默认 2 分钟
        """
        super().__init__(interval=interval)

    async def on_tick(self):
        """
        定时回调：执行健康检查并重启不健康的监听器

        遍历所有监听器，对已启用但不健康的监听器执行重启操作。
        """
        root = self.root
        assert root is not None, "UnhealthyRestartListener must be attached to a root Listener"
        await root.health_check(True)
        for listener in list(root):
            if listener.enabled and not listener.healthy:
                self.logger.warning("Listener %s is unhealthy, restarting...", listener.name)
                await listener.restart(False)
