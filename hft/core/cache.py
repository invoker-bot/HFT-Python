import pickle
from os import makedirs, path
from functools import cached_property
from typing import TYPE_CHECKING
from .listener import Listener
if TYPE_CHECKING:
    from .app import AppCore


class CacheListener(Listener):
    """
    A Listener that periodically saves its state to a cache file and can load from it.
    """

    def __init__(self, interval: float = 300.0):
        """
        Args:
            cache_file: Path to the cache file.
            interval: Interval in seconds to save the cache.
        """
        super().__init__(interval=interval)

    @cached_property
    def cache_file(self) -> str:
        root: AppCore = self.root
        return root.config.data_path

    async def on_tick(self):
        self.save_cache()

    def save_cache(self):
        """Save the current state to the cache file."""
        try:
            makedirs(path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'wb') as f:
                pickle.dump(self, f)
            self.logger.info("Cache saved to %s", self.cache_file)
        except Exception as e:
            self.logger.error("Failed to save cache to %s: %s", self.cache_file, e, exc_info=True)

    @classmethod
    def load_cache(cls, cache_file: str) -> "AppCore":
        """Load the state from the cache file."""
        try:
            with open(cache_file, 'rb') as f:
                obj = pickle.load(f)
            obj.logger.info("Cache loaded from %s", cache_file)
            return obj
        except Exception as e:
            raise RuntimeError(f"Failed to load cache from {cache_file}: {e}") from e
