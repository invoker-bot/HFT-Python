# 指标模块文档

## 概述

指标模块提供两种指标计算模式，分别适用于不同场景：

| 模式 | 类 | 驱动方式 | 适用场景 |
|------|-----|---------|---------|
| 事件驱动 | `BaseIndicator` | 监听 DataSource 事件 | 实时响应、高频计算 |
| 轮询驱动 | `LazyIndicator` | 挂载到 TradingPairDataSource | 按需计算、资源优化 |

```
hft/indicator/
├── base.py      # 事件驱动指标 (BaseIndicator, ChainedIndicator)
├── lazy.py      # 轮询驱动指标 (LazyIndicator, VWAP, Spread, MidPrice)
└── intensity.py # 交易强度指标 (TradeIntensityIndicator)
```

## LazyIndicator（推荐）

挂载在 `TradingPairDataSource` 上的派生指标，享受统一的生命周期管理。

### 特性

- **lazy_start**: 初始为 STOPPED，首次 `query_indicator()` 时启动
- **自动依赖管理**: 自动 query 依赖的数据源，保持其活跃
- **auto-stop**: 5分钟无访问自动 stop()（保留计算结果）
- **多数据源支持**: 可依赖多个 DataType

### 使用示例

```python
from hft.indicator import VWAPIndicator, SpreadIndicator, TradeIntensityIndicator

# 获取 TradingPairDataSource
pair = datasource_group.get_trading_pair("okx", "BTC/USDT:USDT")

# 查询指标（首次调用会创建并启动）
vwap = pair.query_indicator(VWAPIndicator, window=200)
spread = pair.query_indicator(SpreadIndicator)
intensity = pair.query_indicator(TradeIntensityIndicator, total_range_seconds=600.0)

# 获取指标值
if vwap:
    value = vwap.get_value()
    print(f"VWAP: {value}")

if spread:
    value = spread.get_value()
    print(f"Spread: {value:.6f}")

if intensity and intensity.is_ready:
    result = intensity.get_value()
    print(f"buy_k: {result.buy_k}, sell_k: {result.sell_k}")

    # 计算最优价差
    inv_adj, arr_adj = intensity.get_optimal_spread("buy", gamma=0.1)
```

### 内置指标

| 指标 | 依赖 | 说明 |
|------|------|------|
| `VWAPIndicator` | TRADES | 成交量加权平均价 |
| `SpreadIndicator` | ORDER_BOOK | 买卖价差 (Ask-Bid)/Bid |
| `MidPriceIndicator` | ORDER_BOOK | 中间价 (Ask+Bid)/2 |
| `TradeIntensityIndicator` | TRADES, ORDER_BOOK | AS 做市策略参数 |

### 自定义指标

```python
from hft.indicator import LazyIndicator
from hft.datasource.group import DataType

class MyIndicator(LazyIndicator[float]):
    """自定义指标示例"""
    depends_on = [DataType.TRADES, DataType.ORDER_BOOK]

    def __init__(self, my_param: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self._my_param = my_param

    async def _update_value(self) -> None:
        # 获取依赖的数据源
        trades_ds = self.get_datasource(DataType.TRADES)
        ob_ds = self.get_datasource(DataType.ORDER_BOOK)

        if trades_ds is None or ob_ds is None:
            return

        # 获取数据
        trades = trades_ds.get_all()
        ob = ob_ds.get_latest()

        if not trades or ob is None:
            return

        # 计算逻辑
        self._value = calculate_something(trades, ob, self._my_param)

# 使用
my_indicator = pair.query_indicator(MyIndicator, my_param=2.0)
```

### 生命周期

```
1. 初始化: 创建后保持 STOPPED 状态
2. 首次 query_indicator(): 调用 start()，开始定时计算
3. 每次 get_value(): 刷新访问时间，返回缓存值
4. 超时无访问: 自动 stop()（保留计算结果）
5. 再次 query_indicator(): 重新 start()
```

## BaseIndicator（事件驱动）

监听 DataSource 的 update 事件，数据更新时自动计算。适用于需要实时响应的场景。

### 特性

- **事件驱动**: 监听 DataSource 更新事件
- **链式调用**: 支持指标依赖指标
- **历史回溯**: 保留计算历史

### 使用示例

