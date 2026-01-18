# Feature 0010: Executor vars 系统

> **状态**：全部通过

## 概述

统一 Executor 的变量计算和订单展开机制，支持：
1. `vars` 列表语义：顺序计算，后面可引用前面
2. 条件变量：通过 `on` 字段实现条件触发更新
3. 统一的 order 展开机制

## 动机

当前各 Executor 的参数计算方式不统一，且缺乏状态保持能力。新设计提供：
- 更灵活的变量计算
- 条件触发的状态变量
- 统一的订单配置格式

## 设计

### 1. vars 列表语义

```yaml
vars:
  - name: delta_position_usd
    value: current_position_usd - position_usd
  - name: position_ratio
    value: delta_position_usd / max_position_usd  # 可引用前面的变量
```

**特性**：
- 每次 tick 重新计算
- 按列表顺序计算，后面可引用前面
- 支持表达式

### 2. 条件变量

```yaml
vars:
  - name: center_price
    value: mid_price
    on: rsi[-1] < 30 or rsi[-1] > 70 or duration > 7 * 24 * 3600
    initial_value: null
```

**特性**：
- 仅当 `on` 条件满足时更新 `value`
- 条件不满足时保持上次值
- `initial_value` 为首次值（条件从未满足时）
- 支持 `duration` 变量（距上次更新的秒数）

### 3. strategies namespace

Executor 通过 `strategies` namespace 接收多个 Strategy 的聚合输出：

```yaml
vars:
  - name: position_amount
    value: sum(strategies["position_amount"])
  - name: total_speed
    value: max(strategies["speed"])
```

**聚合规则**：
- 多个 Strategy 输出同一字段时，聚合为列表
- 通过 `sum()`, `max()`, `min()`, `avg()` 等函数处理

### 4. 计算顺序

```
1. 内置变量（direction/buy/sell/speed/notional 等）
2. 注入 strategies namespace（来自多个 Strategy 的聚合输出）
3. 收集 requires 中 Indicator 的变量
4. 计算 vars（按列表顺序，包括条件变量和 duration）
5. （如适用）计算 order 内部的 vars
```

### 5. 统一的 Order 展开机制

所有 Executor 使用统一的 order 配置格式：

| 字段 | 适用 Executor | 说明 |
|------|---------------|------|
| `order` / `orders` | LimitExecutor, MarketMakingExecutor | 通用订单 |
| `entry_order` / `entry_orders` | 所有支持 entry/exit 分离的 Executor | 入场订单（趋近目标） |
| `exit_order` / `exit_orders` | 所有支持 entry/exit 分离的 Executor | 出场订单（偏离目标） |

> **注意**：GridExecutor 已废弃，使用 LimitExecutor + `order_levels` 替代。

#### Order 通用字段

```yaml
order:
  vars:           # 订单级别变量（可选）
    - name: spread_value
      value: 0.0002 * mid_price * level
  # vars 支持条件变量（通过 on 字段）
  condition: ...  # 挂单条件
  price: ...      # 订单价格（表达式，可选）
  spread: ...     # 价差（当 price 未定义时使用）
  order_amount: ...     # 订单数量（正=买，负=卖；与 order_usd 二选一）
  order_usd: ...        # 订单金额（表达式，与 order_amount 二选一）
  timeout: ...    # 订单超时
  refresh_tolerance: ...  # 刷新容忍度（比例；相对于当前 mid_price 的原始 spread 的比例阈值）
  # refresh_tolerance_usd: ...  # 预留字段（当前未生效）
```

### 6. PCAExecutor 特殊行为

```yaml
class_name: pca

reset: abs(delta_position_usd) < 50  # 重置条件

entry_order_levels: 10
entry_order:
  vars:
    - name: direction
      value: 1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else null)
  condition: direction is not null
  order_amount: 0.01 * (entry_level ** 2 + entry_level) * direction
  price: center_price - direction * spread
  timeout: 604800

exit_order_levels: 1
exit_order:
  condition: abs(delta_position_usd) > 50
  order_amount: -delta_position_amount
  price: (1 - 0.01 * direction) * average_entry_price
  timeout: 3600
```

#### PCAExecutor 内置变量

| 变量 | 说明 |
|------|------|
| `entry_level` | 当前入场档位（0-based） |
| `exit_level` | 当前出场档位（0-based） |
| `total_entry_amount` | 累计入场数量 |
| `total_entry_usd` | 累计入场金额 |
| `average_entry_price` | 平均入场价格 |
| `delta_position_amount` | 当前仓位数量偏差 |

#### PCAExecutor 状态追踪

- **entry_level 追踪**：记住当前档位，避免重复吃单
- **订单状态追踪**：成交/取消后 level + 1
- **reset 条件**：满足时重置所有统计

### 7. 与现有配置的兼容

新设计向后兼容现有配置：

```yaml
# 旧格式（仍支持）
vars:
  q: 'clip(...)'

# 新格式（推荐）
vars:
  - name: q
    value: 'clip(...)'
```

## 任务列表

### Phase 1: vars 列表语义（P1）

- [x] BaseExecutorConfig 支持 vars 列表格式（已通过）
- [x] 实现顺序计算逻辑（已通过）
- [x] 向后兼容 dict 格式（已通过）

### Phase 2: 条件变量（P1）

- [x] 新增 ConditionalVar 数据结构（已通过）
- [x] 实现条件触发更新逻辑（已通过）
- [x] 支持 duration 变量（已通过）
- [x] 状态持久化（跨 tick 保持）（已通过）

### Phase 3: strategies namespace（P0）

- [x] 实现 Strategy 输出聚合到 strategies namespace（已通过）
  - 由 Feature 0008 Phase 2 实现
- [x] Executor 支持 strategies["field"] 访问（已通过）
  - 由 Feature 0008 Phase 2 实现
- [x] 支持 sum/max/min/avg 聚合函数（已通过）
  - sum/max/min/round/abs/len 已内置
  - 新增 avg() 和 clip() 函数

### Phase 4: Order 统一展开（P1）

- [x] 统一 Order 配置格式（已通过：引入 OrderDefinition，并在 PCAExecutor 中落地使用）
- [x] Order 内部 vars 支持（已通过：PCAExecutor 求值时支持并持久化 order 级状态）
- [x] 支持 order_amount/order_usd 二选一（已通过）
- [x] 支持 price/spread 二选一（已通过）

### Phase 5: PCAExecutor（P2）

- [x] 新增 PCAExecutorConfig（已通过）
- [x] 新增 PCAExecutor 类（已通过）
- [x] 实现 entry/exit 订单逻辑（已通过）
- [x] 实现 level 追踪和统计变量（已通过）
- [x] 实现 reset 条件（已通过）

### Phase 6: 测试和文档（P2）

- [x] 添加单元测试（已通过）
- [x] 更新 docs/executor.md（已通过）
- [x] 更新 examples/001-stablecoin-market-making.md（已通过）
- [x] 创建 examples/002-executor-configurations.md（已通过）

## 与现有 Feature 的关系

| Feature | 关系 |
|---------|------|
| Feature 0005 | 扩展 requires 和表达式求值机制 |
| Feature 0008 | 共享 vars 机制；接收 Strategy 通用字典输出 |
| Feature 0009 | ~~GridExecutor 可使用统一 order 格式~~ GridExecutor 已废弃，使用 LimitExecutor + order_levels 替代 |

## 示例

参考：
- `examples/001-stablecoin-market-making.md` 方案三的 PCAExecutor 配置
- `examples/002-executor-configurations.md` 各 Executor 的详细配置示例
