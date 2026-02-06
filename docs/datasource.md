# 数据源模块文档

## 概述

> **重要**：本项目采用 **Indicator 统一架构**（Feature 0006）。
> DataSource 是特殊的 Indicator，统一通过 `IndicatorGroup` 管理。

数据源（DataSource）负责从交易所获取市场数据，是数据驱动执行架构的基础。

```
┌─────────────────────────────────────────────────────────────┐
│                    Indicator 统一架构                        │
├─────────────────────────────────────────────────────────────┤
│  IndicatorGroup (顶层管理器)                                 │
│  ├── GlobalIndicators (全局指标)                            │
│  │   └── GlobalFundingRateDataSource                       │
│  │                                                          │
│  └── LocalIndicators (交易对级指标)                         │
│      └── TradingPairIndicators: "okx:BTC/USDT:USDT"        │
│          ├── DataSource (数据源类 Indicator)                │
│          │   ├── TickerDataSource                          │
│          │   ├── OrderBookDataSource                       │
│          │   ├── TradesDataSource                          │
│          │   └── OHLCVDataSource                           │
│          │                                                  │
│          └── Computed Indicator (计算类 Indicator)          │
│              ├── MidPriceIndicator                         │
│              ├── MedalEdgeIndicator                        │
│              ├── VolumeIndicator                           │
│              └── RSIIndicator                              │
└─────────────────────────────────────────────────────────────┘
```

## 设计理念

### DataSource 是特殊的 Indicator

| 类型 | 数据来源 | 基类 | 示例 |
|------|----------|------|------|
| DataSource | 从 Exchange 获取 | `BaseDataSource` | TickerDataSource, TradesDataSource |
| Computed | 从其他 Indicator 计算 | `BaseIndicator` | RSIIndicator, MedalEdgeIndicator |

两者都：
- 实现 `calculate_vars(direction)` 方法，向 Executor 提供变量
- 通过 `IndicatorGroup.query_indicator()` 统一访问
- 支持 `is_ready()` 检查数据健康状态

### 数据驱动执行

```
DataSource.on_tick()
    │
    ▼ 获取市场数据
HealthyDataArray._data
    │
    ▼ calculate_vars(direction)
Context Variables
    │
    ▼ Executor.evaluate_condition()
执行决策
```

## 模块结构

```
hft/indicator/
├── base.py              # BaseIndicator
├── group.py             # IndicatorGroup, TradingPairIndicators
│
├── datasource/          # 数据源类 Indicator
│   ├── base.py
│   ├── ticker_datasource.py
│   ├── orderbook_datasource.py
│   ├── trades_datasource.py
│   ├── ohlcv_datasource.py
│   └── funding_rate_datasource.py
│
└── computed/            # 计算类 Indicator
    ├── mid_price_indicator.py
    ├── medal_edge_indicator.py
    ├── volume_indicator.py
    └── rsi_indicator.py
```

## 数据源类型

| 类 | 数据类型 | 说明 |
|----|----------|------|
| `TickerDataSource` | Ticker | 最新价格、买卖价 |
| `OrderBookDataSource` | OrderBook | 订单簿深度 |
| `TradesDataSource` | Trade | 成交记录 |
| `OHLCVDataSource` | Candle | K线数据 |
| `FundingRateDataSource` | FundingRate | 资金费率 |

## 使用示例

### 通过 IndicatorGroup 查询

```python
# 获取 IndicatorGroup
indicator_group = app.indicator_group

# 查询数据源（首次调用会创建并启动）
ticker = indicator_group.query_indicator("ticker", "okx", "BTC/USDT:USDT")
trades = indicator_group.query_indicator("trades", "okx", "BTC/USDT:USDT")

# 检查数据是否就绪
if ticker and ticker.is_ready():
    # 获取变量（用于 Executor 条件求值）
    vars = ticker.calculate_vars(direction=1)
    print(f"Last: {vars['last']}, Mid: {vars['mid']}")
```

### 在 Executor 中使用

```yaml
# conf/executor/demo/market_with_ticker.yaml
class_name: market
requires:
  - ticker
condition: "spread < 0.001"  # 价差小于 0.1% 时执行
per_order_usd: 100
```

## calculate_vars 接口

所有 DataSource 必须实现 `calculate_vars` 方法：

### TickerDataSource

```python
def calculate_vars(self, direction: int) -> dict[str, Any]:
    ticker = self._data.latest
    if ticker is None:
        return {"ticker": None, "last": None}
    return {
        "ticker": ticker,
        "last": ticker.last,
        "bid": ticker.bid,
        "ask": ticker.ask,
        "mid": (ticker.bid + ticker.ask) / 2,
        "spread": (ticker.ask - ticker.bid) / ticker.bid,
    }
```

### TradesDataSource

```python
def calculate_vars(self, direction: int) -> dict[str, Any]:
    return {
        "trades": list(self._data),
        "trade_count": len(self._data),
        "last_trade_price": self._data.latest.price if self._data.latest else None,
    }
```

### OrderBookDataSource

```python
def calculate_vars(self, direction: int) -> dict[str, Any]:
    ob = self._data.latest
    if ob is None:
        return {"order_book": None, "best_bid": None, "best_ask": None}
    return {
        "order_book": ob,
        "best_bid": ob.bids[0].price if ob.bids else None,
        "best_ask": ob.asks[0].price if ob.asks else None,
        "bid_depth": sum(b.amount for b in ob.bids),
        "ask_depth": sum(a.amount for a in ob.asks),
    }
```

## HealthyDataArray

DataSource 使用 `HealthyDataArray` 存储数据，提供健康检查：

```python
class HealthyDataArray(Generic[T]):
    """带健康检查的数据数组"""

    @property
    def latest(self) -> Optional[T]:
        """获取最新数据"""

    def is_healthy(self) -> bool:
        """检查数据是否健康"""

    @property
    def timeout(self) -> float:
        """数据超时时间（秒）"""

    @property
    def cv(self) -> float:
        """采样间隔变异系数"""

    @property
    def range(self) -> float:
        """实际覆盖时间 / 期望窗口时间"""
```

### ready_condition

可通过 `ready_condition` 配置数据就绪条件：

```yaml
# conf/app/demo/main.yaml
indicators:
  trades:
    class: TradesDataSource
    params:
      window: 5m  # 支持 duration 字符串：60s, 1m, 5m, 1h, 1d
    ready_condition: "timeout < 60 and cv < 0.8 and range > 0.6"
```

## 生命周期

```
1. 初始化: DataSource 创建后保持 STOPPED 状态
2. 首次 query_indicator(): 调用 start()，开始获取数据
3. 每次 on_tick(): 从 Exchange 获取数据，更新 HealthyDataArray
4. 超时无访问: 自动 stop()（保留缓存数据）
5. 再次 query_indicator(): 重新 start()
```

## 相关文档

- [indicator.md](indicator.md) - 指标模块文档
- [executor.md](executor.md) - 执行器与数据驱动设计
- [listener.md](listener.md) - Listener 基类和生命周期
