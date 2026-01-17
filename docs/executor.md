# 执行器模块文档

## 概述

执行器（Executor）负责将策略的目标仓位转换为实际交易订单。

### 数据驱动设计

本项目采用**数据驱动**的执行架构：

1. **Indicator 统一架构**：所有数据源（DataSource）都是特殊的 Indicator，统一通过 `IndicatorGroup` 管理
2. **变量注入机制**：Indicator 通过 `calculate_vars(direction)` 提供变量，Executor 通过 `requires` 声明依赖
3. **vars / conditional_vars**：支持变量计算和条件触发更新
4. **统一 order 配置**：所有 Executor 使用相同的 order 配置格式

```
┌─────────────────────────────────────────────────────────────┐
│                    数据驱动执行流程                          │
├─────────────────────────────────────────────────────────────┤
│  IndicatorGroup                                             │
│  ├── DataSource (ticker, trades, order_book, ...)          │
│  └── Computed Indicator (rsi, medal_edge, ...)             │
│           │                                                 │
│           ▼ calculate_vars(direction)                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Context Variables                                   │   │
│  │  {direction, buy, sell, speed, notional, mid_price,  │   │
│  │   rsi, medal_edge, volume, ...}                      │   │
│  └─────────────────────────────────────────────────────┘   │
│           │                                                 │
│           ▼ strategies namespace                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  strategies["position_amount"] = [0.01, 0.02]        │   │
│  │  strategies["speed"] = [0.1]                         │   │
│  └─────────────────────────────────────────────────────┘   │
│           │                                                 │
│           ▼ vars / conditional_vars                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Executor Decision                                   │   │
│  │  - vars: [{name: q, value: "..."}]                   │   │
│  │  - conditional_vars: {center_price: ...}             │   │
│  │  - orders / entry_orders / exit_orders               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 类层次

```
BaseExecutor (抽象基类)
├── MarketExecutor        # 市价单执行
├── LimitExecutor         # 限价单执行（做市）
├── MarketMakingExecutor  # 做市商执行器
├── PCAExecutor           # Position Cost Averaging（金字塔加仓）
└── SmartExecutor         # 智能路由执行器
```

---

## 统一的 Order 配置机制

所有 Executor 使用统一的 order 配置格式。

### Order 字段

```yaml
order:
  price: ...              # 绝对价格（可选）
  spread: ...             # 价差（当 price 未定义时：买一/卖一价 - sign(amount) * spread）
  order_usd: ...          # 订单金额
  order_amount: ...       # 订单数量（正=买，负=卖；定义后忽略 order_usd）
  refresh_tolerance: ...  # 刷新容忍度（比例）
  refresh_tolerance_usd: ... # 刷新容忍度（绝对值）
  timeout: ...            # 订单超时
  condition: ...          # 挂单条件
  vars: ...               # 订单级变量
  conditional_vars: ...   # 订单级条件变量
```

### Order 展开方式

**方式一：显式列表**

```yaml
orders:
  - spread: '0.0002 * mid_price'
    order_usd: 100
  - spread: '0.0004 * mid_price'
    order_usd: 200
```

**方式二：level 扩展**

```yaml
order_levels: 3  # 生成 level ∈ {-3, -2, -1, 1, 2, 3}
order:
  spread: '0.0002 * mid_price * abs(level)'
  order_usd: '100 * abs(level)'
```

**方式三：entry/exit 分离**

```yaml
entry_orders:  # 或 entry_order + entry_order_levels
  - spread: 0.0003
    order_usd: 100
exit_orders:   # 或 exit_order + exit_order_levels
  - spread: 0.0003
    order_usd: 100
```

> **注意**：
> - `entry_orders` 用于趋近目标（入场）
> - `exit_orders` 用于偏离目标（出场）
> - 使用 entry/exit 写法时不需要 `reverse` 参数

### 价格计算

订单价格基于 **best bid/ask** 计算，而非 mid_price：

| 方向 | 计算公式 | 说明 |
|------|----------|------|
| 买单 | `best_bid - spread` | 在买一价下方挂单 |
| 卖单 | `best_ask + spread` | 在卖一价上方挂单 |

当 `spread = 0` 时，订单直接挂在买一/卖一价（最优价格）。

---

## vars 和 conditional_vars

### vars 列表语义

```yaml
vars:
  - name: delta_position_usd
    value: 'current_position_usd - position_usd'
  - name: position_ratio
    value: 'delta_position_usd / max_position_usd'  # 可引用前面的变量
