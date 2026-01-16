# Feature: Indicator 与 DataSource 统一架构

## 背景

当前 DataSource 和 Indicator 是两套独立概念，导致概念冗余、生命周期分散、健康检查不统一。

## 目标

**统一为同一抽象**：DataSource 是从 exchange 获取数据的特殊 Indicator，普通 Indicator 从其他 Indicator 计算数据。

## 核心设计

### 1. HealthyDataArray

位置：`hft/core/healthy_data.py`

#### 1.1 与现有实现的关系

| 现有实现 | 本 Feature | 关系 |
|----------|------------|------|
| `hft/datasource/group.py` 中的 `DataArray` | `HealthyDataArray` | **替换** |
| `HealthyData` (单值缓存) | 保留 | **并存**，用于单值场景 |

**替换原因**：
- 现有 `DataArray` 健康检查维度主要是 freshness/count/coverage（并不覆盖 cv/range 这类“采样均匀度/覆盖比例”指标）
- 现有 `DataArray` 的存储结构以 append 为主，不支持“按数据时间戳排序 + 中间插入 + 去重策略”的统一抽象（对乱序 WS/历史回填不友好）
- 本 Feature 需要把“存储 + 健康指标 + ready_condition 求值”统一到 Indicator/DataSource 的共同基类上，避免两套生命周期与健康口径并存

#### 1.2 存储格式

```python
_data: list[tuple[float, T]]  # [(timestamp, value), ...]
```

#### 1.2.1 批量更新（权威快照优化 / assign 语义）

当上游一次性返回“该指标窗口内的完整权威快照”（典型：OHLCV 的 fetch 返回最近 N 根/最近 window 秒内全部 candle），逐条 upsert 的复杂度与插入成本较高。

因此 `HealthyDataArray` 需要提供一种 **assign 语义** 的批量更新优化接口，例如：

- `assign(points: list[tuple[float, T]]) -> None`

语义：
- 将内部数据直接替换为该快照（等价于 `clear()` 后一次性设置为 points）
- 内部负责：
  - 按 timestamp 排序
  - 同 timestamp（duplicate_tolerance 内）按 `is_duplicate_fn` 做 replace 归并
  - 按 `max_seconds` 做 shrink（仅保留窗口内数据）

使用约束（必须写清楚）：
- 仅用于“权威快照”：该批数据必须覆盖当前窗口内的全部有效点；否则会误删缺失的历史点
- 与 watch 并行时需要避免竞态：要么在 assign 前暂停/串行化写入，要么保证 assign 后再立即用 watch 的最新点 upsert 覆盖（以免 clear 掉更近的新点）

**按时间戳排序 + 中间插入的必要性**：
- **乱序数据**：WebSocket 推送可能乱序到达（网络延迟、重传）
- **历史回填**：fetch 历史数据时需要插入到正确位置
- **统一的去重策略**：不同数据类型需要不同的去重键（见下文）

#### 1.3 去重/替换规则（append = upsert）

同一毫秒内可能存在多条 trades；OrderBook/Ticker 也可能在同一时间戳发生多次更新。因此 **不强制** "相同 timestamp 只保留一条"。

统一规则：`HealthyDataArray.append(timestamp, value)` 的语义是 **upsert**：
- 当 timestamp 与已有点在 `duplicate_tolerance` 范围内，且 `is_duplicate_fn(old, new)==True`：**replace**（覆盖旧值）
- 否则：按 timestamp 有序插入（支持历史回填/乱序插入）

- Snapshot 类（如 Ticker/OrderBook snapshot、OHLCV candle）：通常可以按 `timestamp` 去重（同 timestamp 覆盖旧值）
- Event 类（如 Trades）：通常按 `trade_id`（优先）或 `(timestamp, price, amount, side, id)` 去重；若无可靠 id，可选择不去重或仅做弱去重

