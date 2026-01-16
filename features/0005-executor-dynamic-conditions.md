# Feature: Executor 动态条件与变量注入机制

> 依赖 Feature 0006（Indicator 与 DataSource 统一架构）

## 背景

当前 SmartExecutor 的 routes 已支持 condition 表达式，但存在以下问题：

1. **变量来源不透明**：condition 中的变量（如 speed、edge）是硬编码传入的，无法灵活扩展
2. **indicator 未充分利用**：现有 indicator 计算的结果没有统一的方式注入到 executor 条件判断中
3. **参数静态化**：LimitExecutor 的 spread、timeout 等参数是固定值，无法根据市场状态动态调整
4. **命名不直观**：`edge` 名称含义模糊；建议长期迁移到 `medal_edge`（由 Indicator 统一产出），同时保留 `edge` 作为 SmartExecutor 的兼容变量

## 目标

设计统一的 **条件表达式 + 动态变量注入** 机制：

1. **Indicator 提供变量**：通过 `calculate_vars(direction)` 方法返回变量字典
2. **Executor 声明依赖**：通过 `requires` 字段列出需要的 indicator ID
3. **条件控制执行**：通过 `condition` 表达式决定是否执行（已实现，见 `BaseExecutor._process_single_target`）
4. **参数动态计算**：spread、per_order_usd、timeout 等支持表达式或字面量

## 核心设计

### 0. 配置字段约定（与当前仓库一致）

本仓库配置字段约定：
- 执行器/策略等配置（如 `conf/executor/**`, `conf/strategy/**`）使用 `class_name` 来选择具体实现
- App 的 indicators 配置（`conf/app/**` 的 `indicators:`）使用 `class` 来选择 indicator 类（由 `IndicatorFactory` 映射）

```yaml
class_name: market  # 而不是 class: MarketExecutor
```

### 1. Indicator 的 `calculate_vars` 抽象方法

基于 Feature 0006 的 BaseIndicator，每个 Indicator 必须实现 `calculate_vars` 方法：

```python
class BaseIndicator(Listener, Generic[T]):

    @abstractmethod
    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """
        计算并返回该指标提供的变量

        Args:
            direction: 交易方向，1 表示多（买入），-1 表示空（卖出）

        Returns:
            变量字典，例如 {"medal_edge": 0.0005, "medal_buy_edge": 0.0003, ...}
        """
        ...
```

**direction 参数说明**：
- `1`：多头方向（买入开多 / 卖出平空）
- `-1`：空头方向（卖出开空 / 买入平多）

### 2. 内置变量（无需 requires 声明）

以下变量始终可用，由系统自动注入：

| 变量名 | 类型 | 说明 |
|--------|------|------|
| `direction` | `int` | 交易方向：1（多）或 -1（空） |
| `buy` | `bool` | `direction == 1` |
| `sell` | `bool` | `direction == -1` |
| `speed` | `float` | 目标仓位的紧急程度（来自 strategy） |
| `notional` | `float` | 目标仓位差额的 USD 价值（`abs(delta_usd)`） |
| `mid_price` | `float` | **非全局内置**：当前仓库仅在部分求值点由调用方注入（例如 `BaseExecutor._process_single_target()` 的 condition gate、`LimitExecutor` 的订单参数表达式、`SmartExecutor` 的 child condition）；SmartExecutor 的 routes 上下文不保证存在该变量。 |

**变量优先级（重要）**：
- `BaseExecutor.collect_context_vars()` 会先放入内置变量，再 `context.update(indicator_vars)` 注入 indicator 变量
- 当前仓库部分调用方会在 collect 之后再次注入 `mid_price`（会覆盖同名 indicator 变量）

**命名约定（避免歧义/覆盖）**：
- `direction/buy/sell/speed/notional/target_notional/trades_notional` 视为保留名，indicator 的 `calculate_vars()` 不应返回这些 key
- `mid_price` 在当前仓库会被部分执行链路显式注入并覆盖同名 key；如需从 indicator 提供“订单簿 mid”，建议使用独立变量名（例如 `book_mid_price`）或确保调用方不再覆盖该 key
- 当前仓库存在已知冲突风险（例如 `VolumeIndicator` 返回 `notional`），详见 `issue/0005-executor-context-var-collisions.md`

