import textwrap
import inspect
from abc import abstractmethod
from glob import glob
from os import path, makedirs
from functools import cached_property
from typing import ClassVar, Self, Optional, Union, Generic, TypeVar, Type
import yaml
from prompt_toolkit import prompt
from prompt_toolkit.validation import Validator
from prompt_toolkit.completion import WordCompleter
from pydantic import BaseModel, Field
from promptantic import ModelGenerator


def prompt_with_completion(message: str, choices: list[str], multiple: bool = False, default: Union[str, list[str]] = "") -> Union[str, list[str]]:
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

    class_dir: ClassVar[str] = "conf/"
    class_name: ClassVar[Optional[str]] = None

    @classmethod
    @abstractmethod
    def get_class_type(cls) -> Type[T]:
        """子类必须实现，返回对应的类型"""
        ...

    @cached_property
    def instance(self) -> T:
        return self.get_class_type()(self)

    @classmethod
    def all_classes(cls) -> dict[str, type[Self]]:
        """获取所有子类（不缓存，避免导入顺序问题）"""
        result = {}
        if cls.class_name is not None and not inspect.isabstract(cls):
            result[cls.class_name] = cls
        for subcls in cls.__subclasses__():
            result.update(subcls.all_classes())
        return result

    path: str = Field(description="The class path name")

    def __init_subclass__(cls, **kwargs):
        if len(cls.all_classes()) <= 1:
            cls.model_fields["path"].default = cls.class_name
        else:
            cls.model_fields["path"].default = f"{cls.class_name}/{cls.class_name}"
        return super().__init_subclass__(**kwargs)

    @classmethod
    def prompt_for_config(cls) -> Self:
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

    @property
    def abs_path(self) -> str:
        return path.join(self.class_dir, f"{self.path}.yaml")

    def save(self):
        data = self.model_dump(mode="json", exclude={"path"})
        data["class_name"] = self.class_name  # add class name for loading
        makedirs(path.dirname(self.abs_path), exist_ok=True)
        with open(self.abs_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

    @classmethod
    def load(cls, pathname: str) -> Self:
        path_ = path.join(cls.class_dir, f"{pathname}.yaml")
        with open(path_, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        class_name = data.pop("class_name")
        data["path"] = pathname
        constructor = cls.all_classes()[class_name]
        return constructor(**data)

    @classmethod
    def list_configs(cls) -> list[str]:
        pattern = path.join(cls.class_dir, "**", "*.yaml")
        files = glob(pattern, recursive=True)
        result = []
        for file in files:
            result.append(path.relpath(path.splitext(file)[0], cls.class_dir))
        return result
