# 数据源模块文档

## 概述

数据源模块采用三层架构，实现市场数据的高效订阅和管理：

```
DataSourceGroup (顶层管理器)
│   - 从 ExchangeGroup 的 load_markets() 同步所有交易对
│   - TradingPairDataSource 持久存在，不会被删除
│
├── TradingPairDataSource: "okx:BTC/USDT:USDT" (中间层)
│   │   - 代表一个 (exchange_class, symbol) 对
│   │   - lazy_start: 初始为 STOPPED，首次 query 时启动
│   │   - 持久存在，可存储元数据
│   │
│   ├── 数据层 (lazy_start=True, auto-stop)
│   │   ├── TickerDataSource    → 5分钟无query → stop()（保留缓存）
│   │   ├── OrderBookDataSource → stop()（保留缓存）
│   │   ├── TradesDataSource    → stop()（保留缓存）
│   │   └── OHLCVDataSource     → stop()（保留缓存）
│   │
│   └── 指标层 (LazyIndicator, 依赖数据层)
│       ├── VWAPIndicator       → 依赖 TradesDataSource
│       ├── SpreadIndicator     → 依赖 OrderBookDataSource
│       └── TradeIntensityIndicator → 依赖 TRADES + ORDER_BOOK
│
└── TradingPairDataSource: "binance:ETH/USDT:USDT"
    └── (空，等待 query)
```

## 设计原则

1. **按需创建**：底层 DataSource 只在 query 时才创建
2. **stop() 而非销毁**：无人查询超时后自动 stop()，保留缓存数据
3. **lazy_start**：数据源初始为 STOPPED 状态，不跟随父节点自动启动
4. **持久中间层**：TradingPairDataSource 持久存在，可存储元数据
5. **资源优化**：watch 操作是资源消耗的主要来源，只管理 watch 层生命周期

## lazy_start 生命周期

```python
# Listener 基类新增 lazy_start 属性
class Listener:
    lazy_start: bool = False  # 默认跟随父节点启动

class BaseDataSource(Listener):
    lazy_start: bool = True   # 数据源不跟随父节点启动

class TradingPairDataSource(GroupListener):
    lazy_start: bool = True   # 交易对容器也不跟随父节点启动
```

生命周期流程：
1. **初始化**：DataSource 创建后保持 STOPPED 状态
2. **首次 query**：调用 `start()`，开始 watch 数据
3. **超时无访问**：调用 `stop()`，停止 watch 但保留缓存
4. **再次 query**：调用 `start()`，快速恢复

## 使用示例

### 数据源查询

```python
# 获取 ticker 数据
ticker_ds = datasource_group.query("okx", "BTC/USDT:USDT", DataType.TICKER)
if ticker_ds:
    data = ticker_ds.get_latest()

# 批量获取多个交易对
sources = datasource_group.query_many(
    "okx",
    ["BTC/USDT:USDT", "ETH/USDT:USDT"],
    DataType.TICKER
)

# 获取 TradingPairDataSource（不创建数据源）
pair = datasource_group.get_trading_pair("okx", "BTC/USDT:USDT")

# 列出所有交易对
all_pairs = datasource_group.list_trading_pairs()
okx_pairs = datasource_group.list_trading_pairs("okx")
```

### 指标查询

```python
from hft.indicator import VWAPIndicator, SpreadIndicator, TradeIntensityIndicator

# 获取 TradingPairDataSource
pair = datasource_group.get_trading_pair("okx", "BTC/USDT:USDT")

# 查询指标（首次调用会创建并启动）
vwap = pair.query_indicator(VWAPIndicator, window=200)
spread = pair.query_indicator(SpreadIndicator)
intensity = pair.query_indicator(TradeIntensityIndicator)

# 获取指标值
if vwap:
    vwap_value = vwap.get_value()

if intensity and intensity.is_ready:
    result = intensity.get_value()
    print(f"buy_k: {result.buy_k}, sell_k: {result.sell_k}")
```

## 数据类型

```python
class DataType(Enum):
    TICKER = "ticker"           # 最新价格
    ORDER_BOOK = "order_book"   # 订单簿
    TRADES = "trades"           # 成交记录
    OHLCV = "ohlcv"             # K线数据
```

## 类层级

### DataSourceGroup

顶层管理器，继承 `GroupListener`。

```python
class DataSourceGroup(GroupListener):
    def sync_children_params(self):
        # 从 ExchangeGroup 获取所有 (exchange_class, symbol) 对
        params = {}
        for exchange in self.exchange_group.children.values():
            for symbol in exchange.market_trading_pairs.keys():
                params[f"{exchange.class_name}:{symbol}"] = {...}
        return params

    def query(self, exchange_class, symbol, data_type) -> BaseDataSource:
        # 委托给 TradingPairDataSource
        pair = self._get_trading_pair_source(exchange_class, symbol)
        return pair.query(data_type)
```

### TradingPairDataSource

中间层，代表一个交易对，继承 `GroupListener`。

```python
class TradingPairDataSource(GroupListener):
    lazy_start: bool = True
    DEFAULT_AUTO_STOP_TIMEOUT: float = 300.0  # 5分钟

    def query(self, data_type: DataType) -> BaseDataSource:
        """获取数据源，如果已 stop 会重新 start"""
        self._last_query_time[data_type] = time.time()

        if child_name in self.children:
            ds = self.children[child_name]
            ds.request_watch()
            if ds.state == ListenerState.STOPPED:
                asyncio.create_task(ds.start())
            return ds

        # 创建新的数据源
        ds = self.create_dynamic_child(...)
        ds.request_watch()
        asyncio.create_task(ds.start())
        return ds

    def query_indicator(self, indicator_class, **kwargs) -> LazyIndicator:
        """获取指标，如果已 stop 会重新 start"""
        ...

    async def on_tick(self) -> bool:
        """检查并停止空闲的数据源（不删除）"""
        for data_type, last_time in self._last_query_time.items():
            if now - last_time > self._auto_stop_timeout:
                if ds.state == ListenerState.RUNNING:
                    await ds.stop()  # 只停止，不销毁
        return False
```

### BaseDataSource

底层数据源，提供 watch + fetch 双通道数据获取。

```python
class BaseDataSource(Listener):
    lazy_start: bool = True  # 不跟随父节点启动

    async def _watch(self) -> T:
        """WebSocket 订阅"""
        ...

    async def _fetch(self) -> T:
        """REST API fallback"""
        ...

    def request_watch(self):
        """刷新 auto-unwatch 计时器"""
        self._last_watch_request = time.time()
```

## 生命周期

1. **启动时**：DataSourceGroup 从 load_markets() 创建所有 TradingPairDataSource（STOPPED 状态）
2. **首次 query**：按需创建具体的 DataSource 并 start()
3. **后续 query**：刷新访问时间，返回现有实例（如已 stop 则重新 start）
4. **超时未访问**：on_tick() 检测并 stop() 空闲的 DataSource（保留缓存）
5. **停止时**：所有 DataSource 停止 watch

## 统计信息

```python
# 获取统计
stats = datasource_group.get_stats()
# {
#     "total_pairs": 1000,
#     "by_exchange": {"okx": 500, "binance": 500},
#     "active_datasources": {"ticker": 10, "order_book": 5},
#     "active_indicators": {"VWAPIndicator": 3, "SpreadIndicator": 2}
# }
```

## 相关文档

- [indicator.md](indicator.md) - 指标模块文档
- [listener.md](listener.md) - Listener 基类和 lazy_start
