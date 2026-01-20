# vars 变量系统文档

## 概述

vars 是一个统一的变量定义和计算系统，用于在 Scope 和 Executor 中定义动态计算的变量。

### 使用场景

- **Scope vars（Scope 系统）**：在 `conf/app/*.yaml` 的 `scopes.*.vars` 中定义，用于多层级的变量计算和继承（Strategy 通过 `links` 引用这些 scope）
- **Executor 中的 vars**：在顶级 `vars` 字段中定义，用于 trading_pair instance level 的变量计算

---

## vars 格式

vars 支持三种格式，可以混合使用：

### 格式 1：标准格式（推荐）

**完整功能支持**，包括条件变量和初始值：

```yaml
vars:
  - name: var_name
    value: expression
    on: condition  # 可选，条件表达式
    initial_value: value  # 可选，初始值
```

**字段说明**：
- `name`：变量名（必填）
- `value`：表达式（必填）
- `on`：条件表达式（可选，默认为 True，即每次都更新）
- `initial_value`：初始值（可选，条件从未满足时使用）

**示例**：

```yaml
vars:
  - name: delta_usd
    value: target_usd - current_usd

  - name: center_price
    value: mid_price
    on: rsi[-1] < 30 or rsi[-1] > 70
    initial_value: 100.0
```

### 格式 2：dict 简化格式

**注意**：计算顺序不确定，不支持条件变量。

```yaml
vars:
  var_name: expression
  another_var: another_expression
```

**示例**：

```yaml
vars:
  max_position: 10000
  speed: 0.5
```

### 格式 3：list[str] 简化格式

使用 `name=value` 格式：

```yaml
vars:
  - var_name=expression
  - another_var=another_expression
```

**示例**：

```yaml
vars:
  - risk_ratio=0.6
  - delta_usd=target_usd - current_usd
```

### 混合格式

可以在同一个 list 中混合使用标准格式和简化格式：

```yaml
vars:
  - risk_ratio=0.6  # 简化格式
  - name: direction  # 标准格式
    value: 1 if rsi < 30 else -1
    on: rsi < 30 or rsi > 70
    initial_value: 0
  - delta_usd=target_usd - current_usd  # 简化格式
```

---

## 变量类型

### 普通变量

每次 tick 都重新计算：

```yaml
vars:
  - name: delta_usd
    value: target_usd - current_usd
  - name: ratio
    value: delta_usd / max_usd  # 可以引用前面定义的变量
```

### 条件变量

仅当条件满足时更新值，否则保持上次值：

```yaml
vars:
  - name: center_price
    value: mid_price
    on: rsi[-1] < 30 or rsi[-1] > 70 or duration > 7 * 24 * 3600
    initial_value: mid_price

  - name: base_amount
    value: current_position_amount
    on: abs(delta_ratio) > 0.1
    initial_value: 0
```

**内置变量**：
- `duration`：距上次更新的秒数（仅在条件变量中可用）

---

## 计算顺序

vars 按照列表顺序依次计算，后面的变量可以引用前面的变量：

```yaml
vars:
  - name: a
    value: 100
  - name: b
    value: a * 2  # 引用前面的 a
  - name: c
    value: a + b  # 引用前面的 a 和 b
```

**注意**：格式 2（dict）的计算顺序不确定，因此不推荐在需要引用关系的场景中使用。

---

## 使用场景详解

### Scope vars（Scope 系统）

Scope vars 声明在 App 配置里（`scopes` 字段只允许出现在 `conf/app/*.yaml`）：

```yaml
# conf/app/<app>.yaml（片段）
scopes:
  g:
    class: GlobalScope
    vars:
      - max_position=10000
      - speed=0.5

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope
    vars:
      - exchange_fee=0.001

  trading_pair:
    class: TradingPairScope
    vars:
      - name: center_price
        value: mid_price
        on: rsi[-1] < 30 or rsi[-1] > 70
        initial_value: mid_price
```

Strategy 配置只引用 `links`，不允许出现 `scopes`：

```yaml
# conf/strategy/my_strategy.yaml（片段）
class_name: static_positions

requires:
  - ticker
  - rsi

links:
  - id: link_main
    value: [g, exchange_class, exchange, trading_pair]
```

**特点**：
- 多层级变量继承（子 Scope 可以访问父 Scope 的变量）
- 支持自上而下分配和自下而上聚合
- 与 Scope 系统深度集成
- Scope vars 支持 `post: true/false` 以控制三遍计算（见 [scope.md](scope.md)）

### Executor 中的 vars

在 Executor 配置中，vars 定义在顶级字段：

