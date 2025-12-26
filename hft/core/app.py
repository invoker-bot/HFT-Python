import asyncio
from typing import Optional, TYPE_CHECKING
from .listener import Listener
from .unhealthy_restart import UnhealthyRestartListener
from .state_logger import StateLogListener
from .cache import CacheListener
if TYPE_CHECKING:
    from ..config.app import AppConfig


class AppCore(Listener):

    def __init__(self, config: "AppConfig"):
        super().__init__(interval=config.interval)
        self.config = config
        self.add_child(UnhealthyRestartListener(interval=config.health_check_interval))
        self.add_child(StateLogListener(interval=config.log_interval))
        self.add_child(CacheListener(interval=config.cache_interval))

    def loop(self):
        self.logger.info("Starting AppCore loop")
        asyncio.run(self.run_ticks(-1))

    async def on_tick(self):
        pass

    async def run_ticks(self, duration: float,
                        initialize: Optional[bool] = None,
                        finalize: Optional[bool] = None):
        """
        Run the main loop for a specific duration.

        Args:
            duration: Duration in seconds to run. Use -1 for infinite loop.
            initialize: Whether to call start() at the beginning. Defaults to True if duration < 0.
            finalize: Whether to call stop() at the end. Defaults to True if duration < 0.
        """
        self.logger.debug("Running %f total", self.to_duration_string(duration))

        if initialize is None:
            initialize = duration < 0
        if finalize is None:
            finalize = duration < 0
        try:
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
                #     self.logger.error("Error during tick: %s", e, exc_info=True)
        except KeyboardInterrupt:
            self.logger.info("AppCore loop interrupted by user")
        except Exception as e:
            self.logger.error("Error in AppCore loop: %s", e, exc_info=True)
        finally:
            self.logger.info("AppCore loop stopped, total duration: %s", self.to_duration_string(self.uptime))