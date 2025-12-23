from typing import ClassVar
from .base import BaseConfig


class AppConfig(BaseConfig):
    class_name: ClassVar[str] = "app"
    class_dir: ClassVar[str] = "conf/app"
    health_check_interval: float = 60.0
    log_interval: float = 10
