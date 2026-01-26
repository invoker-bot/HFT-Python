"""
配置基类模块

提供配置管理的基础功能，包括：
- 交互式配置生成
- YAML 文件读写
- 配置类的自动发现和注册
"""
import inspect
import os
import textwrap
from abc import abstractmethod
from glob import glob
from os import makedirs, path
from pathlib import Path
# from functools import cached_property
from typing import ClassVar, Generic, Optional, Self, Type, TypeVar, Union, Any

import yaml
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.validation import Validator
from promptantic import ModelGenerator
from pydantic import BaseModel, Field, PrivateAttr, GetCoreSchemaHandler
from pydantic_core import core_schema


def prompt_with_completion(message: str, choices: list[str], multiple: bool = False, default: Union[str, list[str]] = "") -> Union[str, list[str]]:
    """
    带自动补全的命令行输入提示

    Args:
        message: 提示信息
        choices: 可选项列表
        multiple: 是否允许多选（逗号分隔）
        default: 默认值

    Returns:
        用户输入的选项（单选返回字符串，多选返回列表）
    """
    def is_in_choices(text: str) -> bool:
        if multiple:
            return all(item.strip() in choices for item in text.split(','))
        else:
            return text in choices
    validator = Validator.from_callable(is_in_choices, error_message="Input not in choices")
    completer = WordCompleter(choices, ignore_case=False)
    result = prompt(f"{message} [{textwrap.shorten(','.join(choices), width=120, placeholder='...')}]: ",
                    completer=completer, validator=validator, default=default)
    if multiple:
        return [item.strip() for item in result.split(',')]
    else:
        return result


T = TypeVar('T')


