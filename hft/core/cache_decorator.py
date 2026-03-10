"""
缓存装饰器模块

提供同步和异步函数的缓存装饰器，支持基于时间的强制刷新。
"""
import time
import asyncio
import inspect
from functools import wraps
from typing import Callable
from cache import AsyncTTL


def cache_sync(ttl: float = 60.0, cache_none: bool = True):
    """同步函数缓存装饰器（基于时间的强制刷新）

    与 TTLCache 不同，此装饰器会在每次调用时检查缓存是否过期，
    确保即使高频调用也会在 TTL 后刷新数据。

    Args:
        ttl: 缓存过期时间（秒）
        cache_none: 是否缓存 None 值（默认 True）。设为 False 时，
                   返回 None 的调用不会被缓存，下次调用会重新执行。

    Example:
        @cache_sync(ttl=60)
        def expensive_calculation(x, y):
            return x + y

        @cache_sync(ttl=30, cache_none=False)
        def may_return_none():
            if not ready:
                return None
            return compute()
    """
    def decorator(func: Callable) -> Callable:
        cache_dict = {}  # {cache_key: {'value': ..., 'timestamp': ..., 'has_value': ...}}

        def make_key(*args, **kwargs):
            """生成缓存键"""
            # 使用 args 和 sorted kwargs 作为键
            key_parts = [args]
            if kwargs:
                key_parts.append(tuple(sorted(kwargs.items())))
            return tuple(key_parts)

        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = make_key(*args, **kwargs)
            now = time.time()

            if cache_key not in cache_dict:
                cache_dict[cache_key] = {'value': None, 'timestamp': 0, 'has_value': False}

            cache_ = cache_dict[cache_key]
            if not cache_['has_value'] or now - cache_['timestamp'] > ttl:
                result = func(*args, **kwargs)
                # 如果 cache_none=False 且结果为 None，不缓存
                if not cache_none and result is None:
                    return None
                cache_['value'] = result
                cache_['timestamp'] = now
                cache_['has_value'] = True

            return cache_['value']

        return wrapper

    return decorator


def cache_async(ttl: float = 60.0):
    """异步函数缓存装饰器（基于 AsyncTTL）

    使用 AsyncTTL 实现异步函数缓存，支持并发调用的去重。

    Args:
        ttl: 缓存过期时间（秒）

    Example:
        @cache_async(ttl=60)
        async def fetch_data(url):
            return await http_get(url)
    """
    def decorator(func: Callable) -> Callable:
        cached_func = AsyncTTL(time_to_live=ttl)(func)
        return cached_func

    return decorator


def cache(ttl: float = 60.0):
    """通用缓存装饰器（自动判断同步/异步）

    根据函数类型自动选择 cache_sync 或 cache_async。

    Args:
        ttl: 缓存过期时间（秒）

    Example:
        @cache(ttl=60)
        def sync_func():
            return expensive_calculation()

        @cache(ttl=60)
        async def async_func():
            return await fetch_data()
    """
    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):
            return cache_async(ttl)(func)
        else:
            return cache_sync(ttl)(func)

    return decorator


def instance_cache_sync(ttl: float = 60.0, cache_none: bool = True):
    """实例方法同步缓存装饰器（复用 cache_sync）

    为类的实例方法提供缓存，使用 id(self) + 方法参数区分不同调用。

    Args:
        ttl: 缓存过期时间（秒）
        cache_none: 是否缓存 None 值（默认 True）

    Example:
        class MyClass:
            @instance_cache_sync(ttl=60)
            def calculate(self, x):
                return expensive_calculation(x)

            @instance_cache_sync(ttl=30, cache_none=False)
            def may_fail(self):
                if not self.ready:
                    return None
                return self.compute()
    """
    def decorator(method: Callable) -> Callable:
        # 创建辅助函数，接收 self_id 而不是 self 作为缓存键
        def _method_with_id(self_id: int, self_obj, *args, **kwargs):
            return method(self_obj, *args, **kwargs)

        # 使用 cache_sync 装饰辅助函数
        cached_func = cache_sync(ttl, cache_none=cache_none)(_method_with_id)

        @wraps(method)
        def wrapper(self, *args, **kwargs):
            # 调用时传入 id(self) 和 self
            return cached_func(id(self), self, *args, **kwargs)

        return wrapper

    return decorator


def instance_cache_async(ttl: float = 60.0):
    """实例方法异步缓存装饰器

    为类的异步实例方法提供缓存，使用 id(self) 区分不同实例。
    将 self 转换为 id(self) 作为缓存键的一部分，
    并用 skip_args=1 跳过 self_obj 参数，避免 self 对象参与缓存键计算。

    Args:
        ttl: 缓存过期时间（秒）

    Example:
        class MyClass:
            @instance_cache_async(ttl=60)
            async def fetch_data(self, url):
                return await http_get(url)
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


def instance_cache(ttl: float = 60.0):
    """通用实例方法缓存装饰器（自动判断同步/异步）

    根据方法类型自动选择 instance_cache_sync 或 instance_cache_async。

    Args:
        ttl: 缓存过期时间（秒）

    Example:
        class MyClass:
            @instance_cache(ttl=60)
            def sync_method(self):
                return expensive_calculation()

            @instance_cache(ttl=60)
            async def async_method(self):
                return await fetch_data()
    """
    def decorator(method: Callable) -> Callable:
        if asyncio.iscoroutinefunction(method):
            return instance_cache_async(ttl)(method)
        else:
            return instance_cache_sync(ttl)(method)

    return decorator
