from typing import ClassVar, Type
from pydantic import Field
from .base import BaseConfig
from ..core.app import AppCore


class AppConfig(BaseConfig[AppCore]):
    class_name: ClassVar[str] = "app"
    class_dir: ClassVar[str] = "conf/app"

    @classmethod
    def get_class_type(cls) -> Type[AppCore]:
        return AppCore

    interval: float = Field(1.0, description="Main loop interval in seconds")
    health_check_interval: float = Field(60.0, description="Interval for health checks in seconds")
    log_interval: float = Field(120.0, description="Interval for logging in seconds")
    strategies: list[str] = Field(description="List of strategies to be used by the app")