### 3. App Config 中的 indicators 配置

基于 Feature 0006 的统一架构，在 app config 中定义可用的 indicator 及其参数。

**注意**：DataSource 是特殊的 Indicator（从 exchange 获取数据），普通 Indicator 从其他 Indicator 计算数据。

```yaml
# conf/app/demo/okx_smart.yaml
indicators:
  # 数据源类 indicator（继承 BaseDataSource）
  trades:
    class: TradesDataSource
    params:
      window: 300.0  # 秒（当前仓库未实现 "5m/1d" 这类 duration 字符串解析）
    ready_condition: "timeout < 60 and cv < 0.8 and range > 0.6"

  order_book:
    class: OrderBookDataSource
    params:
      depth: 20
    ready_condition: "timeout < 5"

  ticker:
    class: TickerDataSource
    params:
      # ...
    ready_condition: "timeout < 10"

  ohlcv-1m:
    class: OHLCVDataSource
    params:
      timeframe: "1m"
      window: 86400.0  # 秒（当前仓库未实现 "5m/1d" 这类 duration 字符串解析）
    ready_condition: "timeout < 60 and cv < 0.8 and range > 0.6"

  # 计算类 indicator（继承 BaseIndicator）
  mid_price:
    class: MidPriceIndicator
    params:
      order_book: order_book  # 字符串引用，通过 query_indicator 获取

  medal_edge:
    class: MedalEdgeIndicator
    params:
      trades: trades  # 依赖 trades 数据源
      window: 60.0  # 秒

  rsi:
    class: RSIIndicator
    params:
      ohlcv: ohlcv-1m  # 依赖 ohlcv 数据源
      period: 14

  volume:
    class: VolumeIndicator
    params:
      trades: trades
      window: 300.0  # 秒
```

**ready_condition 说明**：
- `ready_condition` 不属于 `params`：indicator 实例构造完成后，再由配置/加载层调用 `set_ready_condition(...)` 注入
- `ready_condition: null` 的含义：不增加额外限制（表达式视为 True），但 **indicator 仍需满足自身内置 ready 规则**（默认至少有 1 个可用数据点；子类也可叠加更严格的内置判断）
- `timeout`：当前时间与最新数据的时间差（秒）
- `cv`：采样间隔的变异系数（越小表示采样越均匀）
- `range`：实际覆盖时间 / 期望窗口时间（越大表示覆盖越完整）
- 这些指标由 `HealthyDataArray` 自动计算

**ready 的组合语义（与 requires gate 强相关）**：
- 建议口径：`is_ready = ready_internal() and (ready_condition is None or eval(ready_condition))`
- `ready_internal()`：由 indicator 自身实现的内置就绪判断。`BaseIndicator` 的默认行为是“`_data` 至少有 1 个可用数据点则 ready”，其他 indicator 可覆盖为更严格的逻辑（例如 RSI 需要足够长度、MidPrice 需要 orderbook 可用等）
- 由于 `ready_condition` 的变量（timeout/cv/range）来自 indicator 自己的 `_data` 健康统计：计算型 indicator 也必须维护自身 `_data`（通常在依赖数据源 update 时写入一条新点或 upsert/replace），否则无法得到正确的健康口径与 ready 行为

**实现现状提醒（需要与 Feature 0006 口径对齐）**：
- 当前仓库的 `RSIIndicator` 已实现 requires 模式下的 `_data` 维护与 `ready_internal()`
- 当前仓库的 `MidPriceIndicator/MedalEdgeIndicator/VolumeIndicator` 尚未维护自身 `_data` 且未覆盖 `ready_internal()`，会导致它们在 `query_indicator()` 语义下长期处于 not ready（从而无法用于 requires gate / 变量注入）；这部分需要补齐后才能按本文档示例使用（见本文 TODO 与 `issue/0006-feature-0005-implementation-gaps.md`）

### 4. Executor 的 `requires` 和 `condition`

#### 4.1 MarketExecutor 示例

