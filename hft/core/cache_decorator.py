"""
缓存装饰器模块

提供同步和异步函数的缓存装饰器，支持基于时间的强制刷新。
"""
import time
import asyncio
import inspect
from functools import wraps
from typing import Callable, Any
from cache import AsyncTTL


def cache_sync(ttl: float = 60.0):
    """同步函数缓存装饰器（基于时间的强制刷新）

    与 TTLCache 不同，此装饰器会在每次调用时检查缓存是否过期，
    确保即使高频调用也会在 TTL 后刷新数据。

    Args:
        ttl: 缓存过期时间（秒）

    Example:
        @cache_sync(ttl=60)
        def expensive_calculation(x, y):
            return x + y
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

            cache = cache_dict[cache_key]
            if not cache['has_value'] or now - cache['timestamp'] > ttl:
                cache['value'] = func(*args, **kwargs)
                cache['timestamp'] = now
                cache['has_value'] = True

            return cache['value']

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
        if asyncio.iscoroutinefunction(func):
            return cache_async(ttl)(func)
        else:
            return cache_sync(ttl)(func)

    return decorator


def instance_cache_sync(ttl: float = 60.0):
    """实例方法同步缓存装饰器

    为类的实例方法提供缓存，使用 id(self) + 方法参数区分不同调用。

    Args:
        ttl: 缓存过期时间（秒）

    Example:
        class MyClass:
            @instance_cache_sync(ttl=60)
            def calculate(self, x):
                return expensive_calculation(x)
    """
    def decorator(method: Callable) -> Callable:
        cache_dict = {}  # {(instance_id, args, kwargs): {'value': ..., 'timestamp': ..., 'has_value': ...}}

        def make_key(instance_id, args, kwargs):
            """生成缓存键"""
            key_parts = [instance_id, args]
            if kwargs:
                key_parts.append(tuple(sorted(kwargs.items())))
            return tuple(key_parts)

        @wraps(method)
        def wrapper(self, *args, **kwargs):
            cache_key = make_key(id(self), args, kwargs)
            now = time.time()

            if cache_key not in cache_dict:
                cache_dict[cache_key] = {'value': None, 'timestamp': 0, 'has_value': False}

            cache = cache_dict[cache_key]
            if not cache['has_value'] or now - cache['timestamp'] > ttl:
                cache['value'] = method(self, *args, **kwargs)
                cache['timestamp'] = now
                cache['has_value'] = True

            return cache['value']

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
