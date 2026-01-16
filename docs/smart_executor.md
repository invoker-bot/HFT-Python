# SmartExecutor 智能路由执行器

> 基于条件的智能执行器路由，支持在多种执行策略间动态切换

## 概述

SmartExecutor 是一个路由执行器，不直接下单，而是根据配置规则选择合适的子执行器来处理订单。它支持：

- **条件路由**：根据 speed、trades、edge、notional 等条件选择执行器
- **执行器切换**：切换时自动清理旧执行器的订单
- **Fail-safe 设计**：数据缺失或表达式错误时安全降级

## 配置示例

### 基础配置

```yaml
smart_executor:
  class: SmartExecutor
  default_executor: limit  # 默认执行器
  children:
    market: market/default    # 子执行器配置路径
    limit: limit/default
    as: avellaneda_stoikov/default
```

### 条件路由配置

```yaml
smart_executor:
  class: SmartExecutor
  default_executor: limit
  children:
    market: market/default
    limit: limit/default
    as: avellaneda_stoikov/default
  routes:
    # 高速模式：speed > 0.9 时使用市价单
    - condition: "speed > 0.9"
      executor: market
      priority: 1

    # 高流动性 + 有利差：使用 AS 策略
    - condition: "len(trades) > 50 and notional > 10000 and edge > 0.001"
      executor: as
      priority: 2

    # 低速不执行：取消现有订单
    - condition: "speed < 0.05"
      executor: null
      priority: 3

    # 默认规则
    - condition: null
      executor: limit
      priority: 999
```

## 路由优先级

路由决策按以下优先级进行（从高到低）：

1. **显式路由**：`exchange.config.executor_map[symbol]` 指定的执行器
2. **规则匹配**：`routes` 列表中按 `priority` 排序，匹配第一条满足的规则
3. **默认执行器**：`default_executor` 指定的执行器

## 条件表达式

### 可用变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `speed` | float | 执行紧急度，范围 [0, 1] |
| `trades` | list | 最近的成交记录列表 |
| `edge` | float | Taker 优势（比例），如 0.01 表示 1% |
| `notional` | float | 该方向的成交额（USD） |

### 可用函数

| 函数 | 说明 | 示例 |
|------|------|------|
| `len()` | 列表长度 | `len(trades) > 50` |
| `abs()` | 绝对值 | `abs(edge) > 0.01` |
| `min()` | 最小值 | `min(speed, 1.0)` |
| `max()` | 最大值 | `max(edge, 0)` |
| `sum()` | 求和 | - |
| `round()` | 四舍五入 | - |

### 表达式示例

```yaml
# 简单条件
condition: "speed > 0.9"

# 组合条件
condition: "speed > 0.8 and edge > 0"

# 复杂条件
condition: "len(trades) > 50 and notional > 10000 and edge > 0.001"

# 使用数学函数
condition: "abs(edge) > 0.01 and min(speed, 1.0) > 0.5"
```

## Edge 和 Notional 计算

### Edge（Taker 优势）

Edge 是量纲无关的相对值，表示 taker 相对于 VWAP 的优势：

```
买入方向：edge = (current_price - vwap_buy) / current_price - taker_fee
卖出方向：edge = (vwap_sell - current_price) / current_price - taker_fee
```

- 正值：taker 有优势（成交均价优于当前价格）
- 负值：taker 无优势
- 示例：`edge = 0.01` 表示 1% 的优势

### Notional（成交额）

Notional 是该方向的成交额：

- 买入方向（`delta_usd > 0`）：计算 buy side 的成交额
- 卖出方向（`delta_usd < 0`）：计算 sell side 的成交额

## 执行器切换

当路由决策改变时，SmartExecutor 会：

1. **先下新单**：调用新执行器的 `execute_delta()`
2. **成功后取消旧单**：调用旧执行器的 `cancel_orders_for_symbol()`
3. **更新追踪映射**：记录当前使用的执行器

### 边界情况处理

| 情况 | 处理方式 |
|------|----------|
| 新单失败 | 保持旧状态不变，不取消旧单 |
| 旧单取消失败 | 只记录警告，不影响新单状态 |
| 路由到 null | 取消现有订单，清理追踪记录 |

## 缓存机制

为避免重复计算，`trades`、`edge`、`notional` 的计算结果会被缓存：

- **缓存粒度**：每个 `(exchange, symbol)` 独立缓存
- **过期时间**：1 秒
- **刷新时机**：缓存过期后首次访问时重新计算

## 配置验证

启动时会验证配置：

1. `default_executor` 是否存在于 children 中
2. `routes` 中引用的执行器是否存在
3. 条件表达式语法是否正确
4. 条件表达式中的变量名是否有效
5. `priority` 是否重复（警告）
6. 是否有默认回退规则（提示）

## 最佳实践

### 1. 设置合理的优先级

```yaml
routes:
  # 高优先级规则放前面（数字小）
  - condition: "speed > 0.95"
    executor: market
    priority: 1

  # 低优先级规则放后面（数字大）
  - condition: null
    executor: limit
    priority: 999
```

### 2. 使用 null 处理特殊情况

```yaml
# 低速时不执行，避免频繁小额交易
- condition: "speed < 0.05"
  executor: null
  priority: 10
```

### 3. 组合条件避免误触发

```yaml
# 仅在高流动性且有利差时使用激进策略
- condition: "len(trades) > 50 and notional > 10000 and edge > 0.001"
  executor: as
  priority: 5
```

### 4. 保留默认回退

```yaml
# 始终有一个默认规则兜底
- condition: null
  executor: limit
  priority: 999
```

## 监控与调试

SmartExecutor 会记录以下日志：

- **INFO**: 路由决策、执行器切换
- **WARNING**: 旧单取消失败、除零错误
- **ERROR**: 配置错误、未定义变量

### 统计信息

通过 `_routing_stats` 可以查看各规则的命中次数：

```python
# 查看路由统计
print(smart_executor._routing_stats)
# {'explicit': 10, 'route_matched:speed > 0.9': 25, 'route_default': 100, ...}
```

## 注意事项

1. **子执行器状态**：子执行器设置 `lazy_start=True` 和 `enabled=False`，不会自动 tick
2. **并发安全**：订单追踪使用 `asyncio.Lock` 保护
3. **表达式安全**：使用 `simpleeval` 限制可用函数，防止代码注入
4. **数据依赖**：`trades` 数据来自 `TradesDataSource`，需确保数据源已启动