**OHLCV fetch vs watch 的更新方式**：
- watch：通常只会反复推送“最新一根/最近几根”K 线；同 timestamp 的 candle 会通过 upsert replace 覆盖，保证最新 close/volume 生效
- fetch：可能一次返回一段历史；逐条 append 会自动有序插入；与已有数据 timestamp 重叠时同样走 replace，避免重复或错位

> **⚠️ 去重机制说明**：
> 1. 去重是 **timestamp 容差前置** 的：只有当新数据的 timestamp 与已有数据在 `duplicate_tolerance`（默认 1e-6 秒）范围内时，才会调用 `is_duplicate_fn`
> 2. 默认行为：`is_duplicate_fn` 默认返回 `True`，即"时间戳接近就用新值覆盖旧值"（适合 Snapshot 类数据）
> 3. **Trades 等 Event 类数据**：必须传入“永不去重”的函数关闭去重（例如 `hft/core/healthy_data.py:_never_duplicate`；不要用 lambda 以免 pickle 失败），否则同一时间戳的多条 trade 会互相覆盖
> 4. **跨时间戳的 id 去重**（如 trade_id 漂移）：当前接口不支持，需要在 DataSource 层自行维护 seen_ids 集合

#### 1.4 时间戳规范

| 属性 | 规范 |
|------|------|
| 单位 | **秒**（float，支持小数表示毫秒精度） |
| 来源 | 优先使用交易所返回的时间戳，无则用 `time.time()` |
| 精度 | 毫秒级（0.001 秒），去重容差默认 `1e-6` 秒 |

```python
# 示例：交易所返回毫秒时间戳，转换为秒
timestamp = exchange_timestamp_ms / 1000.0
```

#### 1.5 健康判断属性

| 属性 | 说明 | 健康条件 |
|------|------|----------|
| `timeout` | 当前时间 - 最新时间戳 | `< threshold`（数据新鲜） |
| `cv` | 采样间隔变异系数 | `< threshold`（采样均匀） |
| `range` | 实际覆盖时间 / 期望窗口 | `> threshold`（覆盖足够） |

#### 1.6 健康指标的边界情况处理

**cv 和 range 的计算需要指定时间范围**：

```python
def get_cv(self, start_timestamp: float, end_timestamp: float, min_points: int = 3) -> float
def get_range(self, start_timestamp: float, end_timestamp: float, min_points: int = 3) -> float
```

**min_points 语义**：最少数据点数。例如 `min_points=3` 需要至少 3 个数据点。

**边界情况处理**（返回有效极端值，确保 `ready_condition` 表达式求值不出错）：

| 情况 | cv 返回 | range 返回 | 说明 |
|------|---------|------------|------|
| 数据点 < min_points | `100.0` | `0.0` | 数据不足，视为极不健康 |
| 窗口内无数据 | `100.0` | `0.0` | 无数据，视为极不健康 |
| 采样间隔均值 ≈ 0 | `100.0` | 正常计算 | 无法计算 cv |
| 期望窗口 ≈ 0 | 正常计算 | `0.0` | 无法计算 range |

**timeout 定义**：当前时刻与最新数据时间戳的差值（秒），无数据时返回 `float('inf')`

**ready_condition 示例**：

```yaml
ready_condition: "timeout < 60 and cv < 0.8 and range > 0.6"
```

- `timeout < 60`：最新数据不超过 60 秒前
- `cv < 0.8`：采样间隔变异系数小于 0.8（较均匀）
- `range > 0.6`：覆盖至少 60% 的期望窗口

**期望窗口的来源**：
- `is_ready()` 内部使用 indicator 配置的 `window` 参数
- 计算时传入 `(now - window, now)` 作为时间范围

```python
def is_healthy(self, timeout_threshold, cv_threshold, range_threshold) -> bool:
    return timeout < timeout_threshold and cv < cv_threshold and range > range_threshold
```