```yaml
# conf/executor/demo/market_rsi.yaml
class_name: market
requires:
  - rsi
condition: "(buy and rsi < 30) or (sell and rsi > 70)"
per_order_usd: 100  # 支持表达式：'100 + abs(rsi - 50) * 2'
```

**说明**：
- `requires: [rsi]`：声明依赖 RSI 指标，系统会从 app config 的 indicators 中查找并注入
- **重要语义**：当 `requires` 中任意 indicator 未 ready 时，本 executor **不会进入后续动作**（路由/参数求值/下单），应当直接跳过等待下一次 tick
- `condition`：表达式求值为 `True` 时才允许执行；为 `False` 时静默跳过，等待下次 tick
- `condition` 为 `null` 或不配置时，默认为 `True`

#### 4.2 LimitExecutor 示例

```yaml
# conf/executor/demo/limit_dynamic.yaml
class_name: limit
requires: []
condition: null

orders:
  - spread: "mid_price * 0.001"       # 表达式：绝对价差（按比例换算，例如 0.1% * mid_price）
    refresh_tolerance: 0.5            # 字面量：相对原 spread 的比例阈值（0.5 = 50%）
    timeout: "30 if speed > 0.5 else 60"  # 表达式：根据速度调整
    per_order_usd: 100                # 字面量
    reverse: false                    # 字面量

  - spread: 20.0                      # 字面量：绝对价差 20（如 BTC/USDT 则为 20 USDT）
    refresh_tolerance: "0.3"          # 表达式：比例阈值（这里等价于 0.3）
    timeout: 120
    per_order_usd: "50 + notional * 0.1"  # 表达式
    reverse: "speed < 0.2"            # 表达式（示例）
```

> 说明：当前仓库的 `LimitExecutor` 会额外注入 `mid_price=current_price` 到表达式上下文中，用于 `spread` 等参数计算。

#### 4.3 SmartExecutor 示例

```yaml
# conf/executor/demo/smart_dynamic.yaml
class_name: smart
requires: []
condition: null  # 外层 condition，默认为 null（True）

children:
  market: executor/demo/market_fast
  limit_aggressive: executor/demo/limit_aggressive
  limit_passive: executor/demo/limit_passive

routes:
  # 说明：
  # - SmartExecutor 路由上下文里提供 trades_notional/target_notional
  # - 当前仓库还会将 notional 覆盖为 trades_notional（兼容历史表达式），因此建议显式使用 target_notional / trades_notional
  - condition: "speed > 0.8 and trades_notional > 1000000"
    executor: market
    priority: 1

  - condition: "speed > 0.5 and target_notional > 1000"
    executor: limit_aggressive
    priority: 2

  - condition: "edge > 0.001"
    executor: market
    priority: 3

  - condition: null  # fallback（无条件）
    executor: limit_passive
    priority: 999
```

**执行流程**：
1. （规划）检查外层 `condition`（若为 `False` 则整体跳过）
2. 按 routes 顺序遍历，找到第一个 `condition` 为 `True` 的路由
3. （规划）递归检查子 executor 的 `condition`（若子 executor 也有 condition）
4. 最终执行选中的 executor（或 executor=null 表示不执行）

**实现注意（当前仓库现状）**：
- SmartExecutor 路由上下文里的 `trades/edge/trades_notional` 当前来自 legacy 的 `datasource_group`（后续计划在 Feature 0007 移除/迁移）；与 Feature 0006 的 `IndicatorGroup` 仍存在双源并存期，需要在文档/实现中保持口径一致

### 5. 参数解析规则

对于 LimitExecutor 和 MarketExecutor 的动态参数：

| 字段 | 类型 | 字符串解释 | 数值解释 |
|------|------|------------|----------|
| `spread` | `str \| float` | 表达式求值（需返回**绝对价差**） | 直接使用（**绝对价差**，单位为 price 的计价货币，例如 USDT） |
| `refresh_tolerance` | `str \| float` | 表达式求值 | 直接使用（**相对原 spread 的比例阈值**，无单位） |
| `timeout` | `str \| float` | 表达式求值 | 直接使用（秒） |
| `per_order_usd` | `str \| float` | 表达式求值 | 直接使用（USD） |
| `reverse` | `str \| bool` | 表达式求值 | 直接使用 |

