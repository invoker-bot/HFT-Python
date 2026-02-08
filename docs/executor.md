# 执行器模块文档

## 概述

执行器（Executor）负责将策略的目标仓位转换为实际交易订单。

### 数据驱动设计

本项目采用**数据驱动**的执行架构：

1. **Indicator 统一架构**：所有数据源（DataSource）都是特殊的 Indicator，统一通过 `IndicatorGroup` 管理
2. **变量注入机制**：Indicator 通过 `get_vars()` 提供变量，Executor 通过 `requires` 声明依赖
3. **Scope 系统集成**：Executor 可选通过 `scope` 字段声明其订单执行所在的 `scope_class_id`（Feature 0012；不用于声明 Scope 节点）
4. **vars 变量系统**：支持变量计算和条件触发更新（详见 [vars 文档](vars.md)）
5. **统一 order 配置**：所有 Executor 使用相同的 order 配置格式

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
│  │  {direction, buy, sell, speed, notional, last_price, │   │
│  │   bid_price, ask_price, rsi, medal_edge, volume, ...} │   │
│  └─────────────────────────────────────────────────────┘   │
│           │                                                 │
│           ▼ strategies namespace                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  strategies["position_amount"] = [0.01]              │   │
│  │  strategies["speed"] = [0.1]                         │   │
│  └─────────────────────────────────────────────────────┘   │
│           │                                                 │
│           ▼ vars 变量计算                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Executor Decision                                   │   │
│  │  - vars: [{name, value, on?, initial_value?}, ...]  │   │
│  │  - orders / entry_orders / exit_orders               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 类层次

```
BaseExecutor (抽象基类)
└── DefaultExecutor                # 默认执行器
```

---

## 统一的 Order 配置机制

所有 Executor 使用统一的 order 配置格式。

### Order 字段

```yaml
order:
  price: ...              # 绝对价格（可选）
  spread: ...             # 价差（当 price 未定义时：买单 bid - spread，卖单 ask + spread）
  order_usd: ...          # 订单金额
  order_amount: ...       # 订单数量（正=买，负=卖；定义后忽略 order_usd）
  refresh_tolerance: ...  # 刷新容忍度（比例）
  refresh_tolerance_usd: ... # 刷新容忍度（绝对值）
  timeout: ...            # 订单超时
  condition: ...          # 挂单条件
  vars: ...               # 订单级变量（支持条件变量通过 on 字段）
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

## vars 变量系统

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
- 支持条件变量（通过 `on` 和 `initial_value` 字段）

**详细说明**：vars 支持三种格式（标准格式、dict 简化格式、list[str] 简化格式），详见 [vars 文档](vars.md)。

### 计算顺序

```
1. 收集 requires 中 Indicator 的变量
2. 注入 strategies namespace（来自 Strategy 输出；Issue 0013: 单策略标量化）
3. 计算 vars（按列表顺序，包括条件变量）
4. 计算 order 内部的 vars
```

---

## DefaultExecutor

默认执行器（市价单），立即以市场价格成交。

### 配置

```yaml
class_name: default

requires:
  - ticker

condition: 'abs(delta_usd) > 10'

order_levels: 1
order:
  order_usd: 'abs(delta_usd)'
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
| `bid_price` | `float` | 买一价 |
| `ask_price` | `float` | 卖一价 |
| `last_price` | `float` | 最新成交价 |
| `current_position_usd` | `float` | 当前仓位价值 |
| `current_position_amount` | `float` | 当前仓位数量 |

### strategies namespace

来自 Strategy 的聚合输出：

```yaml
vars:
  - name: position_amount
    value: 'strategies["position_amount"]'
  - name: position_usd
    value: 'strategies["position_usd"]'
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
| 调仓 | DefaultExecutor |

### 网格交易技巧

使用条件变量缓存中心价格：

```yaml
vars:
  - name: center_price
    value: mid_price
    on: 'duration > 7 * 24 * 3600'  # 每 7 天更新
    initial_value: mid_price

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