**无 window 的指标/数据源如何处理**：
- 对于“不需要数组语义”的场景（例如只关心最新值的 Ticker/单值健康态），可以使用 `HealthyData`（单值缓存）而非 `HealthyDataArray`
- 为了让 `ready_condition` 写法统一：当 indicator 没有 `window` 或 `window <= 0` 时，规定
  - `cv = 0.0`（视为采样均匀，不阻塞）
  - `range = 1.0`（视为覆盖完整，不阻塞）
  - `timeout` 仍按“当前时间 - 最新时间戳”计算（无数据则 `inf`）

推荐：这类指标的 `ready_condition` 只写 `timeout`（例如 `"timeout < 10"`）。

**表达式求值（安全性约束）**：
- `ready_condition` 使用受限求值器（simpleeval），仅提供变量 `timeout/cv/range`；禁用函数调用（例如不允许 `len(...)`）
- 表达式出错（语法/变量名等）时不抛出到上层：记录 warning 并返回 not ready（False）

### 2. 层级结构

```
IndicatorGroup（将逐步替代 DataSourceGroup）
├── GlobalIndicators
│   └── GlobalFundingRateDataSource, ...
└── LocalIndicators
    └── (exchange_class, symbol) -> TradingPairIndicators
        └── OHLCVDataSource, TradesDataSource, RSIIndicator, ...
```

### 3. BaseIndicator 抽象

#### 3.1 类定义

```python
class BaseIndicator(Listener, Generic[T]):
    # 核心属性
    _data: HealthyDataArray[T]
    _ready_condition: Optional[str]  # 配置注入的就绪条件表达式
    _expire_seconds: float  # 无 query 后自动停止时间
    _event: AsyncIOEventEmitter  # pyee 事件发射器（统一字段名）
    _window: float  # HealthyDataArray 的时间窗口长度（秒），即 max_seconds（也用于 cv/range 的计算窗口）

    # 生命周期
    def touch(self) -> None: """更新查询时间，防止过期"""
    def is_expired(self) -> bool: """是否已过期"""
    def is_ready(self) -> bool: """根据 ready_condition 求值"""

    # 事件（委托给 _event）
    def on(self, event: str, handler: Callable) -> None: ...
    def emit(self, event: str, *args) -> None: ...

    # 抽象方法
    @abstractmethod
    def calculate_vars(self, direction: int) -> dict[str, Any]: ...
```

#### 3.2 calculate_vars 语义说明

```python
@abstractmethod
def calculate_vars(self, direction: int) -> dict[str, Any]:
    """
    计算并返回该指标提供的变量字典

    Args:
        direction: 交易方向
            - 1: 多头方向（买入开多 / 卖出平空）
            - -1: 空头方向（卖出开空 / 买入平多）

    Returns:
        变量字典，用于 Executor 的 condition 表达式求值
        例如 {"medal_edge": 0.0005, "rsi": 65.0}

    用途：
        - 供 Executor.condition 表达式使用（Feature 0005）
        - 供 Strategy 决策使用
        - **不用于** ready_condition 求值（ready_condition 使用 timeout/cv/range）

    注意：
        - 这是"输出变量"，不是 indicator 的内部状态
        - indicator 的历史数据存储在 _data: HealthyDataArray 中
        - 如需访问历史，通过 _data 迭代或索引
    """
```

#### 3.3 事件机制规范

**统一字段名**：所有 Indicator 使用 `_event` 字段（与现有 `BaseDataSource.event` 对齐）

**标准事件**：

| 事件名 | 载荷 | 触发时机 |
|--------|------|----------|
| `update` | `(timestamp: float, value: T)` | 新数据写入 `_data` 后 |
| `ready` | `()` | 从 not ready 变为 ready |
| `error` | `(error: Exception)` | 发生错误 |

```python
# 示例：监听 update 事件
indicator.on("update", lambda ts, val: print(f"New data: {val}"))
```

#### 3.4 interval 与 tick 机制

