from .listener import Listener


class UnhealthyRestartListener(Listener):
    """
    A Listener that automatically restarts itself upon becoming unhealthy.
    """

    def __init__(self, interval: float = 120.0):
        super().__init__(interval=interval)

    async def on_tick(self):
        root = self.root
        assert root is not None, "UnHealthyRestartListener must be attached to a root Listener"
        await root.health_check(True)
        for listener in list(root):
            if listener.enabled and not listener.healthy:
                self.logger.warning("Listener %s is unhealthy, restarting...", listener.name)
                await listener.restart(False)
