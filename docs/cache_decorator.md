# 缓存装饰器使用指南

## 概述

`hft.core.cache_decorator` 提供了一套完整的缓存装饰器，支持同步和异步函数/方法的缓存。

**关键特性**：
- **基于时间的强制刷新**：与 `TTLCache` 不同，即使高频调用也会在 TTL 后刷新数据
- **自动类型检测**：`@cache` 和 `@instance_cache` 自动判断函数是同步还是异步
- **实例隔离**：`instance_cache` 系列装饰器使用 `id(self)` 区分不同实例

## TTLCache 的问题

`cachetools.TTLCache` 使用**惰性过期检查**：

```python
from cachetools import TTLCache, cached

@cached(cache=TTLCache(maxsize=1, ttl=60))
def get_price():
    return fetch_from_api()

# t=0: 第一次调用，价格=100
price1 = get_price()  # 缓存 miss，返回 100

# t=30: 第二次调用
price2 = get_price()  # 缓存 hit，返回 100（旧值）

# t=50: 第三次调用
price3 = get_price()  # 缓存 hit，返回 100（旧值）

# 如果一直每 30 秒调用一次，会永远返回 100
# 即使 API 的实际价格已经变成 200 了
```

**问题**：如果调用间隔小于 TTL，会一直使用最旧的缓存值，永远不会刷新。

## 解决方案

使用 `cache_decorator` 模块的装饰器，它们会在每次调用时检查缓存是否过期：

```python
from hft.core.cache_decorator import cache_sync

@cache_sync(ttl=60)
def get_price():
    return fetch_from_api()

# t=0: 第一次调用，价格=100
price1 = get_price()  # 缓存 miss，返回 100

# t=30: 第二次调用
price2 = get_price()  # 缓存 hit，返回 100（旧值）

# t=70: 第三次调用（超过 60 秒）
price3 = get_price()  # 缓存过期，重新计算，返回 200
```

## API 参考

### 函数级缓存

#### `@cache(ttl=60.0)`

通用缓存装饰器，自动判断函数是同步还是异步。

```python
from hft.core.cache_decorator import cache

@cache(ttl=60)
def sync_func():
    return expensive_calculation()

@cache(ttl=60)
async def async_func():
    return await fetch_data()
```

#### `@cache_sync(ttl=60.0)`

同步函数缓存装饰器。

```python
from hft.core.cache_decorator import cache_sync

@cache_sync(ttl=60)
def expensive_calculation(x, y):
    return x + y
```

#### `@cache_async(ttl=60.0)`

异步函数缓存装饰器，基于 `AsyncTTL` 实现。

```python
from hft.core.cache_decorator import cache_async

@cache_async(ttl=60)
async def fetch_data(url):
    return await http_get(url)
```

### 实例方法级缓存

#### `@instance_cache(ttl=60.0)`

通用实例方法缓存装饰器，自动判断方法是同步还是异步。

```python
from hft.core.cache_decorator import instance_cache

class MyClass:
    @instance_cache(ttl=60)
    def sync_method(self):
        return expensive_calculation()

    @instance_cache(ttl=60)
    async def async_method(self):
        return await fetch_data()
```

#### `@instance_cache_sync(ttl=60.0)`

同步实例方法缓存装饰器。

```python
from hft.core.cache_decorator import instance_cache_sync

class MyClass:
    @instance_cache_sync(ttl=60)
    def calculate(self, x):
        return expensive_calculation(x)
```

**特性**：
- 使用 `id(self)` 区分不同实例
- 每个实例有独立的缓存

```python
obj1 = MyClass()
obj2 = MyClass()

obj1.calculate(10)  # obj1 的缓存
obj2.calculate(10)  # obj2 的缓存（独立）
```

#### `@instance_cache_async(ttl=60.0)`

异步实例方法缓存装饰器。

```python
from hft.core.cache_decorator import instance_cache_async

class MyClass:
    @instance_cache_async(ttl=60)
    async def fetch_data(self, url):
        return await http_get(url)
```

**特性**：
- 使用 `id(self)` 避免 pickle 序列化问题
- 基于 `AsyncTTL` 实现，支持并发调用去重

## 使用场景

### 场景 1：高频调用的数据刷新

```python
class PriceMonitor:
    @instance_cache_async(ttl=5)
    async def get_current_price(self):
        """每 5 秒刷新一次价格"""
        return await self.exchange.fetch_ticker(self.symbol)

monitor = PriceMonitor()

# 每秒调用一次
while True:
    price = await monitor.get_current_price()
    # 前 5 次返回缓存值
    # 第 6 次（5 秒后）重新获取
    await asyncio.sleep(1)
```

### 场景 2：多实例独立缓存

```python
class IndicatorCalculator:
    def __init__(self, symbol):
        self.symbol = symbol

    @instance_cache_sync(ttl=60)
    def calculate_rsi(self):
        """每个交易对独立缓存 RSI"""
        return expensive_rsi_calculation(self.symbol)

btc_calc = IndicatorCalculator("BTC/USDT")
eth_calc = IndicatorCalculator("ETH/USDT")

btc_rsi = btc_calc.calculate_rsi()  # BTC 的缓存
eth_rsi = eth_calc.calculate_rsi()  # ETH 的缓存（独立）
```

### 场景 3：自动类型检测

```python
class DataFetcher:
    @instance_cache(ttl=30)
    def get_config(self):
        """同步方法，自动使用 instance_cache_sync"""
        return load_config_from_file()

    @instance_cache(ttl=30)
    async def get_data(self):
        """异步方法，自动使用 instance_cache_async"""
        return await fetch_from_api()
```

## 注意事项

1. **缓存键**：
   - 函数级缓存：基于函数参数（`*args, **kwargs`）
   - 实例方法缓存：基于 `id(self)` + 方法参数

2. **内存管理**：
   - `cache_sync` 和 `instance_cache_sync` 的缓存会一直保留在内存中
   - 如果有大量不同参数的调用，考虑使用 `maxsize` 限制（需要自定义实现）

3. **线程安全**：
   - `cache_sync` 和 `instance_cache_sync` 不是线程安全的
   - 如果需要线程安全，使用 `threading.Lock` 保护

4. **Pickle 兼容性**：
   - `instance_cache_async` 使用 `id(self)` 避免序列化 `self` 对象
   - 适用于需要 pickle 序列化的场景（如 `AppFactory` 缓存）

## 迁移指南

### 从 TTLCache 迁移

**之前**：
```python
from cachetools import TTLCache, cached

class MyClass:
    @cached(cache=TTLCache(maxsize=1, ttl=60))
    def calculate(self):
        return expensive_calculation()
```

**之后**：
```python
from hft.core.cache_decorator import instance_cache_sync

class MyClass:
    @instance_cache_sync(ttl=60)
    def calculate(self):
        return expensive_calculation()
```

### 从旧的 instance_cache 迁移

**之前**：
```python
from hft.core.cache_decorator import instance_cache

class MyClass:
    @instance_cache(ttl=60)
    async def fetch_data(self):
        return await fetch()
```

**之后**（可选，旧代码仍然兼容）：
```python
from hft.core.cache_decorator import instance_cache_async

class MyClass:
    @instance_cache_async(ttl=60)
    async def fetch_data(self):
        return await fetch()
```

或者继续使用 `@instance_cache`（自动检测）：
```python
from hft.core.cache_decorator import instance_cache

class MyClass:
    @instance_cache(ttl=60)  # 自动使用 instance_cache_async
    async def fetch_data(self):
        return await fetch()
```