**示例**：
```yaml
spread: 10.0                 # 解释为绝对价差 10（如 BTC/USDT 则为 10 USDT）
spread: "0.001 * mid_price"  # 绝对价差表达式：按比例换算（例如 0.1% * mid_price）

refresh_tolerance: 0.5              # 无单位：允许价格在“原 spread”的 50% 范围内偏移仍视为可复用
refresh_tolerance: "0.3 if speed > 0.8 else 0.6"  # 表达式返回比例阈值
```

**refresh_tolerance 的数学定义（复用判断）**：
- `old_spread = abs(old_price - mid_price)`
- `price_deviation = abs(new_price - old_price)`
- 可复用条件：`price_deviation / old_spread <= refresh_tolerance`

注意：由于 `spread` 统一为“绝对价差”，如果你想表达“距离 mid 的比例”，请使用表达式显式乘以 `mid_price`（例如 `"0.005 * mid_price"` 表示 0.5%）。

兼容性提示：当前仓库历史配置里存在 `spread: 0.001` 这类“比例”写法（0.1%）。本 Feature 落地后 `spread` 已统一为“绝对价差”，因此需要按以下方式迁移：
- 旧：`spread: 0.001`（比例）
- 新：`spread: "mid_price * 0.001"`（表达式，返回绝对价差）

### 6. 新增 Indicator 类

基于 Feature 0006 的统一架构，DataSource 是特殊的 Indicator。

#### 6.1 数据源类（继承 BaseDataSource）

这些 indicator 从 exchange 获取数据，数据存储在 `HealthyDataArray` 中：

```python
class TradesDataSource(BaseDataSource[Trade]):
    """最近成交记录"""

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        return {
            "trades": list(self._data),  # list[Trade]
            "trade_count": len(self._data),
            "last_trade_price": self._data.latest.price if self._data.latest else None,
        }


class OrderBookDataSource(BaseDataSource[OrderBook]):
    """订单簿"""

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


class TickerDataSource(BaseDataSource[Ticker]):
    """Ticker"""

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        ticker = self._data.latest
        if ticker is None:
            return {"ticker": None, "last_price": None}
        return {
            "ticker": ticker,
            "last": ticker.last,
            "bid": ticker.bid,
            "ask": ticker.ask,
            "mid": (ticker.bid + ticker.ask) / 2 if ticker.bid and ticker.ask else ticker.last,
            "spread": (ticker.ask - ticker.bid) / ticker.bid if ticker.bid else 0.0,
        }


class OHLCVDataSource(BaseDataSource[Candle]):
    """K线数据"""

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        return {
            "ohlcv": list(self._data),  # list[Candle]
            "candle_count": len(self._data),
        }
```

#### 6.2 计算类（继承 BaseIndicator）

这些 indicator 从其他 indicator 计算数据：

