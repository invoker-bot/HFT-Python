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

## Ready 语义（Feature 0005）

### 概述

Indicator 的 `is_ready()` 方法用于判断指标是否可用。当 Executor 通过 `requires` 声明依赖时，系统会检查所有依赖的 Indicator 是否 ready，只有全部 ready 才会执行。

### Ready 判断逻辑

```python
def is_ready(self) -> bool:
    """
    判断指标是否 ready

    逻辑：
    1. 首先检查 ready_condition（如果配置了）
    2. 然后检查 ready_internal()（子类实现）
    3. 两者都满足才返回 True
    """
    # 1. 检查 ready_condition 表达式
    if self._ready_condition:
        context = self._get_ready_condition_context()
        if not self._safe_eval_bool(self._ready_condition, context):
            return False

    # 2. 检查 ready_internal()
    return self.ready_internal()
```

### ready_condition 配置

`ready_condition` 是一个表达式字符串，支持以下变量：

| 变量 | 说明 |
|------|------|
| `timeout` | 距离最后一次数据更新的秒数 |
| `cv` | 数据变异系数（Coefficient of Variation） |
| `range` | 实际覆盖时间 / 期望窗口时间 |

**注意**：`ready_condition` 禁用函数调用（如 `len/sum/min/max`），仅支持比较/逻辑/基本算术等操作符。

**配置示例**：

```yaml
indicators:
  trades:
    class: TradesDataSource
    ready_condition: "timeout < 60 and cv < 0.8"  # 单独字段
    params:
      window: 5m  # 构造参数（支持 duration 字符串：60s, 1m, 5m, 1h, 1d）
```

### ready_internal() 实现

不同类型的 Indicator 有不同的 `ready_internal()` 实现：

#### 数据源类（DataSource）

数据源类的 `ready_internal()` 通常检查是否有数据：

```python
class TickerDataSource(BaseDataSource[Ticker]):
    def ready_internal(self) -> bool:
        """至少有一个 ticker 数据"""
        return len(self._data) > 0
```

#### 计算类（Computed Indicator）

计算类 Indicator 采用**混合模式**：

- **被 requires 依赖时**：在 `on_tick()` 中定期计算并缓存到 `_data`
- **未被依赖时**：`calculate_vars()` 按需计算（lazy）

```python
class RSIIndicator(BaseIndicator[float]):
    def ready_internal(self) -> bool:
        """
        requires 模式下：检查 _data 是否有数据
        lazy 模式下：检查依赖的 OHLCV 是否 ready
        """
        if self._is_required:
            return len(self._data) > 0
        # lazy 模式：委托给依赖
        ohlcv = self._get_ohlcv_indicator()
        return ohlcv is not None and ohlcv.is_ready()

    async def on_tick(self) -> bool:
        """只在被 requires 时定期计算"""
        if not self._is_required:
            return False
        # 计算 RSI 并缓存到 _data
        ...
```

### Requires Ready Gate

Executor 在执行前会检查所有 `requires` 中的 Indicator 是否 ready：

```python
class BaseExecutor:
    def check_requires_ready(self, exchange_class: str, symbol: str) -> bool:
        """
        检查所有 requires 中的 Indicator 是否 ready

        Returns:
            True 如果所有 Indicator 都 ready，否则 False
        """
        for indicator_id in self.config.requires:
            indicator = self._get_indicator(exchange_class, symbol, indicator_id)
            if indicator is None or not indicator.is_ready():
                return False
        return True
```

如果任一 Indicator 未 ready，执行会被跳过（返回 None），不会触发实际交易。

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
2. 检查 `is_ready()` 状态（**requires ready gate**）
3. 调用 `calculate_vars(direction)` 收集变量
4. 注入到 condition 表达式上下文

## Scope 集成（Feature 0012）

Indicator 可以注入到不同层级的 Scope 中，提供层级化的变量访问。

### scope_level 属性

每个 Indicator 可以指定其注入的 Scope 层级：

```python
class TickerDataSource(BaseDataSource[dict]):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 注入到 trading_pair_class 层级（所有 exchange 共享）
        self.scope_level = "trading_pair_class"
```

### Indicator 层级体系

| Indicator 类型 | Scope 层级 | 说明 |
|---------------|-----------|------|
| GlobalIndicator | global | 全局唯一指标 |
| EquationDataSource | exchange | 账户权益（按 exchange 实例） |
| TickerDataSource | trading_pair_class | 价格数据（按交易对类型） |
| RSIIndicator | trading_pair | 技术指标（按交易对实例） |

### 在 Strategy 中使用

Strategy 通过 `requires` 声明依赖，系统会在 Scope 树构建时自动注入 Indicator 变量：

```yaml
# conf/strategy/my_strategy.yaml
requires:
  - ticker
  - equation
  - rsi

links:
  - [global, exchange, trading_pair]

targets:
  - exchange_id: '*'
    symbol: BTC/USDT
    position_usd: '0.6 * equation_usd'  # 使用 Indicator 变量
```

## 相关文档

- [datasource.md](datasource.md) - 数据源详细文档
- [executor.md](executor.md) - 执行器与数据驱动设计
- [listener.md](listener.md) - Listener 基类和生命周期
- [scope.md](scope.md) - Scope 系统架构
- [vars.md](vars.md) - 变量系统设计