**实现口径**：`Listener.interval` 已调整为 `Optional[float]`；当 `interval=None` 时调度层跳过创建 tick task（事件驱动/被动更新）。

| interval 值 | 行为 |
|-------------|------|
| `None` | 不创建 tick task，由事件驱动（推荐用于 Indicator） |
| `> 0` | 创建 tick task，按间隔调用 `tick_callback` |
| `= 0.0` | 创建 tick task，并尽可能快地循环（用于“很快”的轮询/计算；注意可能造成 busy loop） |

```python
class BaseIndicator(Listener, Generic[T]):
    def __init__(self, interval: Optional[float] = None, ...):
        # interval=None 表示事件驱动，不参与 tick 循环
        super().__init__(interval=interval, ...)
```

**实现要点（Listener/调度层）**：
- `Listener.interval` 类型改为 `Optional[float]`
- `Listener.update_background_task()` / 调度层创建后台任务时：当 `interval is None` 时直接跳过创建（即使 enabled）
- 兼容性：已有 Listener 传入 `0.0` 的，语义保持为“尽可能快”；若希望完全不 tick，请改为 `None`

#### 3.5 子类定义

```python
class GlobalIndicator(BaseIndicator[T]):
    """全局唯一，更长过期时间（默认 1h）"""


class BaseDataSource(BaseIndicator[T]):
    """从 exchange 获取数据，支持 watch/fetch 两种模式"""

    @abstractmethod
    async def _watch(self) -> None: ...

    @abstractmethod
    async def _fetch(self) -> None: ...
```

### 4. AppCore.query_indicator

#### 4.1 接口定义

```python
def get_indicator(
    self,
    indicator_id: str,
    exchange_class: Optional[str],  # GlobalIndicator 传 None
    symbol: Optional[str],          # GlobalIndicator 传 None
) -> Optional[BaseIndicator]:
    """
    获取 indicator 实例（不管 ready 与否）。

    行为：lazy 创建、自动启动、touch 更新。
    用途：订阅 update/ready 事件、访问 _data、调试/观测。

    Returns:
        - BaseIndicator 实例：成功获取或创建
        - None：无法创建（未注册 factory 或创建失败）
    """

def query_indicator(
    self,
    indicator_id: str,
    exchange_class: Optional[str],  # GlobalIndicator 传 None
    symbol: Optional[str],          # GlobalIndicator 传 None
) -> Optional[BaseIndicator]:
    """
    查询 indicator，支持 lazy 创建和自动启动

    Returns:
        - BaseIndicator 实例：indicator ready
        - None：indicator 未 ready 或无法创建
    """
```

#### 4.2 返回 Optional 的语义

| 返回值 | 含义 | 调用方处理 |
|--------|------|------------|
| `BaseIndicator` | ready，可安全使用 | 调用 `calculate_vars()` |
| `None` | 未 ready（正在初始化/数据不足） | 跳过本次计算，等待下次 tick（如需订阅事件，用 get_indicator 获取实例） |

**关键点**：`query_indicator()` 返回 `None` 不代表实例不存在；如需实例（订阅事件/观测数据），请使用 `get_indicator()`。

#### 4.3 依赖链处理

**场景**：RSIIndicator 依赖 OHLCVDataSource

```python
class RSIIndicator(BaseIndicator[float]):
    def __init__(self, exchange_class: str, symbol: str, ohlcv: str, window: float, ...):
        super().__init__(window=window, interval=None, ...)  # 事件驱动
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._ohlcv_id = ohlcv

    async def on_start(self) -> None:
        # 不在构造时拿依赖实例，避免构造期耦合；在启动后通过 IndicatorGroup 获取
        app = self.root
        if app is None:
            return
        ohlcv = app.get_indicator(self._ohlcv_id, self._exchange_class, self._symbol)
        if ohlcv is not None:
            ohlcv.on("update", self._on_ohlcv_update)

    def _on_ohlcv_update(self, timestamp: float, candle: Any) -> None:
        # 计算 RSI 并写入自身 _data。依赖 OHLCV 同一根 candle 更新时 timestamp 不变，
        # 通过 HealthyDataArray.append() 的 upsert 语义触发 replace。
        rsi = self._calculate_rsi_from_ohlcv()
        self._data.append(timestamp, rsi)
        self._emit_update(timestamp, rsi)

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        # 优先返回自身缓存（window 控制历史长度；同 timestamp 会 replace）
        return {"rsi": self._data.latest if self._data else 50.0}
```

