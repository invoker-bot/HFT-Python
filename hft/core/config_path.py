"""
ConfigPath - 配置路径系统

提供基于路径的配置加载和缓存机制。
"""
import os
from typing import ClassVar, Optional, TYPE_CHECKING, Any
from functools import cached_property
from pathlib import Path
from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

if TYPE_CHECKING:
    from .config import BaseConfig


class BaseConfigPath:
    """
    配置路径基类

    特性：
    - 基于 HFT_ROOT_PATH 环境变量定位配置文件
    - 支持 load/save 配置
    - 使用 cached_property 缓存配置实例
    - 支持 Pydantic 验证
    """

    class_dir: ClassVar[str] = "conf/"  # 子类覆盖

    def __init__(self, name: str):
        """
        初始化配置路径

        Args:
            name: 配置文件名（不含扩展名）
        """
        self.name = name

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        """
        Pydantic v2 验证器

        支持从字符串创建 ConfigPath 实例
        """
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
        )

    @classmethod
    def _validate(cls, value: Any) -> 'BaseConfigPath':
        """
        验证并转换输入值

        Args:
            value: 输入值（字符串）

        Returns:
            ConfigPath 实例
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(name=value)
        raise ValueError(f"Cannot convert {type(value)} to {cls.__name__}")

    def _get_file_path(self) -> Path:
        """
        获取配置文件的完整路径

        Returns:
            配置文件路径
        """
        root = os.getenv('HFT_ROOT_PATH', '.')
        return Path(root) / self.class_dir / f"{self.name}.yaml"

    def load(self) -> 'BaseConfig':
        """
        从文件加载配置

        Returns:
            配置实例
        """
        from .config import BaseConfig
        file_path = self._get_file_path()
        return BaseConfig.load(str(file_path))

    def save(self, config: 'BaseConfig') -> None:
        """
        保存配置到文件

        Args:
            config: 配置实例
        """
        file_path = self._get_file_path()
        # 确保目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)
        config.save(str(file_path))

    @cached_property
    def instance(self) -> 'BaseConfig':
        """
        获取缓存的配置实例

        Returns:
            配置实例
        """
        return self.load()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


class AppConfigPath(BaseConfigPath):
    """App 配置路径"""
    class_dir: ClassVar[str] = "conf/app/"


class StrategyConfigPath(BaseConfigPath):
    """Strategy 配置路径"""
    class_dir: ClassVar[str] = "conf/strategy/"


class ExecutorConfigPath(BaseConfigPath):
    """Executor 配置路径"""
    class_dir: ClassVar[str] = "conf/executor/"


class ExchangeConfigPath(BaseConfigPath):
    """Exchange 配置路径"""
    class_dir: ClassVar[str] = "conf/exchange/"


class ExchangeConfigPathGroup:
    """
    Exchange 配置路径组

    特性：
    - 管理多个 Exchange 配置路径
    - 支持基于 id_filter 的过滤
    - 使用 younoyou 包实现过滤逻辑
    - 支持 Pydantic 验证
    """

    def __init__(self, paths: list[str]):
        """
        初始化配置路径组

        Args:
            paths: 配置文件名列表
        """
        self.paths = paths

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        """
        Pydantic v2 验证器

        支持从字符串列表创建 ExchangeConfigPathGroup 实例
        """
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.list_schema(core_schema.str_schema()),
        )

    @classmethod
    def _validate(cls, value: Any) -> 'ExchangeConfigPathGroup':
        """
        验证并转换输入值

        Args:
            value: 输入值（字符串列表）

        Returns:
            ExchangeConfigPathGroup 实例
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, list):
            return cls(paths=value)
        raise ValueError(f"Cannot convert {type(value)} to {cls.__name__}")

    def get_id_map(self, id_filter: str = "") -> dict[str, ExchangeConfigPath]:
        """
        根据 id_filter 过滤并返回 exchange 配置路径映射

        Args:
            id_filter: 过滤规则（逗号分隔）
                - 空字符串: 匹配所有
                - "*": 匹配所有
                - "!xxx": 排除 xxx
                - 可以组合: "okx,binance,!okx/test"

        Returns:
            {exchange_path: ExchangeConfigPath}
        """
        from functools import lru_cache
        from fnmatch import fnmatch

        @lru_cache(maxsize=128)
        def _cached_get_id_map(filter_str: str) -> dict[str, ExchangeConfigPath]:
            # 解析过滤规则
            if not filter_str or filter_str == "*":
                # 匹配所有
                return {path: ExchangeConfigPath(path) for path in self.paths}

            # 解析过滤规则
            filters = [f.strip() for f in filter_str.split(",") if f.strip()]
            includes = [f for f in filters if not f.startswith("!")]
            excludes = [f[1:] for f in filters if f.startswith("!")]

            result = {}
            for path in self.paths:
                # 检查是否匹配 include 规则
                if includes:
                    matched = any(fnmatch(path, pattern) for pattern in includes)
                    if not matched:
                        continue

                # 检查是否匹配 exclude 规则
                if excludes:
                    excluded = any(fnmatch(path, pattern) for pattern in excludes)
                    if excluded:
                        continue

                result[path] = ExchangeConfigPath(path)

            return result

        return _cached_get_id_map(id_filter)

    def __repr__(self) -> str:
        return f"ExchangeConfigPathGroup(paths={self.paths!r})"

