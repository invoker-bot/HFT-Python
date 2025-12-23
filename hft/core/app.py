import asyncio
from typing import Optional
from .listener import Listener
from .state_logger import StateLogger
from ..config.app import AppConfig


class AppCore(Listener):

    def __init__(self, config: AppConfig):
        super().__init__(interval=config.health_check_interval)
        self.config = config
        self.add_child(StateLogger(interval=config.log_interval))

    def loop(self):
        self.logger.info("Starting AppCore loop")
        asyncio.run(self.run_ticks(-1))
    
    async def tick_callback(self):
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
        self.logger.debug("Running %f total seconds", duration)

        if initialize is None:
            initialize = duration < 0
        if finalize is None:
            finalize = duration < 0
        self.start_time = self.current_time
        try:
            if initialize:
                await self.start(True, True)
            while duration < 0 or self.current_time - self.start_time < duration:
                try:
                    loop_start = self.current_time
                    await self.health_check(True)  # check health of self and children
                    for child in self:
                        if not child.health:
                            await child.restart(True, True)
                    await asyncio.sleep(max(0, loop_start + self.interval - self.current_time))
                except asyncio.CancelledError:
                    self.logger.info("Tick loop cancelled")
                    break
                except Exception as e:
                    self.logger.error("Error during tick: %s", e, exc_info=True)
        except KeyboardInterrupt:
            self.logger.info("AppCore loop interrupted by user")
        except Exception as e:
            self.logger.error("Error in AppCore loop: %s", e, exc_info=True)
        finally:
            if finalize:
                await self.stop(True)
            self.logger.info("AppCore loop stopped, total duration: %s",
                             self.to_duration_string(self.uptime))
