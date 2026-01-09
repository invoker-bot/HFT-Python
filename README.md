# HFT-Python

基于 Listener 架构的全异步高频交易策略框架。

## 架构概览

```
AppCore (根节点)
├── CacheListener              # 状态持久化（异步写入）
├── StateLogListener           # 状态日志输出
├── UnhealthyRestartListener   # 不健康自动重启
│
├── ExchangeGroup              # 交易所分组管理
│   ├── okx: [OKX_1, OKX_2, ...]
│   ├── binance: [Binance_1, Binance_2, ...]
│   └── ...
│
├── DataSourceGroup            # 数据源管理
│   ├── FundingRateDataSource
│   ├── OHLCVDataSource
│   ├── OrderBookDataSource
│   ├── TickerDataSource
│   └── TradesDataSource
│
├── StrategyGroup              # 策略组
│   ├── Strategy1
│   ├── Strategy2
│   └── ...
│
└── Executor                   # 全局执行器
```

---

## 核心组件接口

### 1. AppCore - 应用根节点

所有 Listener 的根节点，管理生命周期、持久化、自愈。

```python
class AppCore(Listener):
    """
    应用核心，所有 Listener 的根节点

    职责：
    - 管理所有子 Listener 的生命周期
    - 协调 ExchangeGroup、DataSourceGroup、StrategyGroup、Executor
    - 提供全局配置和数据库连接
    """

    def __init__(self, config: "AppConfig"):
        super().__init__(interval=config.interval)
        self.config = config

        # 内置监听器
        self.add_child(CacheListener())
        self.add_child(StateLogListener())
        self.add_child(UnhealthyRestartListener())

        # 核心组件
        self.exchange_group = ExchangeGroup()
        self.datasource_group = DataSourceGroup()
        self.strategy_group = StrategyGroup()
        self.executor = Executor()

        self.add_child(self.exchange_group)
        self.add_child(self.datasource_group)
        self.add_child(self.strategy_group)
        self.add_child(self.executor)

    @cached_property
    def database(self) -> ClickHouseDatabase:
        """ClickHouse 数据库连接（延迟初始化）"""

    def loop(self) -> None:
        """同步阻塞式运行主循环"""

    async def run_ticks(self, duration: float = -1) -> None:
        """异步运行主循环，duration=-1 表示无限循环"""
```

---

### 2. ExchangeGroup - 交易所分组管理

按交易所类型组织多账户，数据去重，多账户同步执行。

```python
class ExchangeGroup(Listener):
    """
    交易所分组管理器

    设计理念：
    - 按 class_name 分组（okx, binance, ...）
    - 同类交易所共享数据订阅，避免重复获取
    - 下单时同类交易所的所有账户同步执行（老鼠仓模式）
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_watch_refs")

    def __init__(self):
        super().__init__("ExchangeGroup", interval=60.0)
        self._exchanges_map: dict[str, list[str]] = defaultdict(list)  # class_name -> [instance_names]
        self._watch_refs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # class_name -> {symbol: ref_count}

    # ===== 交易所管理 =====

    async def add_exchange(self, exchange: BaseExchange) -> None:
        """动态添加交易所实例"""

    async def remove_exchange(self, exchange: BaseExchange) -> None:
        """动态移除交易所实例"""

    def get_exchanges_by_class(self, class_name: str) -> list[BaseExchange]:
        """获取指定类型的所有交易所实例"""

    def get_primary_exchange(self, class_name: str) -> Optional[BaseExchange]:
        """获取指定类型的主交易所（用于数据订阅）"""

    # ===== 数据订阅管理（去重） =====

    async def watch(self, class_name: str, symbol: str, data_type: DataType) -> None:
        """
        订阅数据（引用计数）

        同一 class_name + symbol 只订阅一次，多次调用增加引用计数
        """

    async def unwatch(self, class_name: str, symbol: str, data_type: DataType) -> None:
        """
        取消订阅（引用计数）

        引用计数归零时才真正取消订阅
        """

    # ===== Watch with Fallback =====

    async def watch_with_fallback(
        self,
        class_name: str,
        symbol: str,
        watch_coro: Coroutine,
        fetch_coro: Coroutine,
        timeout: float = 5.0
    ) -> Any:
        """
        优先 watch，超时自动降级到 fetch

        Args:
            class_name: 交易所类型
            symbol: 交易对
            watch_coro: WebSocket 订阅协程
            fetch_coro: REST API 获取协程
            timeout: watch 超时时间（秒）
        """
        try:
            return await asyncio.wait_for(watch_coro, timeout=timeout)
        except asyncio.TimeoutError:
            self.logger.warning("Watch timeout for %s/%s, fallback to fetch", class_name, symbol)
            return await fetch_coro

    # ===== 多账户执行 =====

    async def execute_on_all(
        self,
        class_name: str,
        coro_factory: Callable[[BaseExchange], Coroutine]
    ) -> list[Any]:
        """
        在同类交易所的所有账户上执行（老鼠仓模式）

        Args:
            class_name: 交易所类型
            coro_factory: 接收 exchange 返回协程的工厂函数

        Returns:
            所有账户的执行结果列表
        """
        exchanges = self.get_exchanges_by_class(class_name)
        tasks = [coro_factory(ex) for ex in exchanges]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

---

### 3. DataSourceGroup - 数据源管理

维护各类市场数据，支持自动订阅/取消订阅。

```python
class DataType(Enum):
    """数据类型枚举"""
    FUNDING_RATE = "funding_rate"
    OHLCV = "ohlcv"
    ORDER_BOOK = "order_book"
    TICKER = "ticker"
    TRADES = "trades"


