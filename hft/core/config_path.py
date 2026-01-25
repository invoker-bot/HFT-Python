"""
ConfigPath - 配置路径系统

提供基于路径的配置加载和缓存机制。
"""
# pylint: disable=import-outside-toplevel
import os
from functools import cached_property, lru_cache
from pathlib import Path
from glob import glob
from typing import Any, ClassVar

import yaml
from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema
from younotyou import Matcher

from ..config.base import BaseConfig


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
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
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

        file_path = self._get_file_path()
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        class_name = data.pop("class_name")
        data["path"] = self.name
        constructor = BaseConfig.all_classes()[class_name]
        return constructor(**data)

    def save(self, config: 'BaseConfig') -> None:
        """
        保存配置到文件

        Args:
            config: 配置实例
        """

        file_path = self._get_file_path()
        # 确保目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)

        data = config.model_dump(mode="json", exclude={"path"})
        data["class_name"] = config.class_name
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

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


@lru_cache(maxsize=1)
def _scan_exchange_config_ids() -> tuple[str, ...]:
    """
    扫描 conf/exchange 目录，返回所有可用的 exchange 配置 ID

    Returns:
        配置 ID 元组（不含 .yaml 扩展名）
    """
    root = os.getenv('HFT_ROOT_PATH', '.')
    pattern = os.path.join(root, 'conf/exchange', '**', '*.yaml')
    files = glob(pattern, recursive=True)

    result = []
    base_dir = os.path.join(root, 'conf/exchange')
    for file in files:
        # 获取相对于 conf/exchange 的路径，去掉 .yaml 扩展名
        rel_path = os.path.relpath(file, base_dir)
        config_id = os.path.splitext(rel_path)[0]
        # 统一使用 / 作为路径分隔符
        config_id = config_id.replace(os.sep, '/')
        result.append(config_id)

    return tuple(sorted(result))