```python
class MidPriceIndicator(BaseIndicator[float]):
    """中间价格"""

    def __init__(self, order_book: str, **kwargs):
        super().__init__(interval=None, **kwargs)  # 不需要 tick
        self._order_book_id = order_book

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        # 通过 query_indicator 获取依赖
        ob_indicator = self._app.query_indicator(
            self._order_book_id, self._exchange_class, self._symbol
        )
        if ob_indicator is None or not ob_indicator.is_ready():
            return {"mid_price": None}

        ob = ob_indicator._data.latest
        mid = (ob.bids[0].price + ob.asks[0].price) / 2
        return {"mid_price": mid}


class MedalEdgeIndicator(BaseIndicator[float]):
    """
    Medal Edge 指标

    计算 taker 相对于 maker 的百分比优势
    原名 edge，重命名为 medal_edge 以更直观
    """

    def __init__(self, trades: str, window: float, **kwargs):
        super().__init__(interval=None, **kwargs)
        self._trades_id = trades
        self._window = window  # 秒（当前仓库未实现 duration 字符串解析）

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        trades_indicator = self._app.query_indicator(
            self._trades_id, self._exchange_class, self._symbol
        )
        if trades_indicator is None or not trades_indicator.is_ready():
            return {"medal_edge": 0.0, "medal_buy_edge": 0.0, "medal_sell_edge": 0.0}

        buy_edge = self._calculate_buy_edge(trades_indicator._data)
        sell_edge = self._calculate_sell_edge(trades_indicator._data)

        # 根据 direction 返回对应方向的 edge
        edge = buy_edge if direction == 1 else sell_edge

        return {
            "medal_edge": edge,
            "medal_buy_edge": buy_edge,
            "medal_sell_edge": sell_edge,
        }


class VolumeIndicator(BaseIndicator[float]):
    """成交量指标"""

    def __init__(self, trades: str, window: float, **kwargs):
        super().__init__(interval=None, **kwargs)
        self._trades_id = trades
        self._window = window

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        trades_indicator = self._app.query_indicator(
            self._trades_id, self._exchange_class, self._symbol
        )
        if trades_indicator is None or not trades_indicator.is_ready():
            return {"volume": 0.0, "buy_volume": 0.0, "sell_volume": 0.0}

        return {
            "volume": self._calculate_volume(trades_indicator._data),
            "buy_volume": self._calculate_buy_volume(trades_indicator._data),
            "sell_volume": self._calculate_sell_volume(trades_indicator._data),
        }


class RSIIndicator(BaseIndicator[float]):
    """RSI 指标"""

    def __init__(self, ohlcv: str, period: int = 14, **kwargs):
        super().__init__(interval=None, **kwargs)
        self._ohlcv_id = ohlcv
        self._period = period

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        ohlcv_indicator = self._app.query_indicator(
            self._ohlcv_id, self._exchange_class, self._symbol
        )
        if ohlcv_indicator is None or not ohlcv_indicator.is_ready():
            return {"rsi": 50.0}  # 默认中性值

        return {"rsi": self._calculate_rsi(ohlcv_indicator._data)}
```

> 说明（实现约束）：计算类 indicator 如果要参与 requires ready gate，必须：
> 1) 覆盖 `ready_internal()`（至少依赖数据源 ready 时可 ready）；2) 在 requires 模式下维护自身 `_data`（用于 timeout/cv/range 与健康口径）。

### 7. Executor 基类扩展

本仓库当前实现方式：在 `BaseExecutor` 内提供 `collect_context_vars/evaluate_condition/evaluate_param`，并使用 simpleeval 的函数白名单（`len/abs/min/max/sum/round`）。

```python
class BaseExecutor(GroupListener):

    @property
    def requires(self) -> list[str]:
        """依赖的 indicator ID 列表"""
        return self.config.requires or []

    @property
    def condition(self) -> Optional[str]:
        """执行条件表达式，None 表示始终执行"""
        return self.config.condition

    def collect_context_vars(
        self,
        exchange_class: str,
        symbol: str,
        direction: int,
        speed: float,
        notional: float,
    ) -> dict[str, Any]:
        """
        收集条件求值所需的所有变量

        1. 内置变量（direction, buy, sell, speed, notional）
        2. requires 中声明的 indicator 提供的变量
        """
        # 内置变量
        context = {
            "direction": direction,
            "buy": direction == 1,
            "sell": direction == -1,
            "speed": speed,
            "notional": notional,
        }

        # 从 indicator 收集变量
        for indicator_id in self.requires:
            indicator = self._get_indicator(indicator_id, exchange_class, symbol)
            if indicator and indicator.is_ready():
                try:
                    vars_dict = indicator.calculate_vars(direction)
                    context.update(vars_dict)
                except Exception:
                    # fail-safe：indicator 变量注入失败不影响执行链路
                    pass

        return context

    def evaluate_condition(self, context: dict[str, Any]) -> bool:
        """
        求值 condition 表达式

        Returns:
            True: 执行
            False: 跳过（静默等待下次 tick）
        """
        if self.condition is None:
            return True

        return self._safe_eval_bool(self.condition, context)

    def evaluate_param(
        self,
        param: Any,
        context: dict[str, Any],
    ) -> Any:
        """
        求值参数（支持表达式或字面量）

        Args:
            param: 参数值（str 为表达式，float/bool 为字面量）
            context: 变量上下文
        """
        if isinstance(param, str):
            return self._safe_eval(param, context)
        return param
```