**原则**：
- 依赖通过字符串 ID 声明，不在构造时直接传入/持有其他 Indicator 实例；在运行期（on_start/calculate_vars）再获取
- 依赖未 ready 时返回默认值或空字典，不阻塞

补充（推荐做法）：对于“计算型 Indicator”（RSI/MidPrice 等），更推荐在依赖的 `update` 事件中计算并写入自身 `_data`，
这样 `query_indicator()` 的 ready 语义与 `window`（历史长度）才成立；replace 行为由 `HealthyDataArray.append()` 的 upsert 语义承担。

#### 4.4 并发安全

**缓存键（概念口径）**：`(indicator_id, exchange_class, symbol)`（实际实现拆分为 GlobalIndicators + TradingPairIndicators 容器；语义一致）

```python
class IndicatorGroup:
    # 伪代码：展示 get_indicator/query_indicator 的语义（实际实现见 `hft/indicator/group.py`）
    _cache: dict[tuple[str, Optional[str], Optional[str]], BaseIndicator]

    def query_indicator(self, indicator_id, exchange_class, symbol):
        key = (indicator_id, exchange_class, symbol)

        # 快速路径：已存在
        if key in self._cache:
            indicator = self._cache[key]
            indicator.touch()
            return indicator if indicator.is_ready() else None

        # 慢速路径：需要创建
        #
        # 说明：本接口保持为同步函数（不包含 await），在单线程事件循环中是“原子”的，
        # 因此不会出现两个协程在同一 key 上并发创建的竞态条件。
        # 如果未来把这里改为 async 并引入 await，再补 asyncio.Lock 做并发保护。
        indicator = self._create_indicator(indicator_id, exchange_class, symbol)
        self._cache[key] = indicator
        asyncio.create_task(indicator.start())  # 异步启动，不阻塞调用方
        indicator.touch()
        return indicator if indicator.is_ready() else None
```

### 5. App Config 示例

```yaml
indicators:
  ohlcv-1m:
    class: OHLCVDataSource
    params:
      timeframe: "1m"
      window: 86400  # 秒（1天 = 86400秒）
    ready_condition: "timeout < 60 and cv < 0.8 and range > 0.6"

  rsi:
    class: RSIIndicator
    params:
      ohlcv: ohlcv-1m  # 字符串引用，通过 get_indicator() 获取实例并自行判断 ready
      period: 14
      # window 的语义是 HealthyDataArray 的时间窗口长度（秒）。
      # 对于 RSIIndicator：window 用于保留 RSI 的历史序列（用于观测/回测/条件表达式），并不要求“纯 append”。
      # 推荐语义：当依赖的 OHLCV 在同一根 candle 上更新时（timestamp 不变），RSI 调用 HealthyDataArray.append()
      # 触发“同 timestamp -> replace”（upsert）；当进入新 candle（timestamp 前进）时，append 新点即可。
      window: 3600  # 秒（1小时 = 3600秒）
```