@dataclass
class DataArray(Generic[T]):
    """
    时序数据数组，支持自动过期清理

    Features:
    - 固定容量，超出自动淘汰旧数据
    - 支持按时间范围查询
    - 自动清理过期数据
    """
    data: deque[T] = field(default_factory=lambda: deque(maxlen=1000))
    max_age: float = 600.0  # 最大保留时间（秒）
    last_access: float = 0.0  # 最后访问时间

    def append(self, item: T) -> None:
        """追加数据"""

    def get_latest(self, n: int = 1) -> list[T]:
        """获取最新 n 条数据"""

    def get_since(self, timestamp: float) -> list[T]:
        """获取指定时间戳之后的数据"""

    def cleanup_expired(self) -> int:
        """清理过期数据，返回清理数量"""


class DataSourceGroup(Listener):
    """
    数据源管理器

    维护结构：
        _data[DataType][class_name][symbol] -> DataArray

    Features:
    - query 时自动订阅（watch）
    - 长时间无 query 自动取消订阅（unwatch）
    - 启动时从数据库加载最新数据
    - 定期写入 ClickHouse
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_data", "_watch_tasks")

    def __init__(self, auto_unwatch_timeout: float = 300.0):
        super().__init__("DataSourceGroup", interval=1.0)
        self._auto_unwatch_timeout = auto_unwatch_timeout

        # 数据存储: DataType -> class_name -> symbol -> DataArray
        self._data: dict[DataType, dict[str, dict[str, DataArray]]] = defaultdict(
            lambda: defaultdict(dict)
        )

        # 订阅状态: class_name -> symbol -> DataType -> last_query_time
        self._subscriptions: dict[str, dict[str, dict[DataType, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )

        # 后台 watch 任务
        self._watch_tasks: dict[str, asyncio.Task] = {}

    # ===== 核心查询接口 =====

    def query(
        self,
        data_type: DataType,
        class_name: str,
        symbols: list[str],
        since: Optional[float] = None,
        limit: Optional[int] = None
    ) -> dict[str, DataArray]:
        """
        查询数据（自动触发订阅）

        Args:
            data_type: 数据类型
            class_name: 交易所类型 (okx, binance, ...)
            symbols: 交易对列表
            since: 起始时间戳（可选）
            limit: 返回条数限制（可选）

        Returns:
            {symbol: DataArray} 字典

        Side Effects:
            - 自动订阅未订阅的 symbol
            - 更新 last_query_time
        """

    def query_single(
        self,
        data_type: DataType,
        class_name: str,
        symbol: str,
        since: Optional[float] = None,
        limit: Optional[int] = None
    ) -> Optional[DataArray]:
        """查询单个交易对的数据"""

    # ===== 数据写入 =====

    def push(
        self,
        data_type: DataType,
        class_name: str,
        symbol: str,
        data: Any
    ) -> None:
        """
        写入数据（由 watch 回调调用）

        同时发出 update 事件
        """
        arr = self._get_or_create_array(data_type, class_name, symbol)
        arr.append(data)
        self.emit("update", data_type, class_name, symbol, data)

    # ===== 自动订阅管理 =====

    async def _ensure_subscribed(
        self,
        data_type: DataType,
        class_name: str,
        symbol: str
    ) -> None:
        """确保已订阅（内部方法）"""

    async def _check_auto_unwatch(self) -> None:
        """
        检查并自动取消订阅

        在 on_tick 中调用，清理超过 auto_unwatch_timeout 未查询的订阅
        """

    # ===== 数据库同步 =====

    async def on_start(self) -> None:
        """启动时从数据库加载最新数据"""
        await super().on_start()
        await self._load_from_database()

    async def on_tick(self) -> None:
        """定期检查自动 unwatch + 清理过期数据 + 写入数据库"""
        await self._check_auto_unwatch()
        self._cleanup_expired_data()
        await self._flush_to_database()

    async def _load_from_database(self) -> None:
        """从 ClickHouse 加载最近数据"""

    async def _flush_to_database(self) -> None:
        """将缓存数据写入 ClickHouse"""

    # ===== 事件 =====
    # emit("update", data_type, class_name, symbol, data)  # 数据更新
    # emit("subscribed", data_type, class_name, symbol)    # 新订阅
    # emit("unsubscribed", data_type, class_name, symbol)  # 取消订阅
```

---

### 4. StrategyGroup & Strategy - 策略系统

策略组管理多个策略，策略发射交易信号。

```python
@dataclass
class TradeSignal:
    """
    交易信号（由 Strategy 发出，Executor 消费）

    Attributes:
        exchange_class: 交易所类型 (okx, binance, ...)
        symbol: 交易对 (BTC/USDT:USDT, ...)
        value: 期望仓位 [-1.0, 1.0]，最大仓位的百分比
               正数 = 做多，负数 = 做空，0 = 平仓
        speed: 执行紧急度 [0.0, 1.0]
               1.0 = 立即市价执行
               0.0 = 可以慢慢限价执行
    """
    exchange_class: str
    symbol: str
    value: float  # [-1.0, 1.0]
    speed: float  # [0.0, 1.0]

    # 元数据
    source: str = ""  # 策略名称
    timestamp: float = field(default_factory=time.time)
    reason: str = ""  # 信号原因
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        # 值域校验
        self.value = max(-1.0, min(1.0, self.value))
        self.speed = max(0.0, min(1.0, self.speed))

    @property
    def side(self) -> str:
        """推导方向: 'long', 'short', 'flat'"""
        if self.value > 0:
            return "long"
        elif self.value < 0:
            return "short"
        return "flat"

    @property
    def is_urgent(self) -> bool:
        """是否紧急（speed > 0.8）"""
        return self.speed > 0.8


class StrategyGroup(Listener):
    """
    策略组管理器

    管理多个策略，收集并转发交易信号给 Executor
    """

    def __init__(self):
        super().__init__("StrategyGroup", interval=1.0)
        self._strategies: dict[str, "BaseStrategy"] = {}

    def add_strategy(self, strategy: "BaseStrategy") -> None:
        """添加策略"""
        self._strategies[strategy.name] = strategy
        self.add_child(strategy)
        # 监听策略的信号事件
        strategy.on("signal", self._on_strategy_signal)

    def remove_strategy(self, name: str) -> None:
        """移除策略"""

    def _on_strategy_signal(self, signal: TradeSignal) -> None:
        """策略信号回调，转发给 Executor"""
        self.emit("signal", signal)

    @property
    def datasource(self) -> DataSourceGroup:
        """获取数据源（从 root 获取）"""
        return self.root.datasource_group


class BaseStrategy(Listener, ABC):
    """
    策略基类

    子类需要实现:
    - on_tick(): 每 tick 执行策略逻辑
    - 调用 emit_signal() 发出交易信号
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__,)

    def __init__(self, name: str, interval: float = 1.0):
        super().__init__(name, interval=interval)
        self._signal_count: int = 0

    @property
    def datasource(self) -> DataSourceGroup:
        """获取数据源"""
        return self.root.datasource_group

    @property
    def exchange_group(self) -> ExchangeGroup:
        """获取交易所组"""
        return self.root.exchange_group

    def emit_signal(
        self,
        exchange_class: str,
        symbol: str,
        value: float,
        speed: float = 0.5,
        reason: str = ""
    ) -> TradeSignal:
        """
        发出交易信号

        Args:
            exchange_class: 交易所类型
            symbol: 交易对
            value: 期望仓位 [-1.0, 1.0]
            speed: 执行紧急度 [0.0, 1.0]
            reason: 信号原因（日志用）

        Returns:
            发出的 TradeSignal
        """
        signal = TradeSignal(
            exchange_class=exchange_class,
            symbol=symbol,
            value=value,
            speed=speed,
            source=self.name,
            reason=reason
        )
        self._signal_count += 1
        self.logger.info("Signal: %s %s value=%.2f speed=%.2f (%s)",
                         exchange_class, symbol, value, speed, reason)
        self.emit("signal", signal)
        return signal

    def query_data(
        self,
        data_type: DataType,
        exchange_class: str,
        symbols: list[str]
    ) -> dict[str, DataArray]:
        """便捷方法：查询数据源"""
        return self.datasource.query(data_type, exchange_class, symbols)

    @abstractmethod
    async def on_tick(self) -> None:
        """
        策略逻辑（子类实现）

        典型流程：
        1. 从 datasource 获取数据
        2. 计算信号
        3. 调用 emit_signal() 发出信号
        """

    @property
    def log_state_dict(self) -> dict:
        """状态日志"""
        return {
            "signal_count": self._signal_count,
        }