```

**特性**：
- 每次 tick 重新计算
- 按列表顺序计算，后面可引用前面
- 支持表达式

### conditional_vars 条件触发

```yaml
conditional_vars:
  center_price:
    value: mid_price
    on: 'rsi[-1] < 30 or rsi[-1] > 70 or duration > 7 * 24 * 3600'
    default: null
```

**特性**：
- 仅当 `on` 条件满足时更新 `value`
- 条件不满足时保持上次值
- `default` 为首次值（条件从未满足时）
- 支持 `duration` 变量（距上次更新的秒数）

### 计算顺序

```
1. 收集 requires 中 Indicator 的变量
2. 注入 strategies namespace（来自多个 Strategy 的聚合输出）
3. 计算 vars（按列表顺序）
4. 计算 conditional_vars（按定义顺序）
5. 计算 order 内部的 vars 和 conditional_vars
```

---

## MarketExecutor

市价单执行器，立即以市场价格成交。

### 配置

```yaml
class_name: market

requires:
  - ticker

condition: 'abs(delta_usd) > 10'

order_levels: 1
order:
  order_usd: 'abs(delta_usd)'
```

---

## LimitExecutor

限价单执行器，支持多层挂单。

### 配置

```yaml
class_name: limit

requires:
  - ticker

vars:
  - name: q
    value: 'clip((current_position_usd - position_usd) / max_position_usd, -1, 1)'

entry_order_levels: 3
entry_order:
  spread: '0.0002 * mid_price * abs(level)'
  order_usd: '100 - q * 50'
  timeout: 7d
  refresh_tolerance: 1.0

exit_order_levels: 3
exit_order:
  spread: '0.0002 * mid_price * abs(level)'
  order_usd: '100 + q * 50'
  timeout: 7d
  refresh_tolerance: 1.0
```

### Duration 格式

`timeout` 等时间参数支持人类可读格式：

| 格式 | 秒数 |
|------|------|
| `30s` | 30 |
| `5m` | 300 |
| `1h` | 3600 |
| `7d` | 604800 |
| `604800` | 604800（纯数字按秒解析） |

---

## MarketMakingExecutor

做市商执行器，使用 entry/exit 分离做市。

### 配置

```yaml
class_name: market_making

requires:
  - ticker

vars:
  - name: inventory_ratio
    value: 'current_position_usd / max_position_usd'
  - name: base_spread
    value: '0.0003 * mid_price'
  - name: skew
    value: 'inventory_ratio * base_spread * 0.5'

entry_orders:
  - spread: 'base_spread + skew'
    order_usd: '100 * (1 - inventory_ratio * 0.5)'
    refresh_tolerance: 0.5
    timeout: 30s

exit_orders:
  - spread: 'base_spread - skew'
    order_usd: '100 * (1 + inventory_ratio * 0.5)'
    refresh_tolerance: 0.5
    timeout: 30s
```

### 多层做市

```yaml
entry_order_levels: 3
entry_order:
  spread: 'base_spread * abs(level)'
  order_usd: '50 * abs(level)'
  refresh_tolerance: 0.8
  timeout: 1h

exit_order_levels: 3
exit_order:
  spread: 'base_spread * abs(level)'
  order_usd: '50 * abs(level)'
  refresh_tolerance: 0.8
  timeout: 1h
```

---

## PCAExecutor

Position Cost Averaging 执行器，金字塔式加仓/减仓。

### 配置

```yaml
class_name: pca

requires:
  - ticker
  - rsi

vars:
  - name: delta_position_usd
    value: 'current_position_usd - position_usd'

conditional_vars:
  center_price:
    value: mid_price
    on: 'rsi[-1] < 30 or rsi[-1] > 70 or duration > 7 * 24 * 3600'
    default: null

reset: 'abs(delta_position_usd) < 50'

entry_order_levels: 10
entry_order:
  vars:
    - name: direction
      value: '1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else null)'
    - name: spread_value
      value: '0.0002 * mid_price * (entry_level ** 2 + entry_level)'
  condition: 'direction is not null'
  spread: 'spread_value'
  price: 'center_price - direction * spread_value'
  order_amount: '0.01 * (entry_level ** 2 + entry_level) * direction'
  refresh_tolerance: 1.0
  timeout: 7d