**配置注意事项（Phase 1.5 审核要点）**：
- `ready_condition` 不属于 `params`：indicator 实例构造完成后，再由配置/加载层调用 `set_ready_condition(...)` 注入
- `ready_condition: null` 的含义：不增加额外限制（表达式视为 True），但 **indicator 仍需满足自身内置 ready 规则**（通过 `ready_internal()` 实现；`BaseIndicator` 默认“至少有 1 个可用数据点”，其他 indicator 可更严格）
- 由于 `ready_condition` 的变量（timeout/cv/range）来自 indicator 自己的 `_data` 健康统计：计算型 indicator 也必须维护自身 `_data`（在依赖数据源 update 时写入新点或 upsert/replace），否则无法得到正确的健康口径与 ready 行为
- `IndicatorFactory` 目前的内置映射只覆盖 DataSource（Ticker/Trades/OrderBook/OHLCV）；类似 `RSIIndicator` 的计算型指标若要通过 YAML 创建，需要扩展内置映射或提供自定义 factory（否则会提示 Unknown indicator class）
- DataSource 使用 `mode: fetch` 时必须配置 `interval`（否则不会触发 `_fetch()`）；`mode: watch` 可保持 `interval=None`（事件驱动，不创建 tick task）

### 6. 自动过期机制

长时间未被 query 的 indicator 自动停止，释放资源：

```python
class BaseIndicator:
    _last_touch: float  # 上次 touch 时间
    _expire_seconds: float  # 过期时间（默认 300s，GlobalIndicator 默认 3600s）

    def touch(self) -> None:
        self._last_touch = time.time()

    def is_expired(self) -> bool:
        return time.time() - self._last_touch > self._expire_seconds
```

### 7. 事件驱动示例（GlobalFundingRateDataSource -> FundingRateDataSource）

`GlobalFundingRateDataSource` 负责“全市场拉取/订阅资金费率并广播 update 事件”；`FundingRateDataSource` 只做被动容器（不 on_tick），通过订阅全局源的 update 来更新自身的 `HealthyDataArray`：

```python
# 伪代码：展示事件驱动关系（interval=None -> 不创建 tick task）

class GlobalFundingRateDataSource(GlobalIndicator[tuple[str, dict[str, FundingRate]]]):
    def __init__(self, ...):
        super().__init__(interval=None)

    async def _watch_or_fetch(self) -> None:
        for exchange in app.exchange_group.children.values():
            if not exchange.ready:
                continue
            exchange_class = exchange.class_name
            funding_rates = await exchange.medal_fetch_funding_rates()  # {symbol: FundingRate}
            ts = time.time()
            payload = (exchange_class, funding_rates)
            self._data.append(ts, payload)
            self._emit_update(ts, payload)  # 标准事件载荷：(timestamp, value)


class FundingRateDataSource(BaseIndicator[FundingRate]):
    def __init__(self, exchange_class: str, symbol: str, ...):
        super().__init__(interval=None)  # 自己不 tick
        self._exchange_class = exchange_class
        self._symbol = symbol
        global_fr = app.get_indicator("global_funding_rate", None, None)
        if global_fr:
            global_fr.on("update", self._on_global_update)

    def _on_global_update(self, timestamp: float, payload: tuple[str, dict[str, FundingRate]]) -> None:
        exchange_class, funding_rates = payload
        if exchange_class != self._exchange_class:
            return
        fr = funding_rates.get(self._symbol)
        if fr is None:
            return
        self._data.append(fr.timestamp, fr)
        self._emit_update(fr.timestamp, fr)
```

## 迁移影响

### 迁移映射

| 原模块 | 新模块 |
|--------|--------|
| `DataSourceGroup` | `IndicatorGroup` |
| `DataType` 枚举 | 删除 |
| `hft/datasource/` | 合并到 `hft/indicator/datasource/` |

### 分阶段迁移计划

**Phase 1：基础设施（本 Feature 范围）**
- 实现 `HealthyDataArray`
- 重构 `BaseIndicator` 基类，添加 `_event`、`calculate_vars`
- 实现 `IndicatorGroup` 和 `query_indicator`