### 8. 执行流程

```
┌─────────────────────────────────────────────────────────────┐
│                      Executor.execute()                      │
├─────────────────────────────────────────────────────────────┤
│  1. 收集上下文变量                                           │
│     context = collect_context_vars(direction, speed, notional)│
│                                                              │
│  2. （规划）求值外层 condition                               │
│     if not evaluate_condition(context): return               │
│                                                              │
│  3. [SmartExecutor] 遍历 routes                              │
│     for route in routes:                                     │
│         if evaluate_condition(route.condition, context):     │
│             selected_executor = route.executor               │
│             break                                            │
│                                                              │
│  4. （规划）递归检查子 executor condition                    │
│     if not selected_executor.evaluate_condition(context):    │
│         return                                               │
│                                                              │
│  5. 求值动态参数                                             │
│     spread = evaluate_param(config.spread, context)            │
│     per_order_usd = evaluate_param(config.per_order_usd, context)│
│     ...                                                      │
│                                                              │
│  6. 执行下单逻辑                                             │
│     create_orders(...)                                       │
└─────────────────────────────────────────────────────────────┘
```

### 9. Indicator 查找机制

基于 Feature 0006 的 `IndicatorGroup` 和 `query_indicator` 机制。

#### 9.1 IndicatorGroup 层级结构

```
IndicatorGroup
├── GlobalIndicators
│   └── GlobalFundingRateDataSource, ...
└── LocalIndicators
    └── (exchange_class, symbol) -> TradingPairIndicators
        └── TradesDataSource, OrderBookDataSource, MedalEdgeIndicator, ...
```

| 级别 | 挂载位置 | 查找方式 | 示例 |
|------|----------|----------|------|
| 全局级 | GlobalIndicators | `exchange_class=None, symbol=None` | 全局资金费率 |
| 交易对级 | LocalIndicators | 自动匹配当前交易对 | trades、order_book、medal_edge |

#### 9.2 query_indicator 机制

```python
def _get_indicator(
    self,
    indicator_id: str,
    exchange_class: str,
    symbol: str,
) -> Optional[BaseIndicator]:
    """
    通过 root.indicator_group.query_indicator 获取 indicator

    特性：
    1. lazy 创建：首次访问时创建
    2. 自动启动：STOPPED 状态自动 start
    3. touch 更新：防止过期停止
    4. ready 检查：根据 ready_condition 判断
    """
    indicator = self.root.indicator_group.query_indicator(
        indicator_id,
        exchange_class,
        symbol,
    )

    # query_indicator 返回 None 表示 indicator 未 ready（实例仍可通过 get_indicator 获取）
    return indicator
```

## 验收标准

1. **BaseIndicator 新增 `calculate_vars` 抽象方法**：所有 indicator 子类必须实现
2. **Executor 支持 `requires`**：配置解析正确，能收集 ready indicator 的变量并注入上下文
3. **requires ready gate（关键语义）**：当 `requires` 中任意 indicator 未 ready 时，跳过路由/参数求值/下单（BaseExecutor 已实现；计算类 indicator 需补齐 `ready_internal()`/`_data` 维护后才能可靠依赖）
4. **动态参数解析**：spread、timeout、per_order_usd、refresh_tolerance、reverse 等支持表达式和字面量两种形式
5. **表达式安全性**：仅允许白名单函数（`len/abs/min/max/sum/round`），非法表达式 fail-safe
6. **内置变量可用**：direction、buy、sell、speed、notional（`mid_price` 在对应求值点可用：LimitExecutor 参数/condition gate/child condition）
7. **Executor-level condition gate**：`condition` 为 False 时不下单（已实现，见 `BaseExecutor._process_single_target`）
8. **SmartExecutor 路由 condition**：routes 条件求值正确，且路由引用校验清晰可读（Feature 0002/0005 交叉）

## TODO

> 注意：部分任务依赖 Feature 0006 的基础设施（IndicatorGroup / BaseIndicator / BaseDataSource）