class BaseConfig(BaseModel, Generic[T]):
    """
    配置基类

    提供配置管理的基础功能：
    - 交互式配置生成 (prompt_for_config)
    - YAML 文件持久化 (save/load)
    - 配置类的自动发现 (all_classes)
    - 延迟实例化 (instance)

    Attributes:
        data_dir: 数据文件存储目录
        class_dir: 配置文件存储目录
        class_name: 配置类名称标识
        path: 配置文件路径名
    """

    data_dir: ClassVar[str] = "data/"
    class_dir: ClassVar[str] = "conf/"
    class_name: ClassVar[Optional[str]] = None
    # _instance: Optional[T] = PrivateAttr(None, init=True)

    @classmethod
    @abstractmethod
    def get_class_type(cls) -> Type[T]:
        """获取配置对应的实例类型，子类必须实现"""

    def create_instance(self) -> T:
        """根据配置创建对应的实例对象"""
        return self.get_class_type()(self)

    # @property
    # def instance(self) -> T:
    #     """根据配置创建并返回对应的实例对象"""
    #     if self._instance is None:
    #         self._instance = self.create_instance()
    #     return self._instance

    # @instance.setter
    # def instance(self, value: T) -> None:
    #     """设置实例对象"""
    #     self._instance = value

    @classmethod
    def all_classes(cls) -> dict[str, type[Self]]:
        """
        递归获取所有配置子类

        Returns:
            字典，键为类名，值为类类型
        """
        result = {}
        if cls.class_name is not None and not inspect.isabstract(cls):
            result[cls.class_name] = cls
        for subcls in cls.__subclasses__():
            result.update(subcls.all_classes())
        return result

    path: str = Field(description="配置文件路径名")

    def __init_subclass__(cls, **kwargs):
        """子类初始化时自动设置默认路径"""
        if len(cls.all_classes()) <= 1:
            cls.model_fields["path"].default = cls.class_name
        else:
            cls.model_fields["path"].default = f"{cls.class_name}/{cls.class_name}"
        return super().__init_subclass__(**kwargs)

    @classmethod
    def get_str_value(cls, field):
        """对于SecretStr类型，获取其明文值"""
        if hasattr(field, 'get_secret_value'):
            return field.get_secret_value()
        return str(field)

    @classmethod
    def prompt_for_config(cls) -> Self:
        """
        交互式创建配置

        通过命令行提示用户输入配置参数，自动生成配置对象。
        如果有多个子类可选，会先提示用户选择配置类型。

        Returns:
            创建的配置实例
        """
        gen = ModelGenerator()
        if len(cls.all_classes()) == 0:
            raise ValueError("No subclasses available for prompting")
        elif len(cls.all_classes()) == 1:
            name, constructor = list(cls.all_classes().items())[0]  # only one option
            print("config class:", name)
            return gen.populate(constructor)
        else:
            names = list(cls.all_classes().keys())
            name = prompt_with_completion("Select config class", names, default=names[0])
            constructor = cls.all_classes()[name]
            print("config class:", name)
            return gen.populate(constructor)

    def get_abs_path(self, cwd: str = '.') -> str:
        """获取配置文件的绝对路径"""
        return path.join(cwd, self.class_dir, f"{self.path}.yaml")

    def save_to_file(self, filepath: Union[str, Path]) -> None:
        data = self.model_dump(mode="json", exclude={"path"})
        data["class_name"] = self.class_name  # add class name for loading
        makedirs(path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

    def save(self, cwd: str = None) -> None:
        """
        保存配置到 YAML 文件

        自动创建目录结构，并在数据中添加 class_name 用于加载时识别类型。
        """
        if cwd is None:
            cwd = os.getenv('HFT_ROOT_PATH', '.')
        filepath = self.get_abs_path(cwd)
        self.save_to_file(filepath)

    @classmethod
    def load_from_file(cls, filepath: Union[str, Path], name: str) -> Self:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        class_name = data.pop("class_name")
        data["path"] = name
        constructor = cls.all_classes()[class_name]
        return constructor(**data)

    @classmethod
    def load(cls, name: str, cwd: str = None) -> Self:
        """
        从 YAML 文件加载配置

        Args:
            pathname: 配置文件路径名（不含扩展名）

        Returns:
            加载的配置实例
        """
        if cwd is None:
            cwd = os.getenv('HFT_ROOT_PATH', '.')
        path_ = path.join(cwd, cls.class_dir, f"{name}.yaml")
        return cls.load_from_file(path_, name=name)

    @classmethod
    def list_configs(cls, cwd: str = None) -> list[str]:
        """
        列出所有已保存的配置文件

        Returns:
            配置文件路径名列表
        """
        if cwd is None:
            cwd = os.getenv('HFT_ROOT_PATH', '.')
        pattern = path.join(cwd, cls.class_dir, "**", "*.yaml")
        files = glob(pattern, recursive=True)
        result = []
        for file in files:
            result.append(path.relpath(path.splitext(file)[0], path.join(cwd, cls.class_dir)))
        return result

    def __getstate__(self):
        return {"path": self.path}

    def __setstate__(self, state):
        path_ = path.join(self.class_dir, f"{state['path']}.yaml")
        with open(path_, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data.pop("class_name", None)
        data["path"] = state["path"]
        self.__init__(**data)


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
        self._instance: Optional[BaseConfig] = None

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
        return BaseConfig.load_from_file(self._get_file_path(), self.name)

    def save(self, config: 'BaseConfig') -> None:
        """
        保存配置到文件

        Args:
            config: 配置实例
        """
        config.save_to_file(self._get_file_path())

    # def instance(self) -> 'BaseConfig':
    #     """
    #     获取缓存的配置实例
    #
    #     Returns:
    #         配置实例
    #     """
    #     if self._instance is None:
    #         raise NotImplementedError("Please implement caching logic here.")
    #     return self._instance
    #     # return self._load()

    # @cached_property
    # def instance(self) -> 'BaseConfig':
    #     """
    #     获取缓存的配置实例
    #
    #     Returns:
    #         配置实例
    #     """
    #     return self._load()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