**Phase 2：DataSource 迁移（部分完成）**
- （已完成）将 `hft/datasource/` 下的市场数据源（Ticker/Trades/OrderBook/OHLCV）迁移到 `hft/indicator/datasource/`
- （已完成）继承新的 `BaseDataSource`（继承自 `BaseIndicator`）
- （已完成）添加 DEPRECATED 标记到旧模块
- （已完成）运行期 DataArray 使用方迁移到 `HealthyDataArray`（旧 DataArray 仅保留兼容导出；移除见 Phase 3）

**Phase 3：清理（后续 Feature）**
- 删除 `DataType` 枚举
- 删除旧的 `DataSourceGroup`
- 更新所有引用

### Non-Goals（不在本 Feature 范围）

| 不做的事 | 原因 |
|----------|------|
| 完整迁移 `LazyIndicator`（脱离旧 DataSourceGroup/DataType） | 仍依赖旧链路，需与 Phase 3 清理一起做，避免破坏向后兼容 |
| 删除 `HealthyData`（单值） | 与 `HealthyDataArray` 并存，用于不同场景 |
| 实现所有具体 Indicator | 由 Feature 0005 定义，本 Feature 只提供基础设施 |

## TODO

> Phase 1：基础设施（本 Feature 范围）

- [x] 实现 HealthyDataArray（审核完成）
- [x] HealthyDataArray.assign() 批量更新方法（审核完成）
  - 实现权威快照优化接口，支持批量替换数据
  - 自动排序、去重归并、shrink
  - 单元测试：`tests/test_healthy_data_array.py::TestHealthyDataArrayAssign`
- [x] HealthyDataArray 单元测试（审核完成）
- [x] 重构 BaseIndicator 基类，添加 `_event`、`_data`、`calculate_vars`（审核完成）
- [x] 实现 GlobalIndicator（审核完成）
- [x] 实现 BaseDataSource 骨架（继承 BaseIndicator）（审核完成）
- [x] 实现 IndicatorGroup（审核完成）
- [x] IndicatorGroup.get_indicator 和 query_indicator（审核完成）
- [x] BaseIndicator 自动过期机制（审核完成）
- [x] 单元测试：BaseIndicator、IndicatorGroup、query_indicator（审核完成）
- [x] 迁移 DataListener 到 hft/indicator/persist/（审核完成）
- [x] 更新文档（审核完成）

> Phase 1.5：配置驱动与落地

- [x] BaseDataSource 完善（审核完成）
  - 覆盖：on_start() 启动 watch 协程；exchange 引用机制（通过 root.exchange_group + exchange_class 获取 exchange 实例）；_watch_task 管理（启动/停止）
- [x] 实现具体 DataSource 示例（审核完成）
  - 覆盖：TickerDataSource（继承 BaseDataSource）+ 单元测试
- [x] AppCore 集成 IndicatorGroup（审核完成）
  - 覆盖：AppCore 创建/管理 IndicatorGroup；暴露 query_indicator / get_indicator 接口
- [x] 配置驱动创建指标（审核完成）
  - 覆盖：从 YAML 配置解析 indicator 定义；注册 indicator factory（`params` 会作为 **kwargs 传给 indicator）

> Phase 2：DataSource 迁移（部分完成）

- [x] 将 `hft/datasource/` 下的市场数据源（Ticker/Trades/OrderBook/OHLCV）迁移到 `hft/indicator/datasource/`（审核完成）
  - 已完成：`hft/indicator/datasource/` 下已存在 `TickerDataSource/TradesDataSource/OrderBookDataSource/OHLCVDataSource`
  - 修复内容：
    - `TradesDataSource` 使用 `_never_duplicate` 函数替代 lambda，确保可 pickle
    - `TradesDataSource/OrderBookDataSource/OHLCVDataSource` 的 `from_ccxt()` 对 `timestamp=None` 健壮处理
  - 回归测试：`tests/test_ticker_datasource.py::TestRegressionFeature0006Phase2`