```yaml
# conf/executor/<executor>.yaml（片段）
class_name: market
scope: trading_pair  # 可选：使用 Scope 系统时，声明订单执行所在的 scope_class_id

vars:
  - delta_usd=target_usd - current_usd
  - ratio=delta_usd / max_usd
  - name: entry_price
    value: mid_price
    on: position == 0
    initial_value: null
```

**特点**：
- 执行在 trading_pair instance level
- 可以访问 Strategy 注入的 `strategies` 命名空间变量（规范统一使用 `strategies["field"]`，避免 `strategies.field`）

---

## 可用函数

在表达式中可使用的内置函数：

| 函数 | 说明 | 示例 |
|------|------|------|
| `len` | 列表长度 | `len(strategies["position_usd"])` |
| `abs` | 绝对值 | `abs(delta_usd)` |
| `min` | 最小值 | `min(a, b)` |
| `max` | 最大值 | `max(a, b)` |
| `sum` | 求和 | `sum(strategies["position_usd"])` |
| `round` | 四舍五入 | `round(price, 2)` |
| `avg` | 平均值 | `avg(strategies["speed"])` |
| `clip` | 限制范围 | `clip(value, 0, 1)` |

---

## 完整示例

### 示例 1：Scope vars（Scope 系统）

```yaml
# conf/app/<app>.yaml（片段：scopes 在 app 配置里声明）
scopes:
  g:
    class: GlobalScope
    vars:
      - max_position_ratio=0.8
      - base_speed=0.5

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope
    vars:
      - exchange_fee=0.001
      - max_position_usd=equation_usd * max_position_ratio

  trading_pair:
    class: TradingPairScope
    vars:
      - name: center_price
        value: mid_price
        on: rsi[-1] < 30 or rsi[-1] > 70 or duration > 7 * 24 * 3600
        initial_value: mid_price
      - price_ratio=mid_price / center_price
      - direction=1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else 0)
```

```yaml
# conf/strategy/dynamic_positions.yaml
class_name: static_positions

requires:
  - equation  # 账户权益数据源
  - ticker    # 价格数据源（提供 mid_price）
  - rsi       # RSI 指标

links:
  - id: link_main
    value: [g, exchange_class, exchange, trading_pair]

targets:
  - exchange: '*'
    symbol: BTC/USDT:USDT
    position_usd: max_position_usd * direction * price_ratio
    speed: base_speed
```

### 示例 2：Executor vars

```yaml
# conf/executor/<executor>.yaml
class_name: market
scope: trading_pair  # 可选：使用 Scope 系统时，声明订单执行所在的 scope_class_id

vars:
  # 聚合所有 Strategy 的目标仓位
  - target_usd=sum(strategies["position_usd"]) if "position_usd" in strategies else 0
  - delta_usd=target_usd - current_usd
  - ratio=abs(delta_usd) / max_usd if max_usd > 0 else 0

  # 条件变量：记录入场价格
  - name: entry_price
    value: mid_price
    on: position == 0
    initial_value: null

  # 计算盈亏
  - pnl=(mid_price - entry_price) * position if entry_price else 0
```

---

## 最佳实践

### 1. 优先使用标准格式

标准格式支持完整功能，可读性更好：

```yaml
# 推荐
vars:
  - name: center_price
    value: mid_price
    on: rsi[-1] < 30 or rsi[-1] > 70
    initial_value: mid_price

# 不推荐（无法使用条件变量）
vars:
  center_price: mid_price
```

### 2. 注意计算顺序

后面的变量可以引用前面的变量，但不能反向引用：

```yaml
# 正确
vars:
  - a=100
  - b=a * 2  # 可以引用前面的 a

# 错误
vars:
  - a=b * 2  # b 还未定义
  - b=100
```

### 3. 避免使用 dict 格式

dict 格式的计算顺序不确定，容易出错：

```yaml
# 不推荐（顺序不确定）
vars:
  b: a * 2
  a: 100

# 推荐（顺序明确）
vars:
  - a=100
  - b=a * 2
```

### 4. 合理使用条件变量

条件变量适用于需要"记忆"上次值的场景：

```yaml
vars:
  # 记录中心价格，只在 RSI 超买超卖时更新
  - name: center_price
    value: mid_price
    on: rsi[-1] < 30 or rsi[-1] > 70
    initial_value: mid_price

  # 记录入场价格，只在开仓时更新
  - name: entry_price
    value: mid_price
    on: position == 0
    initial_value: null
```

---

## 相关文档

- [Scope 系统文档](scope.md) - 了解 Scope 的多层级变量继承
- [Strategy 文档](strategy.md) - 了解 Strategy 中如何使用 Scope vars
- [Executor 文档](executor.md) - 了解 Executor 中如何使用 vars