```

**策略示例**：

```python
class FundingRateArbitrageStrategy(BaseStrategy):
    """资金费率套利策略示例"""

    def __init__(self, threshold: float = 0.001):
        super().__init__("FundingRateArbitrage", interval=60.0)
        self._threshold = threshold
        self._positions: dict[str, float] = {}  # symbol -> current_value

    async def on_tick(self) -> None:
        # 1. 获取资金费率数据
        data = self.query_data(
            DataType.FUNDING_RATE,
            "okx",
            ["BTC/USDT:USDT", "ETH/USDT:USDT"]
        )

        for symbol, arr in data.items():
            if not arr.data:
                continue

            latest = arr.get_latest(1)[0]
            funding_rate = latest.funding_rate

            # 2. 计算目标仓位
            current = self._positions.get(symbol, 0.0)

            if funding_rate > self._threshold:
                # 资金费率高，做空收费
                target = -0.5
                reason = f"High funding rate: {funding_rate:.4f}"
            elif funding_rate < -self._threshold:
                # 资金费率低，做多收费
                target = 0.5
                reason = f"Low funding rate: {funding_rate:.4f}"
            else:
                target = 0.0
                reason = "Neutral funding rate"

            # 3. 发出信号（仅当仓位变化时）
            if abs(target - current) > 0.1:
                self.emit_signal(
                    exchange_class="okx",
                    symbol=symbol,
                    value=target,
                    speed=0.3,  # 不紧急，可以限价
                    reason=reason
                )
                self._positions[symbol] = target
