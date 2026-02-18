"""
实例方法缓存装饰器
将 self 转换为 id(self)，然后使用 AsyncTTL 包装。
"""
from functools import wraps
from typing import Callable
from cache import AsyncTTL


def instance_cache(ttl: float = 60.0):
    """实例方法缓存装饰器

    将 self 转换为 id(self) 作为缓存键的一部分，
    并用 skip_args=1 跳过 self_obj 参数，避免 self 对象参与缓存键计算。

    Args:
        ttl: 缓存过期时间（秒）
    """
    def decorator(method: Callable) -> Callable:
        @AsyncTTL(time_to_live=ttl, skip_args=1)
        async def cached_func(self_obj, self_id: int, *args, **kwargs):
            return await method(self_obj, *args, **kwargs)

        @wraps(method)
        async def wrapper(self, *args, **kwargs):
            return await cached_func(self, id(self), *args, **kwargs)

        return wrapper

    return decorator
