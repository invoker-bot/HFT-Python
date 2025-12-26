from os import path
from typing import ClassVar, Type
from pydantic import Field
from .base import BaseConfig
from ..core.app import AppCore
from ..core.cache import CacheListener


class AppConfig(BaseConfig[AppCore]):
    class_name: ClassVar[str] = "app"
    data_dir: ClassVar[str] = "data/app"
    class_dir: ClassVar[str] = "conf/app"

    @property
    def data_path(self) -> str:
        return path.join(self.data_dir, f"{self.path}.pkl")

    @classmethod
    def get_class_type(cls) -> Type[AppCore]:
        return AppCore

    @property
    def instance(self) -> AppCore:
        if path.exists(self.data_path):
            app = CacheListener.load_cache(self.data_path)
            app.config = self
        else:
            app = AppCore(self)
        return app

    interval: float = Field(1.0, description="Main loop interval in seconds")
    health_check_interval: float = Field(60.0, description="Interval for health checks in seconds")
    log_interval: float = Field(120.0, description="Interval for logging in seconds")
    cache_interval: float = Field(300.0, description="Interval for caching in seconds")
    strategies: list[str] = Field(description="List of strategies to be used by the app")
