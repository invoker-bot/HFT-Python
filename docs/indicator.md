# 指标模块文档

## 概述

> **核心理念**：本项目采用 **Indicator 统一架构**（Feature 0006）。
> DataSource 是特殊的 Indicator，统一通过 `IndicatorGroup` 管理。

指标模块是数据驱动执行架构的核心，负责：
1. 从交易所获取市场数据（DataSource）
2. 计算派生指标（Computed Indicator）
3. 向 Executor 提供变量（`calculate_vars`）

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
│          ├── DataSource (数据源类)                          │
│          │   ├── TickerDataSource                          │
│          │   ├── OrderBookDataSource                       │
│          │   ├── TradesDataSource                          │
│          │   └── OHLCVDataSource                           │
│          │                                                  │
│          └── Computed (计算类)                              │
│              ├── MidPriceIndicator                         │
│              ├── MedalEdgeIndicator                        │
│              ├── VolumeIndicator                           │
│              └── RSIIndicator                              │
└─────────────────────────────────────────────────────────────┘
```

## 模块结构

```
hft/indicator/
├── base.py              # BaseIndicator, GlobalIndicator, BaseDataSource
├── group.py             # IndicatorGroup, TradingPairIndicators
├── factory.py           # IndicatorFactory 注册表
│
├── datasource/          # 数据源类 Indicator
│   ├── ticker_datasource.py
│   ├── orderbook_datasource.py
│   ├── trades_datasource.py
│   ├── ohlcv_datasource.py
│   └── funding_rate_datasource.py
│
├── computed/            # 计算类 Indicator
│   ├── mid_price_indicator.py
│   ├── medal_edge_indicator.py
│   ├── volume_indicator.py
│   └── rsi_indicator.py
│
├── persist/             # 数据持久化
│   └── listeners.py
│
└── lazy_indicator.py    # LazyIndicator (legacy)
```

## Feature 0005: calculate_vars 接口

所有 Indicator 必须实现 `calculate_vars` 方法，用于向 Executor 提供变量：

```python
@abstractmethod
def calculate_vars(self, direction: int) -> dict[str, Any]:
    """
    计算并返回该指标提供的变量

    Args:
        direction: 交易方向，1 表示多（买入），-1 表示空（卖出）

    Returns:
        变量字典，例如 {"medal_edge": 0.0005, "rsi": 45.0}
    """
    ...
```

### 数据源类示例

```python
class TickerDataSource(BaseDataSource[Ticker]):
    def calculate_vars(self, direction: int) -> dict[str, Any]:
        ticker = self._data.latest
        if ticker is None:
            return {"ticker": None, "last_price": None}
        return {
            "ticker": ticker,
            "last": ticker.last,
            "bid": ticker.bid,
            "ask": ticker.ask,
            "mid": (ticker.bid + ticker.ask) / 2,
        }
```

### 计算类示例

```python
class RSIIndicator(BaseIndicator[float]):
    def calculate_vars(self, direction: int) -> dict[str, Any]:
        if not self.is_ready():
            return {"rsi": 50.0}  # 默认中性值
        return {"rsi": self._calculate_rsi()}
```

## LazyIndicator（Legacy）

> **注意**: 此类为兼容层，新代码推荐使用 `hft/indicator/computed/` 下的 Indicator。

轮询驱动的派生指标，通过 IndicatorGroup 获取依赖的数据源。

### 特性

- **lazy_start**: 初始为 STOPPED，首次 `query_indicator()` 时启动
- **auto-stop**: 5分钟无访问自动 stop()（保留计算结果）

## 内置 Indicator

### 数据源类

| 类 | ID | 提供的变量 |
|----|-----|-----------|
| `TickerDataSource` | ticker | last, bid, ask, mid, spread |
| `OrderBookDataSource` | order_book | best_bid, best_ask, bid_depth, ask_depth |
| `TradesDataSource` | trades | trades, trade_count, last_trade_price |
| `OHLCVDataSource` | ohlcv | ohlcv, candle_count |

### 计算类

| 类 | ID | 依赖 | 提供的变量 |
|----|-----|------|-----------|
| `MidPriceIndicator` | mid_price | order_book | mid_price |
| `MedalEdgeIndicator` | medal_edge | trades | medal_edge, medal_buy_edge, medal_sell_edge |
| `VolumeIndicator` | volume | trades | volume, buy_volume, sell_volume |
| `RSIIndicator` | rsi | ohlcv | rsi |

## 在 Executor 中使用

```yaml
# conf/executor/demo/market_rsi.yaml
class_name: market
requires:
  - rsi
  - medal_edge
condition: "(buy and rsi < 30) or (sell and rsi > 70)"
per_order_usd: 100
```

Executor 通过 `requires` 声明依赖，系统自动：
1. 查询所需 Indicator
2. 检查 `is_ready()` 状态
3. 调用 `calculate_vars(direction)` 收集变量
4. 注入到 condition 表达式上下文

## 相关文档

- [datasource.md](datasource.md) - 数据源详细文档
- [executor.md](executor.md) - 执行器与数据驱动设计
- [listener.md](listener.md) - Listener 基类和生命周期