```

---

### 5. Executor - 全局执行器

监听策略信号，执行交易。

```python
class Executor(Listener):
    """
    全局执行器

    职责：
    - 监听 StrategyGroup 发出的 TradeSignal
    - 订单拆分（大单拆成小单）
    - 订单类型选择（限价/市价，基于 speed）
    - 仓位管理与风控
    - 多账户同步执行
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_signal_queue")

    def __init__(
        self,
        max_position_pct: float = 1.0,      # 最大仓位百分比
        split_threshold: float = 10000.0,    # 拆单阈值（USD）
        max_splits: int = 10,                # 最大拆单数
        order_timeout: float = 60.0,         # 订单超时（秒）
    ):
        super().__init__("Executor", interval=0.1)
        self._max_position_pct = max_position_pct
        self._split_threshold = split_threshold
        self._max_splits = max_splits
        self._order_timeout = order_timeout

        # 信号队列（按 speed 优先级排序）
        self._signal_queue: list[TradeSignal] = []

        # 当前仓位追踪: class_name -> symbol -> current_value
        self._positions: dict[str, dict[str, float]] = defaultdict(dict)

        # 活跃订单: order_id -> OrderRecord
        self._active_orders: dict[str, "OrderRecord"] = {}

        # 执行统计
        self._stats = {
            "signals_received": 0,
            "signals_executed": 0,
            "signals_rejected": 0,
            "orders_created": 0,
            "orders_filled": 0,
            "orders_canceled": 0,
        }

    async def on_start(self) -> None:
        await super().on_start()
        # 监听策略组的信号
        strategy_group: StrategyGroup = self.root.strategy_group
        strategy_group.on("signal", self._on_signal)

    def _on_signal(self, signal: TradeSignal) -> None:
        """信号回调：加入队列"""
        self._stats["signals_received"] += 1
        # 按 speed 降序插入
        for i, s in enumerate(self._signal_queue):
            if signal.speed > s.speed:
                self._signal_queue.insert(i, signal)
                return
        self._signal_queue.append(signal)
        self.emit("signal_queued", signal)

    async def on_tick(self) -> None:
        """处理信号队列"""
        while self._signal_queue:
            signal = self._signal_queue.pop(0)
            await self._execute_signal(signal)

    # ===== 信号执行 =====

    async def _execute_signal(self, signal: TradeSignal) -> bool:
        """
        执行交易信号

        流程：
        1. 风控检查
        2. 计算目标仓位变化
        3. 决定订单类型（基于 speed）
        4. 决定是否拆单
        5. 多账户执行
        """
        # 1. 风控检查
        if not await self._risk_check(signal):
            self._stats["signals_rejected"] += 1
            self.emit("signal_rejected", signal, "Risk check failed")
            return False

        # 2. 计算仓位变化
        current = self._positions[signal.exchange_class].get(signal.symbol, 0.0)
        delta = signal.value - current

        if abs(delta) < 0.01:  # 变化太小，忽略
            return True

        # 3. 决定订单类型
        order_type = self._decide_order_type(signal)

        # 4. 决定拆单
        splits = self._decide_splits(signal, delta)

        # 5. 执行
        exchange_group: ExchangeGroup = self.root.exchange_group

        for split_ratio in splits:
            split_delta = delta * split_ratio

            # 多账户同步执行
            results = await exchange_group.execute_on_all(
                signal.exchange_class,
                lambda ex: self._create_order(ex, signal, split_delta, order_type)
            )

            # 处理结果
            for result in results:
                if isinstance(result, Exception):
                    self.logger.error("Order failed: %s", result)
                else:
                    self._stats["orders_created"] += 1

        # 更新仓位
        self._positions[signal.exchange_class][signal.symbol] = signal.value
        self._stats["signals_executed"] += 1
        self.emit("signal_executed", signal)
        return True

    def _decide_order_type(self, signal: TradeSignal) -> str:
        """
        决定订单类型

        speed >= 0.8: 市价单
        speed >= 0.5: 激进限价单（靠近盘口）
        speed < 0.5:  保守限价单（有利价格）
        """
        if signal.speed >= 0.8:
            return "market"
        return "limit"

    def _decide_splits(self, signal: TradeSignal, delta: float) -> list[float]:
        """
        决定拆单比例

        Returns:
            比例列表，如 [0.5, 0.3, 0.2] 表示拆成 3 单
        """
        # TODO: 根据市场深度和金额决定
        return [1.0]  # 暂不拆单

    async def _risk_check(self, signal: TradeSignal) -> bool:
        """风控检查"""
        # TODO: 实现风控逻辑
        return True

    async def _create_order(
        self,
        exchange: "BaseExchange",
        signal: TradeSignal,
        delta: float,
        order_type: str
    ) -> Optional["Order"]:
        """创建订单"""
        side = "buy" if delta > 0 else "sell"
        amount = abs(delta) * self._max_position_pct

        # 计算价格（限价单）
        price = None
        if order_type == "limit":
            price = await self._calculate_limit_price(exchange, signal, side)

        return await exchange.create_order(
            symbol=signal.symbol,
            type=order_type,
            side=side,
            amount=amount,
            price=price
        )

    async def _calculate_limit_price(
        self,
        exchange: "BaseExchange",
        signal: TradeSignal,
        side: str
    ) -> float:
        """计算限价单价格"""
        orderbook = await exchange.fetch_order_book(signal.symbol, limit=5)

        if side == "buy":
            # 买单：根据 speed 决定靠近程度
            best_ask = orderbook["asks"][0][0]
            best_bid = orderbook["bids"][0][0]
            spread = best_ask - best_bid
            # speed 越高，越靠近 ask
            return best_bid + spread * signal.speed
        else:
            # 卖单：根据 speed 决定靠近程度
            best_ask = orderbook["asks"][0][0]
            best_bid = orderbook["bids"][0][0]
            spread = best_ask - best_bid
            # speed 越高，越靠近 bid
            return best_ask - spread * signal.speed

    @property
    def log_state_dict(self) -> dict:
        return {
            "queue_size": len(self._signal_queue),
            "active_orders": len(self._active_orders),
            **self._stats
        }

    # ===== 事件 =====
    # emit("signal_queued", signal)
    # emit("signal_executed", signal)
    # emit("signal_rejected", signal, reason)
    # emit("order_created", order)
    # emit("order_filled", order)
    # emit("order_canceled", order)
```

---

## 数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ExchangeGroup                               │
│   ┌─────────┐   ┌─────────┐   ┌─────────┐                           │
│   │   OKX   │   │ Binance │   │  Bybit  │  ...                      │
│   │ [1,2,3] │   │ [1,2]   │   │  [1]    │                           │
│   └────┬────┘   └────┬────┘   └────┬────┘                           │
│        │             │             │                                 │
│        └─────────────┼─────────────┘                                 │
│                      │ watch_with_fallback()                         │
└──────────────────────┼──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       DataSourceGroup                                │
│                                                                      │
│   _data[DataType][class_name][symbol] -> DataArray                  │
│                                                                      │
│   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                │
│   │ FundingRate  │ │    OHLCV     │ │  OrderBook   │ ...            │
│   │   DataArray  │ │   DataArray  │ │   DataArray  │                │
│   └──────────────┘ └──────────────┘ └──────────────┘                │
│                                                                      │
│   Features:                                                          │
│   - 自动过期清理                                                      │
│   - 自动 watch/unwatch                                               │
│   - 数据库同步                                                        │
│                                                                      │
│   query(DataType, class_name, symbols) -> {symbol: DataArray}       │
│                                                                      │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        StrategyGroup                                 │
│                                                                      │
│   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐        │
│   │   Strategy 1   │  │   Strategy 2   │  │   Strategy 3   │  ...   │
│   │                │  │                │  │                │        │
│   │ on_tick():     │  │ on_tick():     │  │ on_tick():     │        │
│   │   data=query() │  │   data=query() │  │   data=query() │        │
│   │   emit_signal()│  │   emit_signal()│  │   emit_signal()│        │
│   └───────┬────────┘  └───────┬────────┘  └───────┬────────┘        │
│           │                   │                   │                  │
│           └───────────────────┼───────────────────┘                  │
│                               │                                      │
│                     emit("signal", TradeSignal)                      │
│                                                                      │
└───────────────────────────────┼─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           Executor                                   │
│                                                                      │
│   TradeSignal:                                                       │
│   ┌─────────────────────────────────────────────┐                   │
│   │ exchange_class: "okx"                       │                   │
│   │ symbol: "BTC/USDT:USDT"                     │                   │
│   │ value: 0.5         # 期望仓位 50%            │                   │
│   │ speed: 0.8         # 紧急程度               │                   │
│   └─────────────────────────────────────────────┘                   │
│                                                                      │
│   Processing:                                                        │
│   1. 风控检查                                                         │
│   2. 订单类型选择 (speed >= 0.8 → 市价)                               │
│   3. 订单拆分                                                         │
│   4. 多账户执行 (老鼠仓)                                              │
│                                                                      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
                         ExchangeGroup
                      execute_on_all()
                    ┌────────┬────────┐
                    ▼        ▼        ▼
                 OKX_1    OKX_2    OKX_3
                 (order)  (order)  (order)
```

---

## 特性

| 特性 | 说明 |
|------|------|
| **全异步** | 基于 asyncio，非阻塞 I/O |
| **可持续运行** | 状态持久化 + 断点恢复 + 异常自愈 |
| **资源高效** | 自动订阅管理，数据去重 |
| **灵活扩展** | Listener 树形结构，易于添加组件 |
| **多账户** | 同类交易所多账户同步执行 |
| **事件驱动** | DataSource → Strategy → Executor 事件链 |

---

## 事件系统

```python
# DataSourceGroup
emit("update", data_type, class_name, symbol, data)
emit("subscribed", data_type, class_name, symbol)
emit("unsubscribed", data_type, class_name, symbol)

# Strategy
emit("signal", TradeSignal)

# Executor
emit("signal_queued", signal)
emit("signal_executed", signal)
emit("signal_rejected", signal, reason)
emit("order_created", order)
emit("order_filled", order)
```

---

## 配置示例

```yaml
# conf/app/main.yaml
interval: 1.0
health_check_interval: 60.0
log_interval: 120.0
cache_interval: 300.0
database_url: clickhouse://user:pass@localhost:8123/hft

exchanges:
  - okx_main
  - okx_sub1
  - binance_main

strategies:
  - funding_rate_arbitrage
  - grid_trading
```