class ExchangeConfigPathGroup:
    """
    Exchange 配置路径组

    特性：
    - 支持 selector 语义（*、!、通配）
    - 扫描并展开全部 exchange config id
    - 支持运行时过滤和分组
    - 支持 Pydantic 验证
    """

    def __init__(self, selectors: list[str]):
        """
        初始化配置路径组

        Args:
            selectors: selector 列表，支持 *、!pattern、pattern
        """
        self.selectors = selectors

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
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
            return cls(selectors=value)
        raise ValueError(f"Cannot convert {type(value)} to {cls.__name__}")

    @staticmethod
    @lru_cache(maxsize=256)
    def _apply_selectors(selectors: tuple[str, ...]) -> frozenset[str]:
        """
        应用 selector 规则，返回匹配的配置 ID 集合

        Args:
            selectors: selector 元组（必须是 tuple 以支持缓存）

        Returns:
            匹配的配置 ID 集合
        """
        # 获取所有可用的配置 ID
        all_ids = set(_scan_exchange_config_ids())

        # 规则 1: 空列表等价于 ["*"]
        if not selectors:
            return frozenset(all_ids)

        # 规则 2: 全部为 exclude，等价于 ["*"] + selectors
        if all(s.startswith("!") for s in selectors):
            selectors = ("*",) + selectors

        # 规则 3: 按顺序应用 selector
        result = set()
        for selector in selectors:
            if selector.startswith("!"):
                # exclude: 从结果集中移除
                pattern = selector[1:]
                matcher = Matcher(include_patterns=[pattern])
                to_remove = {id_ for id_ in result if id_ in matcher}
                result -= to_remove
            else:
                # include: 加入结果集
                pattern = selector
                matcher = Matcher(include_patterns=[pattern])
                to_add = {id_ for id_ in all_ids if id_ in matcher}
                result |= to_add

        return frozenset(result)

    def get_id_map(self, id_filter: str = "") -> dict[str, ExchangeConfigPath]:
        """
        根据 id_filter 过滤并返回 exchange 配置路径映射

        Args:
            id_filter: 过滤规则（逗号分隔的 selector）
                - 空字符串或 "*": 匹配所有
                - "!xxx": 排除 xxx
                - 可以组合: "okx/*,binance/*,!okx/test"

        Returns:
            {exchange_config_id: ExchangeConfigPath}
        """
        # 先应用 selectors 得到基础集合
        base_ids = self._apply_selectors(tuple(self.selectors))

        # 再应用 id_filter
        if not id_filter:
            id_filter = "*"

        filter_selectors = tuple(s.strip() for s in id_filter.split(",") if s.strip())
        filtered_ids = self._apply_selectors(filter_selectors)

        # 取交集
        final_ids = base_ids & filtered_ids

        return {id_: ExchangeConfigPath(id_) for id_ in sorted(final_ids)}

    def get_grouped_id_map(
        self, id_filter: str = "", group_filter: str = ""
    ) -> dict[str, list[str]]:
        """
        根据 id_filter 和 group_filter 过滤并返回分组的配置 ID 映射

        Args:
            id_filter: 配置 ID 过滤规则
            group_filter: 分组过滤规则（应用于 exchange_class_id）

        Returns:
            {exchange_class_id: [exchange_config_id, ...]}
        """
        # 获取过滤后的配置 ID
        id_map = self.get_id_map(id_filter)

        # 按 exchange_class_id 分组
        grouped: dict[str, list[str]] = {}
        for config_id in id_map.keys():
            exchange_class_id = config_id.split("/", 1)[0]
            if exchange_class_id not in grouped:
                grouped[exchange_class_id] = []
            grouped[exchange_class_id].append(config_id)

        # 应用 group_filter
        if not group_filter:
            group_filter = "*"

        if group_filter == "*":
            return grouped

        filter_selectors = tuple(s.strip() for s in group_filter.split(",") if s.strip())

        # 使用 _filter_ids，但应用于 group keys
        all_groups = frozenset(grouped.keys())
        result_groups = self._filter_ids(all_groups, filter_selectors)

        return {k: v for k, v in grouped.items() if k in result_groups}

    def get_grouped_map(
        self, id_filter: str = "", group_filter: str = ""
    ) -> dict[str, list[ExchangeConfigPath]]:
        """
        根据 id_filter 和 group_filter 过滤并返回分组的配置路径映射

        Args:
            id_filter: 配置 ID 过滤规则
            group_filter: 分组过滤规则（应用于 exchange_class_id）

        Returns:
            {exchange_class_id: [ExchangeConfigPath, ...]}
        """
        grouped_id_map = self.get_grouped_id_map(id_filter, group_filter)
        return {
            k: [ExchangeConfigPath(id_) for id_ in v]
            for k, v in grouped_id_map.items()
        }

    @staticmethod
    @lru_cache(maxsize=256)
    def _filter_ids(all_ids: frozenset[str], selectors: tuple[str, ...]) -> frozenset[str]:
        """
        对给定的 ID 集合应用 selector 过滤

        Args:
            all_ids: 所有可用的 ID 集合（frozenset 以支持缓存）
            selectors: selector 元组（必须是 tuple 以支持缓存）

        Returns:
            过滤后的 ID 集合
        """
        # 规则 1: 空列表等价于 ["*"]
        if not selectors:
            return all_ids

        # 规则 2: 全部为 exclude，等价于 ["*"] + selectors
        if all(s.startswith("!") for s in selectors):
            selectors = ("*",) + selectors

        # 规则 3: 按顺序应用 selector
        result = set()
        for selector in selectors:
            if selector.startswith("!"):
                # exclude: 从结果集中移除
                pattern = selector[1:]
                matcher = Matcher(include_patterns=[pattern])
                to_remove = {id_ for id_ in result if id_ in matcher}
                result -= to_remove
            else:
                # include: 加入结果集
                pattern = selector
                matcher = Matcher(include_patterns=[pattern])
                to_add = {id_ for id_ in all_ids if id_ in matcher}
                result |= to_add

        return frozenset(result)

    def __repr__(self) -> str:
        return f"ExchangeConfigPathGroup(selectors={self.selectors!r})"
