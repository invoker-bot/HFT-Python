# 执行器模块文档

## 概述

执行器（Executor）负责将策略的目标仓位转换为实际交易订单。

## 类层次

```
BaseExecutor (抽象基类)
├── MarketExecutor      # 市价单执行
├── LimitExecutor       # 限价单执行（做市）
└── PCAExecutor         # Position Cost Averaging（马丁格尔）
```

## BaseExecutor

### 核心属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `config` | BaseExecutorConfig | 配置对象 |
| `paused` | bool | 是否暂停 |
| `_active_orders` | dict | 活跃订单追踪 |
| `_stats` | dict | 执行统计 |

### 核心方法

```python
@abstractmethod
async def execute_delta(
    self,
    exchange: "BaseExchange",
    symbol: str,
    delta_usd: float,
    speed: float,
    current_price: float,
) -> ExecutionResult:
    """执行交易，子类必须实现"""
    pass

def usd_to_amount(self, exchange, symbol, usd, price) -> float:
    """USD 转合约数量"""
    base_amount = usd / price
    contract_size = exchange.get_contract_size(symbol)
    return base_amount / contract_size

async def manage_limit_orders(
    self,
    exchange: "BaseExchange",
    symbol: str,
    intents: list[OrderIntent],
    current_price: float,
) -> tuple[int, int, int]:
    """管理限价订单：创建/取消/复用"""
    pass
```

### on_tick 流程

```python
async def on_tick(self) -> bool:
    if self.paused:
        return False

    targets = self.strategy_group.get_aggregated_targets()

    for (exchange_name, symbol), target in targets.items():
        exchange = self.exchange_group.get_exchange(exchange_name)
        # ... 计算 delta_usd
        result = await self.execute_delta(exchange, symbol, delta_usd, speed, price)

    return False
```

## MarketExecutor

最简单的执行器，使用市价单立即成交。

### 配置

```yaml
# conf/executor/market/default.yaml
class_name: market
interval: 1.0
per_order_usd: 100.0
```

### 执行逻辑

```python
async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
    side = "buy" if delta_usd > 0 else "sell"
    amount = abs(self.usd_to_amount(exchange, symbol, delta_usd, current_price))

    order = await exchange.create_order(
        symbol=symbol,
        type="market",
        side=side,
        amount=amount,
    )

    return ExecutionResult(success=True, ...)
```

## LimitExecutor

做市类执行器，支持多层限价挂单。

### 配置

```yaml
# conf/executor/limit/maker.yaml
class_name: limit
interval: 0.5

orders:
  - spread: 0.001           # 距离当前价格 0.1%
    refresh_tolerance: 0.5  # 价格偏离 50% 时刷新
    timeout: 30             # 30秒后超时取消
    per_order_usd: 50

  - spread: 0.003
    refresh_tolerance: 0.5
    timeout: 60
    per_order_usd: 100

  - spread: 0.005
    reverse: true           # 反向订单（对冲）
    refresh_tolerance: 0.8
    timeout: 120
    per_order_usd: 200
```

### OrderIntent

```python
@dataclass
class OrderIntent:
    side: str           # "buy" | "sell"
    level: int          # 层级索引
    price: float        # 目标价格
    amount: float       # 数量
    timeout: float      # 超时时间
    refresh_tolerance: float  # 刷新容忍度
```

### 执行逻辑

1. 根据 delta_usd 方向确定 side
2. 为每个 level 计算 OrderIntent
3. 调用 `manage_limit_orders()` 管理订单

## PCAExecutor

Position Cost Averaging 执行器，马丁格尔风格。

### 特点

- 开仓单：在更优价格等待加仓
- 平仓单：在盈利价格等待止盈
- 根据加仓次数递增金额和距离

### 配置

```yaml
# conf/executor/pca/default.yaml
class_name: pca
interval: 5.0

base_order_usd: 100.0       # 基础订单金额
amount_multiplier: 1.5      # 加仓金额倍数
spread_open: 0.01           # 开仓距离（1%）
spread_close: 0.02          # 平仓距离（2%）
spread_multiplier: 1.2      # 加仓距离倍数
max_additions: 5            # 最大加仓次数
timeout: 3600               # 订单超时（1小时）
refresh_tolerance: 0.3      # 刷新容忍度
```

### 加仓计算

```python
# 第 n 次加仓金额
order_usd = base_order_usd * (amount_multiplier ** n)

# 第 n 次加仓距离
spread = spread_open * (spread_multiplier ** n)
```

### 仓位追踪

```python
@dataclass
class PositionInfo:
    amount: float          # 仓位数量
    cost_price: float      # 成本价
    addition_count: int    # 已加仓次数
```

## 订单管理

### 活跃订单追踪

```python
# key: (exchange_name, symbol, level)
# value: ActiveOrder
_active_orders: dict[tuple[str, str, int], ActiveOrder] = {}

@dataclass
class ActiveOrder:
    order_id: str
    created_at: float
    price: float
    amount: float
    side: str
    timeout: float
    refresh_tolerance: float
```

### 订单复用

```python
def can_reuse_order(self, active: ActiveOrder, intent: OrderIntent, current_price: float) -> bool:
    # 1. 检查超时
    if time.time() - active.created_at > active.timeout:
        return False

    # 2. 检查价格偏离
    price_diff = abs(active.price - intent.price) / intent.price
    if price_diff > active.refresh_tolerance:
        return False

    return True
```

### manage_limit_orders 流程

```
1. 对每个 intent：
   ├── 查找现有订单 (key = exchange, symbol, level)
   ├── 如果存在且可复用 → 跳过
   ├── 如果存在但需刷新 → 取消旧订单
   └── 创建新订单

2. 取消无对应 intent 的多余订单

3. 返回 (created, cancelled, reused)
```

## ExecutionResult

```python
@dataclass
class ExecutionResult:
    exchange_class: str     # 交易所类名
    symbol: str             # 交易对
    success: bool           # 是否成功
    exchange_name: str      # 交易所名称
    delta_usd: float        # 执行的 delta
    order_id: str = ""      # 订单 ID
    filled_amount: float = 0.0
    filled_price: float = 0.0
    error: str = ""         # 错误信息
```

## 最佳实践

### 选择执行器

| 场景 | 推荐执行器 |
|------|-----------|
| 快速调仓 | MarketExecutor |
| 做市/低滑点 | LimitExecutor |
| 马丁格尔策略 | PCAExecutor |

### 配置调优

```yaml
# 高频交易：短 interval，小 spread
interval: 0.1
spread: 0.0005

# 低频交易：长 interval，大 spread
interval: 5.0
spread: 0.01
```
