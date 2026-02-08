# 数据库模块文档

## 概述

数据库模块负责数据的持久化和缓存管理，采用多层架构：

```
┌─────────────────────────────────────────────────────────────┐
│                      数据访问层                              │
│  HealthyData (单值缓存) / HealthyDataArray (时序缓存)         │
│  - fresh → 直接返回                                         │
│  - expired/dirty → fetch 或等待 watch 填充                   │
└─────────────────────────────────────────────────────────────┘
                           │
         ┌─────────────────┴─────────────────┐
         ▼                                   ▼
┌─────────────────┐                 ┌─────────────────┐
│  Watch (被动)   │                 │  Fetch (主动)    │
│  WebSocket 推送 │                 │  REST 轮询       │
│  低延迟         │                 │  可靠兜底        │
└─────────────────┘                 └─────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   持久化层 (可选)                            │
│  ClickHouse                                                 │
│  - 首次启动加载历史数据到缓存                                 │
│  - 增量写入新数据                                            │
│  - 自动 TTL 过期和聚合压缩                                   │
└─────────────────────────────────────────────────────────────┘
```

## 模块结构

```
hft/database/
├── __init__.py
├── client.py           # ClickHouseDatabase
├── config.py           # 数据库配置
└── controllers/        # 数据库控制器
```

## 缓存层

### HealthyData - 单值缓存

用于缓存单个数据对象（如 positions, balance）。

```python
from hft.core.healthy_data import HealthyData, HealthyDataWithFallback

# 基础用法
ticker = HealthyData[dict](max_age=5.0)
ticker.set({"last": 100.0})

if ticker.is_healthy:
    data = ticker.get()  # 直接返回
else:
    # 数据过期，需要刷新

# 带 fallback
positions = HealthyDataWithFallback(
    max_age=10.0,
    fetch_func=exchange.fetch_positions
)
data = await positions.get_or_fetch()  # 自动刷新
```

### HealthyDataArray - 时序缓存

用于缓存时序数据（如 OHLCV, trades）。

```python
from hft.core.healthy_data import HealthyDataArray

ohlcv = HealthyDataArray[OHLCVData](
    max_seconds=600.0,        # 数据保留时间窗口
)

# 添加数据
ohlcv.append(timestamp, data)

# 健康检查
if ohlcv.is_healthy(start_timestamp, end_timestamp, timeout_threshold=10):
    data = list(ohlcv)
```

## 同步模式

### Watch + Fetch 双通道

关键数据（positions, balance）同时使用两种方式同步：

| 方式 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| Watch | 低延迟、实时 | 可能丢失、连接不稳定 | 主要数据源 |
| Fetch | 可靠、完整 | 延迟高、有频率限制 | 兜底确认 |

```python
class ExchangeBalanceListener(GroupListener):
    def sync_children_params(self):
        params = {}
        for key in exchange.config.ccxt_instances.keys():
            params[f"watch-{key}"] = {"type": "watch", "key": key}
            params[f"fetch-{key}"] = {"type": "fetch", "key": key}
        return params
```

### 数据访问流程

```python
async def get_positions(self):
    # 1. 检查缓存健康
    if self._positions.is_healthy:
        return self._positions.get_unchecked()

    # 2. 不健康时 fetch
    data = await self.fetch_positions()
    self._positions.set(data)
    return data
```

## 持久化层

### ClickHouseDatabase

```python
from hft.database import ClickHouseDatabase

db = ClickHouseDatabase("clickhouse://user:pass@host:8123/database")
await db.init()  # 创建表
```

### Controllers

每种数据类型对应一个 Controller：

| Controller | 表名 | TTL 策略 |
|------------|------|----------|
| OrderBillController | order_bill | 30天删除 |
| BalanceUSDController | balance_usd | 30天聚合，365天删除 |
| OHLCVController | ohlcv | 1天聚合为15分钟，365天删除 |
| TradesController | trades | 10分钟聚合为1分钟，30天删除 |
| TickerController | ticker | 10分钟聚合为1分钟，30天删除 |
| OrderBookController | order_book | 10分钟删除 |

### 可选持久化

某些数据（orderbook, trades）量大，可配置不存储：

```yaml
# conf/app/main.yaml
database_url: clickhouse://localhost:8123/hft
persist:
  order_bill: true
  funding_rate_bill: true
  exchange_state: true
  ohlcv: true
  ticker: true
  ticker_volume: true
  funding_rate: true
  trades: false      # 数据量大，默认关闭
  order_book: false  # 数据量大，默认关闭
```

## DataListener

数据采集监听器基类：

```python
class DataListener(Listener):
    persist_key: str = ""  # 子类覆盖，对应 PersistConfig 中的字段名

    @property
    def db_ready(self) -> bool:
        """检查数据库是否就绪"""
        return self.db is not None and self.db.client is not None

    @property
    def persist_enabled(self) -> bool:
        """检查当前数据类型是否启用持久化"""
        persist_config = self.root.config.persist
        return getattr(persist_config, self.persist_key, True)

    async def on_start(self):
        self.db = self.root.database
```

### 使用示例

```python
class ExchangeBalanceUsdListener(DataListener):
    persist_key = "balance_usd"  # 对应 PersistConfig.balance_usd

    async def on_tick(self):
        # 检查交易所就绪、数据库就绪、持久化启用
        if not self.parent.ready or not self.db_ready or not self.persist_enabled:
            return

        balance = await self.parent.medal_fetch_total_balance_usd()
        positions = await self.parent.medal_fetch_positions()

        controller = BalanceUSDController(self.db)
        await controller.update(position_usd, balance_usd, self.parent)
```

## 设计原则

1. **缓存优先**：内存访问比数据库快，避免重复计算
2. **按需持久化**：大数据量可选不存储
3. **双通道可靠**：Watch 提供实时性，Fetch 提供可靠性
4. **健康检查**：自动判断数据是否可用
5. **TTL 自动清理**：ClickHouse 自动过期和聚合压缩
