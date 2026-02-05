"""
实例方法缓存装饰器

解决 AsyncTTL 在 pickle 序列化后缓存键失效的问题。
将 self 转换为 id(self)，然后使用 AsyncTTL 包装。
"""
from functools import wraps
from typing import Callable
from cache import AsyncTTL


def instance_cache(ttl: float = 60.0):
    """实例方法缓存装饰器工厂函数

    将实例方法转换为使用 id(self) 作为参数的函数，然后使用 AsyncTTL 缓存。
    这样缓存键不包含 self 对象本身，避免 pickle 序列化问题。

    用法：
        @instance_cache(ttl=300.0)
        async def my_method(self, arg1, arg2):
            ...

    Args:
        ttl: 缓存过期时间（秒）
    """
    def decorator(method: Callable) -> Callable:
        # 创建一个接受 self_id 和原方法的缓存函数
        @AsyncTTL(time_to_live=ttl)
        async def cached_func(self_id: int, self_obj, *args, **kwargs):
            # 调用原方法
            return await method(self_obj, *args, **kwargs)

        @wraps(method)
        async def wrapper(self, *args, **kwargs):
            # 将 self 转换为 id，并传递 self 对象
            return await cached_func(id(self), self, *args, **kwargs)

        return wrapper

    return decorator