exit_order_levels: 1
exit_order:
  vars:
    - name: direction
      value: '-1 if delta_position_usd > 0 else 1'
  condition: 'abs(delta_position_usd) > 50'
  price: '(1 - 0.01 * direction) * average_entry_price'
  order_amount: '-delta_position_amount'
  refresh_tolerance: 0.5
  timeout: 1h
```

### PCAExecutor 内置变量

| 变量 | 说明 |
|------|------|
| `entry_level` | 当前入场档位（0-based） |
| `exit_level` | 当前出场档位（0-based） |
| `total_entry_amount` | 累计入场数量 |
| `total_entry_usd` | 累计入场金额 |
| `average_entry_price` | 平均入场价格 |
| `delta_position_amount` | 当前仓位数量偏差 |

### 状态追踪

- **entry_level 追踪**：记住当前档位，避免重复吃单
- **订单状态追踪**：成交/取消后 level + 1
- **reset 条件**：满足时重置所有统计

---

## SmartExecutor

智能路由执行器，根据条件选择执行方式。

### 配置

```yaml
class_name: smart

requires:
  - ticker

vars:
  - name: spread_pct
    value: '(best_ask - best_bid) / mid_price'
  - name: is_liquid
    value: 'spread_pct < 0.0005'

routes:
  - condition: 'is_liquid and abs(delta_usd) < 5000'
    executor: market/basic
  - condition: 'spread_pct < 0.002'
    executor: limit/fixed_spread
  - condition: 'spread_pct >= 0.002'
    executor: market_making/symmetric
```

---

## 内置变量

以下变量始终可用，由系统自动注入：

| 变量名 | 类型 | 说明 |
|--------|------|------|
| `direction` | `int` | 交易方向：1（多）或 -1（空） |
| `buy` | `bool` | `direction == 1` |
| `sell` | `bool` | `direction == -1` |
| `speed` | `float` | 目标仓位的紧急程度 |
| `notional` | `float` | 目标仓位的 USD 价值（绝对值） |
| `mid_price` | `float` | 当前中间价 |
| `best_bid` | `float` | 买一价 |
| `best_ask` | `float` | 卖一价 |
| `current_position_usd` | `float` | 当前仓位价值 |
| `current_position_amount` | `float` | 当前仓位数量 |

### strategies namespace

来自 Strategy 的聚合输出：

```yaml
vars:
  - name: position_amount
    value: 'sum(strategies["position_amount"])'
  - name: position_usd
    value: 'sum(strategies["position_usd"])'
```

---

## 安全函数白名单

表达式仅支持以下函数：

- `len`, `abs`, `min`, `max`, `sum`, `avg`
- `round`, `clip`, `log`, `int`, `float`

---

## 最佳实践

### 选择执行器

| 场景 | 推荐执行器 |
|------|-----------|
| 快速调仓 | MarketExecutor |
| 做市/低滑点 | LimitExecutor |
| 双边做市 | MarketMakingExecutor |
| 金字塔加仓 | PCAExecutor |
| 复杂路由 | SmartExecutor |

### 网格交易技巧

使用 `conditional_vars` 缓存中心价格：

```yaml
conditional_vars:
  center_price:
    value: mid_price
    on: 'duration > 7 * 24 * 3600'  # 每 7 天更新
    default: mid_price

order_levels: 5
order:
  vars:
    - name: grid_price
      value: 'center_price + 0.0002 * center_price * level'
    - name: is_buy
      value: 'grid_price < mid_price'
  condition: 'is_buy or grid_price > mid_price'
  price: 'grid_price'
  order_amount: '(100 / grid_price) if is_buy else -(100 / grid_price)'
  timeout: 7d
```

---

## 相关文档

- [Feature 0005: Executor 动态条件](../features/0005-executor-dynamic-conditions.md)
- [Feature 0008: Strategy 数据驱动](../features/0008-strategy-data-driven.md)
- [Feature 0010: Executor vars 系统](../features/0010-executor-vars-system.md)
- [Example 002: Executor 配置详解](../examples/002-executor-configurations.md)