- [x] BaseExecutor：实现 requires + collect_context_vars（仅注入 ready indicator vars）（审核完成）
- [x] BaseExecutor：requires 作为 ready gate：任一 requires indicator 未 ready 时，跳过路由/参数求值/下单（审核完成）
- [x] BaseExecutor：实现 evaluate_condition/evaluate_param + safe eval 白名单，并 fail-safe（审核完成）
- [x] LimitExecutor：orders 动态参数（spread/refresh_tolerance/timeout/per_order_usd/reverse）表达式求值（审核完成）
- [x] MarketExecutor：实现动态 per_order_usd（支持表达式），并由 BaseExecutor 阈值判断/拆单逻辑统一使用（审核完成）
- [x] 单元测试：condition 求值、动态参数解析、indicator 变量注入、安全性（审核完成）
- [ ] 单元测试：覆盖 MidPrice/MedalEdge/Volume 的 ready 语义（requires 模式 `_data` 维护 + `ready_internal()`）（待实现）
- [x] BaseExecutor：在统一执行链路中接入 `condition` gate（condition=False 时不下单）（审核完成）
- [x] MarketExecutor / LimitExecutor：明确 condition 的执行语义（在 delta 阈值检查之前执行）（审核完成）
- [x] SmartExecutor：接入外层 `condition` 与子 executor 的 `condition` 递归检查（审核完成）
- [x] 实现数据源类 Indicator：TradesDataSource / OrderBookDataSource / OHLCVDataSource（依赖 Feature 0006；实现：`hft/indicator/datasource/`）（审核完成）
- [ ] 实现计算类 Indicator：MidPriceIndicator（可用于 requires/变量注入）（审核不通过：当前未覆盖 `ready_internal()` 且未维护 `_data`，导致长期 not ready）
- [ ] 实现计算类 Indicator：MedalEdgeIndicator（可用于 requires/变量注入）（审核不通过：当前未覆盖 `ready_internal()` 且未维护 `_data`，导致长期 not ready）
- [ ] 实现计算类 Indicator：VolumeIndicator（可用于 requires/变量注入）（审核不通过：当前未覆盖 `ready_internal()` 且未维护 `_data`，导致长期 not ready，且 `notional` key 与内置变量冲突风险较高）
- [x] 实现计算类 Indicator：RSIIndicator（依赖 Feature 0006；实现：`hft/indicator/computed/`，注册：`hft/indicator/factory.py`）（审核完成）
- [x] 计算类 Indicator：requires 模式维护自身 `_data` + 覆盖 `ready_internal()`（以 RSIIndicator 为基准样例）（审核完成）
- [x] 配置与兼容：历史 `spread` 比例写法迁移与 demo 配置更新（审核完成）
- [x] 文档更新：`docs/executor.md`、`docs/indicator.md`（审核完成）
- [x] 配置加载层：支持 `ready_condition` 通过 `set_ready_condition(...)` 注入（不放入 `params`）（审核完成）

> 相关追踪：`issue/0006-feature-0005-implementation-gaps.md`、`issue/0007-feature-0005-computed-indicators-not-ready.md`

## 兼容性说明

- **向后兼容**：现有配置无 requires/condition 时，行为与当前一致
- **SmartExecutor routes**：现有 condition 语法保持不变，仅扩展可用变量
- **edge → medal_edge**：需要迁移现有配置，可提供迁移脚本或兼容层

## 依赖关系

- **依赖 Feature 0006**（Indicator 与 DataSource 统一架构）：
  - `HealthyDataArray`：数据存储和健康检查
  - `BaseIndicator` / `BaseDataSource`：统一的 indicator 抽象
  - `IndicatorGroup`：indicator 管理和查找
  - `query_indicator`：lazy 创建、自动启动、ready 检查
- 依赖 Feature 0002（SmartExecutor Router）的 condition 表达式基础设施

## 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| calculate_vars 性能开销 | 每次 tick 调用，可能影响延迟 | 缓存计算结果，仅数据变化时重算 |
| 表达式求值安全 | 恶意表达式可能造成问题 | 使用 simpleeval 限制可用函数 |
| 配置复杂度增加 | 用户学习成本 | 提供默认值和示例配置、demo 配置与迁移指南 |
