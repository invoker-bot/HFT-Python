"""
测试缓存装饰器模块
"""
import time
import asyncio
import pytest
from hft.core.cache_decorator import (
    cache_sync,
    cache_async,
    cache,
    instance_cache_sync,
    instance_cache_async,
    instance_cache,
)


class TestCacheSync:
    """测试同步缓存装饰器"""

    def test_basic_caching(self):
        """测试基本缓存功能"""
        call_count = 0

        @cache_sync(ttl=1.0)
        def func():
            nonlocal call_count
            call_count += 1
            return call_count

        # 第一次调用
        assert func() == 1
        assert call_count == 1

        # 第二次调用，命中缓存
        assert func() == 1
        assert call_count == 1

        # 等待过期
        time.sleep(1.1)

        # 第三次调用，缓存过期
        assert func() == 2
        assert call_count == 2

    def test_high_frequency_refresh(self):
        """测试高频调用时的强制刷新"""
        call_count = 0

        @cache_sync(ttl=0.5)
        def func():
            nonlocal call_count
            call_count += 1
            return call_count

        # 第一次调用
        assert func() == 1

        # 高频调用（间隔 < TTL）
        time.sleep(0.2)
        assert func() == 1  # 缓存命中

        time.sleep(0.2)
        assert func() == 1  # 缓存命中

        # 等待过期
        time.sleep(0.2)  # 总共 0.6 秒，超过 TTL

        # 缓存过期，重新计算
        assert func() == 2
        assert call_count == 2


class TestCacheAsync:
    """测试异步缓存装饰器"""

    @pytest.mark.asyncio
    async def test_basic_caching(self):
        """测试基本缓存功能"""
        call_count = 0

        @cache_async(ttl=1.0)
        async def func():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return call_count

        # 第一次调用
        assert await func() == 1
        assert call_count == 1

        # 第二次调用，命中缓存
        assert await func() == 1
        assert call_count == 1

        # 等待过期
        await asyncio.sleep(1.1)

        # 第三次调用，缓存过期
        assert await func() == 2
        assert call_count == 2


class TestCache:
    """测试通用缓存装饰器"""

    def test_sync_function(self):
        """测试同步函数"""
        call_count = 0

        @cache(ttl=1.0)
        def func():
            nonlocal call_count
            call_count += 1
            return call_count

        assert func() == 1
        assert func() == 1
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_function(self):
        """测试异步函数"""
        call_count = 0

        @cache(ttl=1.0)
        async def func():
            nonlocal call_count
            call_count += 1
            return call_count

        assert await func() == 1
        assert await func() == 1
        assert call_count == 1


class TestInstanceCacheSync:
    """测试实例方法同步缓存装饰器"""

    def test_basic_caching(self):
        """测试基本缓存功能"""

        class MyClass:
            def __init__(self):
                self.call_count = 0

            @instance_cache_sync(ttl=1.0)
            def calculate(self):
                self.call_count += 1
                return self.call_count

        obj = MyClass()

        # 第一次调用
        assert obj.calculate() == 1
        assert obj.call_count == 1

        # 第二次调用，命中缓存
        assert obj.calculate() == 1
        assert obj.call_count == 1

        # 等待过期
        time.sleep(1.1)

        # 第三次调用，缓存过期
        assert obj.calculate() == 2
        assert obj.call_count == 2

    def test_multiple_instances(self):
        """测试多个实例的缓存隔离"""

        class MyClass:
            def __init__(self, value):
                self.value = value

            @instance_cache_sync(ttl=1.0)
            def get_value(self):
                return self.value

        obj1 = MyClass(1)
        obj2 = MyClass(2)

        assert obj1.get_value() == 1
        assert obj2.get_value() == 2

        # 修改值
        obj1.value = 10
        obj2.value = 20

        # 缓存命中，返回旧值
        assert obj1.get_value() == 1
        assert obj2.get_value() == 2

        # 等待过期
        time.sleep(1.1)

        # 缓存过期，返回新值
        assert obj1.get_value() == 10
        assert obj2.get_value() == 20


class TestInstanceCacheAsync:
    """测试实例方法异步缓存装饰器"""

    @pytest.mark.asyncio
    async def test_basic_caching(self):
        """测试基本缓存功能"""

        class MyClass:
            def __init__(self):
                self.call_count = 0

            @instance_cache_async(ttl=1.0)
            async def calculate(self):
                self.call_count += 1
                await asyncio.sleep(0.01)
                return self.call_count

        obj = MyClass()

        # 第一次调用
        assert await obj.calculate() == 1
        assert obj.call_count == 1

        # 第二次调用，命中缓存
        assert await obj.calculate() == 1
        assert obj.call_count == 1

        # 等待过期
        await asyncio.sleep(1.1)

        # 第三次调用，缓存过期
        assert await obj.calculate() == 2
        assert obj.call_count == 2


class TestInstanceCache:
    """测试通用实例方法缓存装饰器"""

    def test_sync_method(self):
        """测试同步方法"""

        class MyClass:
            def __init__(self):
                self.call_count = 0

            @instance_cache(ttl=1.0)
            def calculate(self):
                self.call_count += 1
                return self.call_count

        obj = MyClass()
        assert obj.calculate() == 1
        assert obj.calculate() == 1
        assert obj.call_count == 1

    @pytest.mark.asyncio
    async def test_async_method(self):
        """测试异步方法"""

        class MyClass:
            def __init__(self):
                self.call_count = 0

            @instance_cache(ttl=1.0)
            async def calculate(self):
                self.call_count += 1
                await asyncio.sleep(0.01)
                return self.call_count

        obj = MyClass()
        assert await obj.calculate() == 1
        assert await obj.calculate() == 1
        assert obj.call_count == 1