- [x] 继承新的 `BaseDataSource`（继承自 `BaseIndicator`）（审核完成）
  - 已完成：所有 DataSource 类继承关系正确，pickle 兼容性和 timestamp 健壮性已修复
- [x] DataArray 使用方迁移到 HealthyDataArray（运行期不再依赖 DataArray）（审核完成）
  - 证据：生产代码中不存在 `DataArray(...)` 实例化；资金费率容器已使用 `HealthyDataArray`（`hft/datasource/funding_rate_datasource.py`）
  - 兼容：旧 `hft/datasource/group.py:DataArray` 仍被 `hft/datasource/__init__.py` 导出（仅用于旧代码兼容），将在 Phase 3 移除
- [x] 添加 DEPRECATED 标记到旧模块（审核完成）
  - 覆盖：`hft/datasource/base.py`、`hft/datasource/group.py`、`hft/datasource/ticker_datasource.py`、`hft/datasource/trades_datasource.py`、`hft/datasource/orderbook_datasource.py`、`hft/datasource/ohlcv_datasource.py`

> Phase 3：清理（后续 Feature）

- [x] 旧 DataArray 标记为 DEPRECATED（保留兼容导出；完全删除移交 Feature 0007）（审核完成）
  - 已添加 DEPRECATED 标记到 `hft/datasource/group.py`
  - 生产链路已迁移到 `HealthyDataArray`；旧 `DataArray` 仅作为旧模块兼容与历史测试保留
- [x] 旧 DataType 标记为 DEPRECATED（字符串 ID 为主；完全删除移交 Feature 0007）（审核完成）
  - 已添加 DEPRECATED 标记到 `hft/datasource/group.py`
  - 新链路优先使用字符串 ID（如 `"ticker"`, `"trades"`），仍保留枚举作为旧回退路径兼容
- [ ] 删除旧的 `DataSourceGroup`（待实现）
  - **→ 已移至 [Feature 0007: 移除 DataSourceGroup](./0007-remove-datasource-group.md)**
  - 依赖分析：
    - `AppCore` 创建并持有 `datasource_group` 实例
    - `GlobalFundingRateFetcher` 依赖 `datasource_group.children` 和 `exchange_group`
    - `avellaneda_stoikov_executor` 回退路径使用 `datasource_group.query()`
  - 前置条件：
    - 迁移 `GlobalFundingRateFetcher` 到 `IndicatorGroup` 架构
    - 移除 `AppCore.datasource_group`
    - 更新所有回退路径
- [x] `LazyIndicator` 支持字符串 ID（兼容 DataType）（审核完成）
  - 说明：目前仍通过 `DataType` 适配到旧 `TradingPairDataSource.query(DataType)`；这是“API 入口兼容改造”，不是“架构迁移”
  - 已覆盖：`VWAPIndicator`、`SpreadIndicator`、`MidPriceIndicator`、`TradeIntensityIndicator` 的 `depends_on` 均使用字符串 ID
- [x] `LazyIndicator` 优先使用 IndicatorGroup 获取数据源（保留旧回退路径）（审核完成）
  - 已完成：`get_datasource()` 优先 `IndicatorGroup.get_indicator()`，并提供 `_get_indicator_group()` / `_get_exchange_info()`；保留旧架构回退以兼容
  - 注意：新 `hft/indicator/datasource/*` 的时间戳单位为“秒(float)”；旧 `hft/datasource/*` 多为“毫秒(int)”——混用时必须统一单位
  - 说明：移除旧回退路径与删除 `DataType` 属于 Feature 0007 的收尾工作
- [x] 更新关键引用（Executor 回退路径优先走 IndicatorGroup）（审核完成）
  - `avellaneda_stoikov_executor` 已迁移：
    - 添加 `indicator_group` 属性和 `_get_datasource()` 辅助方法
    - 优先使用 `IndicatorGroup.get_indicator()`，回退到旧 `DataSourceGroup.query()`
    - 修复原有 `query_single` 方法不存在的 bug