```python
from hft.indicator import BaseIndicator, IndicatorResult

class RSIIndicator(BaseIndicator[OHLCVData, IndicatorResult]):
    """RSI 指标示例"""

    def __init__(self, datasource, period: int = 14):
        super().__init__(name="RSI", datasource=datasource, period=period)

    def calculate(self) -> Optional[IndicatorResult]:
        # 获取历史数据
        data = self.datasource.get_last_n(self.period + 1)
        if len(data) < self.period + 1:
            return None

        # 计算 RSI
        gains, losses = [], []
        for i in range(1, len(data)):
            change = data[i].close - data[i-1].close
            gains.append(max(change, 0))
            losses.append(abs(min(change, 0)))

        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        return IndicatorResult(
            ready=True,
            value=rsi,
            bias=(rsi - 50) / 50,  # 映射到 -1 ~ 1
            confidence=1.0,
        )

# 使用
ohlcv_ds = trading_pair.query(DataType.OHLCV)
rsi = RSIIndicator(datasource=ohlcv_ds, period=14)

# 事件驱动，自动计算
rsi.on("update", lambda result: print(f"RSI: {result.value}"))
```

### ChainedIndicator

支持指标依赖另一个指标：

```python
from hft.indicator import ChainedIndicator, IndicatorResult

class RSISmoothedIndicator(ChainedIndicator):
    """RSI 平滑指标 - 依赖 RSI"""

    def __init__(self, rsi_indicator, period: int = 3):
        super().__init__(name="RSI_Smoothed", source_indicator=rsi_indicator, period=period)

    def calculate(self) -> Optional[IndicatorResult]:
        history = self.source.history[-self.period:]
        if len(history) < self.period:
            return None

        avg_rsi = sum(r.value for r in history) / len(history)
        return IndicatorResult(ready=True, value=avg_rsi)
```

## TradeIntensityIndicator

用于 AS (Avellaneda-Stoikov) 做市策略的订单到达率估计。

### 原理

基于历史成交数据估计订单到达率参数 kappa (k)：
- 收集一段时间内的成交数据
- 统计不同价格偏离下的成交量分布
- 拟合指数衰减模型：λ(δ) = A × exp(-k × δ)

### 使用示例

```python
from hft.indicator import TradeIntensityIndicator

# 创建指标
intensity = trading_pair.query_indicator(
    TradeIntensityIndicator,
    total_range_seconds=600.0,  # 10分钟数据窗口
    min_trades=50,              # 最少成交笔数
    min_correlation=0.5,        # 最小拟合相关系数
)

if intensity and intensity.is_ready:
    result = intensity.get_value()

    # 获取强度参数
    print(f"买方 k: {result.buy_k}")
    print(f"卖方 k: {result.sell_k}")
    print(f"订单簿不平衡: {result.imbalance}")

    # 计算最优价差
    inv_adj, arr_adj = intensity.get_optimal_spread(
        side="buy",
        gamma=0.1,        # 风险厌恶系数
        inventory=0.5,    # 标准化库存
    )
    optimal_spread = inv_adj + arr_adj
```

### IntensityResult 字段

```python
@dataclass
class IntensityResult:
    # 基础统计
    average_price: float    # 加权平均价
    average_std: float      # 相对标准差（比例）
    trade_count: int        # 成交笔数
    total_amount: float     # 总成交量

    # 买方强度参数
    buy_k: float            # 订单到达率衰减参数
    buy_A: float            # 基础强度（截距）
    buy_correlation: float  # 拟合相关系数

    # 卖方强度参数
    sell_k: float
    sell_A: float
    sell_correlation: float

    # 订单簿不平衡
    imbalance: float        # >0 买盘强，<0 卖盘强

    @property
    def is_valid(self) -> bool:
        """检查结果是否有效"""
        return (
            self.trade_count >= 10 and
            self.buy_correlation >= 0.5 and
            self.sell_correlation >= 0.5
        )
```

## 两种模式对比

| 特性 | LazyIndicator | BaseIndicator |
|------|---------------|---------------|
| 驱动方式 | 轮询 (on_tick) | 事件 (on update) |
| 挂载位置 | TradingPairDataSource | 直接绑定 DataSource |
| 生命周期 | lazy_start, auto-stop | 跟随 DataSource |
| 数据依赖 | 多个 DataType | 单个 DataSource |
| 适用场景 | 按需计算、资源优化 | 实时响应、高频计算 |
| 链式支持 | 否 | 是 (ChainedIndicator) |

## 相关文档

- [datasource.md](datasource.md) - 数据源模块文档
- [listener.md](listener.md) - Listener 基类和生命周期
