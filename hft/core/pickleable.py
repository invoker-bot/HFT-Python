"""
实验性 Pickleable 基类

TODO: 未来可以考虑使用 __getnewargs_ex__ 来支持更复杂的构造参数
注意：此文件为试验性添加，暂未在项目中使用
"""
from typing import Optional


class Pickleable:  # 这是一个可以被打包的类
    __pickle_include__: Optional[set[str]] = None
    __pickle_exclude__: Optional[set[str]] = {
        "args",
        "kwargs",
    }

    def __init__(self, *args, **kwargs):
        self.initialize(args, kwargs)

    def initialize(self, *args, **kwargs):
        pass

    def __getstate__(self) -> dict:
        include_keys = self.__dict__.keys() if self.__pickle_include__ is None else self.__pickle_include__
        state = {k: self.__dict__[k] for k in include_keys if k not in self.__pickle_exclude__}
        assert "cache_time" in state, "must include cache time"
        # state["cache_time"] = self.cache_time
        return state

    def __setstate__(self, state: dict):
        """
        从序列化数据恢复状态

        重新初始化不可序列化的对象（锁、任务、弱引用）。
        children 不再从 pickle 恢复，而是通过 get_or_create 重建。
        """
        # Restore basic attributes
        self.__dict__.update(state)

        # Reinitialize non-serializable objects (including empty _children)
        args = state.get("args", ())
        kwargs = state.get('kwargs', {})
        self.initialize(*args, **kwargs)
