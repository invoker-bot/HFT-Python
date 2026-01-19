# Scope 系统

## 概述

Scope 系统是 HFT-Python 的核心数据驱动机制，提供分层的变量作用域管理。

**核心特性**：
- **分层结构**：支持多层级的变量作用域（global → exchange → trading_pair 等）
- **变量继承**：子 Scope 自动继承父 Scope 的变量（使用 ChainMap）
- **灵活配置**：用户可以自由定义 Scope 类型标识符
- **多根节点**：支持多个根节点，形成森林结构
- **Scope 复用**：中间节点可以被多个父节点共享

## 核心概念

### Scope 的三个标识符

1. **Scope Class Name**（类名）
   - 在代码中定义的 Python 类名
   - 例如：`GlobalScope`, `ExchangeScope`, `TradingPairScope`
   - 由开发者在代码中实现

2. **Scope Class ID**（类型标识符）
   - 用户在app配置文件中自由定义的标识符
   - 例如：`"global"`, `"my_scope"`, `"层级1"`
   - **可以任意命名，不是硬编码的**

3. **Scope Instance ID**（实例标识符）
   - 具体的 Scope 实例标识
   - 例如：`"okx/main"`, `"BTC/USDT"`, `"global"`
   - 用于区分同一类型的不同实例

### 缓存机制

Scope 实例通过 `scope_path` 作为缓存 key，`scope_path` 包含从根到当前节点的完整路径：

```python
# scope_path 格式："scope_class_id:scope_instance_id/parent_path"
# 例如：
cache_key = "global:global"  # GlobalScope
cache_key = "exchange:okx/main/global:global"  # ExchangeScope (parent 是 GlobalScope)
cache_key = "trading_pair:okx/main:BTC/USDT/exchange:okx/main/global:global"  # TradingPairScope
```

**重要**：只有完整的 `scope_path` 相同（包括 parent 链），才会复用同一个 Scope 实例。这意味着：
- 相同的 `(scope_class_id, scope_instance_id)` 但不同的 parent，会创建不同的 Scope 实例
- 这确保了 ChainMap 变量继承链的正确性

## 标准 Scope 类

| Scope Class Name | 说明 | 典型 Class ID | Instance ID 示例 |
|------------------|------|---------------|------------------|
| `GlobalScope` | 全局作用域 | `global` | `"global"` |
| `ExchangeClassScope` | 交易所类型 | `exchange_class` | `"okx"`, `"binance"` |
| `ExchangeScope` | 交易所实例 | `exchange` | `"okx/main"`, `"binance/spot"` |
| `TradingPairClassScope` | 交易对类型 | `trading_pair_class` | `"BTC/USDT"` |
| `TradingPairScope` | 交易对实例 | `trading_pair` | `"okx/main:BTC/USDT"` |

**注意**：表格中的"典型 Class ID"只是建议，用户可以使用任何名称。

## 基本用法

### 单根节点配置

最简单的配置，使用单一的 GlobalScope：

```yaml
# Strategy 配置
class_name: static_positions

# Scope 链路定义
links:
  - ["global", "exchange", "trading_pair"]

# Scope 变量配置
scopes:
  global:
    class_name: GlobalScope
    instance_id: "global"
    vars:
      - name: max_position_usd
        value: 10000
      - name: default_speed
        value: 0.5

  exchange:
    class_name: ExchangeScope
    vars:
      - name: total_equity
        value: equation_usd  # 来自 Indicator 注入

  trading_pair:
    class_name: TradingPairScope
    vars:
      - name: target_position
        value: max_position_usd * 0.5  # 使用父 Scope 变量
      - name: speed
        value: default_speed

targets:
  - symbol: BTC/USDT
    position_usd: target_position  # 使用 Scope 变量
    speed: speed
```

### 多根节点配置

支持多个 GlobalScope，用于不同的策略或场景：

```yaml
scopes:
  global_arbitrage:
    class_name: GlobalScope
    instance_id: "arbitrage"  # 必须不同
    vars:
      - name: strategy_type
        value: "arbitrage"
      - name: max_position_usd
        value: 10000

  global_market_making:
    class_name: GlobalScope
    instance_id: "market_making"  # 必须不同
    vars:
      - name: strategy_type
        value: "market_making"
      - name: max_position_usd
        value: 5000

links:
  - ["global_arbitrage", "exchange", "trading_pair"]
  - ["global_market_making", "exchange", "trading_pair"]
```

**关键点**：
- 每个 GlobalScope 必须有不同的 `instance_id`
- 不同的 GlobalScope 形成独立的 Scope 树
- 中间节点（如 `exchange`, `trading_pair`）可以被多个根节点共享

## 变量继承

Scope 使用 Python 的 `ChainMap` 实现变量继承：

```python
# 父 Scope
parent_scope.set_var("parent_var", "parent_value")

# 子 Scope
child_scope.set_var("child_var", "child_value")

# 子 Scope 可以访问父 Scope 的变量
child_scope.get_var("parent_var")  # "parent_value"
child_scope.get_var("child_var")   # "child_value"

# 子 Scope 的变量会覆盖父 Scope 的同名变量
parent_scope.set_var("shared_var", "parent")
child_scope.set_var("shared_var", "child")
child_scope.get_var("shared_var")  # "child"
```

## 计算顺序与 parent/children 访问

### 计算顺序

Scope 系统的变量计算遵循以下顺序：

1. **Indicator 注入**：首先注入所有 Indicator 提供的变量（如 `mid_price`, `rsi`, `amount` 等）
2. **vars 计算**：然后按照 Scope 树的层级顺序计算 vars（包括条件变量）

### parent 和 children 访问

Scope 系统支持通过 `parent` 和 `children` 访问父节点和子节点：

```yaml
# ✅ 自下而上聚合（parent 访问 children 的 indicator 变量）
scopes:
  exchange:
    # indicator 注入的变量（如 amount）

  global:
    vars:
      - name: total_amount
        value: sum([scope["amount"] for scope in children.values()])
```

**关键点**：
- `parent` 可以访问 `children` 的 **indicator 注入的变量**
- 因为 indicator 注入发生在 vars 计算之前

```yaml
# ✅ 自上而下分配（child 访问 parent 的 vars）
scopes:
  global:
    vars:
      - name: total_budget
        value: 10000

  exchange:
    vars:
      - name: allocation
        value: parent["total_budget"] * 0.5
```

**关键点**：
- `child` 可以访问 `parent` 的 **vars 计算结果**
- 因为 parent 的 vars 在 child 之前计算

## Scope 复用

中间节点可以被多个父节点引用，实现 Scope 复用：

```yaml
# 同一个 exchange 可以属于多个策略的 Scope 树
links:
  - ["global_1", "exchange", "trading_pair"]  # 策略 1
  - ["global_2", "exchange", "trading_pair"]  # 策略 2
```

**缓存行为**：
- 相同的 `(scope_class_id, scope_instance_id)` 会返回同一个实例，**即使 parent 不同**
- 这意味着同一个 Scope 可以被多个 parent 共享
- 例如：`global_1 -> exchange:okx/main` 和 `global_2 -> exchange:okx/main` 会共享同一个 `exchange:okx/main` 实例

## Links 计算规则

### Links 的定义

Links 定义了 Scope 树的拓扑结构，指定了从根节点到目标节点的**层级路径**：

```yaml
links:
  - ["global", "exchange_class", "exchange", "trading_pair"]
  - ["global", "exchange_class", "trading_pair_class", "trading_pair"]
```

**重要**：每个 link 不是"一条路径"，而是定义了**层级关系**。在展开时，会遍历每一层的所有 children。

### 沿 Link 展开计算

**展开规则**（树的完整遍历）：

Link `["global", "exchange", "trading_pair"]` 的展开过程：

1. **第一层**：创建/获取 `global` scope（1个实例）
2. **第二层**：遍历 `global` 的所有 `exchange` children（如 `okx/main`, `binance/spot`）
3. **第三层**：对每个 `exchange`，遍历其所有 `trading_pair` children（如 `BTC/USDT`, `ETH/USDT`）

**结果**：形成完整的树结构，而非单一路径。

**计算流程**：

1. **Indicator 注入**
   - 在每个 Scope 层级注入对应的 Indicator 变量
   - 例如：`trading_pair` 层级注入 `mid_price`, `rsi` 等

2. **计算 vars**
   - 按照 link 的顺序，从根到叶依次计算每个 Scope 的 vars
   - 每个 Scope 可以访问：
     - 自己的 Indicator 注入变量
     - parent 的 vars（通过 ChainMap 继承）
     - children 的 Indicator 注入变量（通过 `children` 字典）

## 条件变量

Scope 支持条件变量，用于状态缓存和条件分支：

```yaml
scopes:
  trading_pair:
    class_name: TradingPairScope
    vars:
      - name: is_oversold
        value: rsi[-1] < 30

      # 条件变量：仅当条件满足时更新
      - name: entry_price
        value: mid_price
        on: is_oversold
        initial_value: null

      - name: entry_time
        value: current_timestamp
        on: is_oversold
        initial_value: null

      - name: time_since_entry
        value: current_timestamp - entry_time if entry_time else 0
```

**行为**：
- `on` 为 `True` 时，更新变量值
- `on` 为 `False` 时，保持上次的值（缓存）
- `on` 未定义时，默认为 `True`（每次都更新）
- `initial_value` 定义初始值（条件从未满足时使用）
- 可以实现状态记忆和条件分支逻辑

## 自定义 Scope 类型

Strategy 可以定义自己的 Scope 类型：

```python
from hft.core.scope import BaseScope

class TradingPairClassGroupScope(BaseScope):
    """
    交易对分组 Scope

    用于 MarketNeutralPositions 策略的分组聚合
    """

    def __init__(self, scope_class_id: str, scope_instance_id: str,
                 parent: BaseScope):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            parent=parent
        )
        self.set_var("group_id", scope_instance_id)
```

然后在 Strategy 中注册：

```python
class MarketNeutralPositionsStrategy(BaseStrategy):
    def _register_custom_scopes(self):
        """注册自定义 Scope 类型"""
        self.scope_manager.register_scope_class(
            "TradingPairClassGroupScope",
            TradingPairClassGroupScope
        )
```

配置中使用：

```yaml
scopes:
  trading_pair_group:
    class_name: TradingPairClassGroupScope
    vars:
      - name: fair_price_min
        value: min([scope["fair_price"] for scope in children.values()])
```

## ScopeManager

`ScopeManager` 负责 Scope 实例的创建、缓存和管理：

```python
from hft.core.scope import ScopeManager

# 创建 ScopeManager
manager = ScopeManager()

# 获取或创建 Scope 实例
scope = manager.get_or_create(
    scope_class_name="GlobalScope",      # 类名
    scope_class_id="global",             # 类型标识符
    scope_instance_id="global",          # 实例标识符
    parent=None                          # 父 Scope
)

# 注册自定义 Scope 类型
manager.register_scope_class("CustomScope", CustomScope)
```

## 与 Indicator 集成

Indicator 可以将计算结果注入到指定层级的 Scope：

```python
class TickerDataSource(BaseIndicator):
    """注入到 trading_pair_class scope"""

    def calculate_vars(self, direction: Optional[str] = None) -> Dict[str, Any]:
        latest = self._data.get_latest()
        return {
            "mid_price": latest.mid_price,
            "best_bid": latest.best_bid,
            "best_ask": latest.best_ask,
        }
```

配置中指定注入层级：

```yaml
indicators:
  ticker:
    class_name: TickerDataSource
    inject_to: trading_pair_class  # 注入到 trading_pair_class scope
```

## 与 Executor 集成

Executor 可以访问 Strategy 定义的 Scope 变量：

```yaml
# Executor 配置
class_name: limit

requires:
  - ticker

vars:
  # 访问 Scope 变量
  - name: target_pos
    value: target_position  # 来自 trading_pair scope
  - name: max_pos
    value: max_position_usd  # 来自 global scope

  # 计算本地变量
  - name: position_ratio
    value: current_position_usd / max_pos if max_pos > 0 else 0

orders:
  - spread: '0.0002 * mid_price'
    order_usd: 'abs(delta_usd)'
    condition: 'position_ratio < 0.8'  # 使用本地变量
```

### vars 格式说明

Scope 的 `vars` 支持三种格式：

**格式 1：标准格式（推荐）**
```yaml
scopes:
  global:
    vars:
      - name: var_name
        value: expression
        on: condition  # 可选，条件表达式
        initial_value: value  # 可选，初始值
```

**格式 2：dict 简化格式（计算顺序不确定）**
```yaml
scopes:
  global:
    vars:
      var_name: expression
      another_var: another_expression
```

**格式 3：list[str] 简化格式**
```yaml
scopes:
  global:
    vars:
      - var_name=expression
      - another_var=another_expression
```

**注意**：
- 格式 1 支持完整功能（条件变量、初始值）
- 格式 2 和 3 不支持条件变量，且格式 2 的计算顺序不确定
- 推荐使用格式 1 以获得最佳可读性和功能支持

## 完整的计算流程

详细的执行流程说明请参考：[Scope 系统执行流程详解](./scope-execution-flow.md)

**简要概述**：

### 在 AppCore 中的初始化

1. **创建 ScopeManager**
   ```python
   self.scope_manager = ScopeManager()
   ```

2. **注册自定义 Scope 类**
   - Strategy 通过 `_register_custom_scopes()` 注册自定义 Scope 类
   - 例如：`TradingPairClassGroupScope`

### 在 Strategy 中的展开计算

**每次 tick 的计算流程**：

1. **构建 Scope 树** (`_build_scope_trees()`)
   - 遍历每个 link
   - 沿着 link 创建/获取 Scope 实例
   - 建立 parent-child 关系

2. **Indicator 注入**
   - 在对应层级注入 Indicator 变量
   - 例如：`trading_pair` 层级注入 `mid_price`

3. **计算 vars**
   - 按 link 顺序，从根到叶计算每个 Scope 的 vars

4. **输出 targets**
   - 调用 `get_output()` 获取目标仓位
   - 返回 `{(exchange_path, symbol): {...}}`

### 在 Executor 中的使用

Executor 通过 `strategies` namespace 接收 Strategy 的输出，并可以访问 Scope 变量。

**计算顺序**：
1. 收集 Indicator 变量
2. 注入 `strategies` namespace
3. 计算 Executor 的 vars
4. 执行订单逻辑

## 最佳实践

### 1. 命名约定

- **Scope Class ID**：使用小写下划线命名（如 `global`, `exchange`, `trading_pair`）
- **Instance ID**：使用有意义的标识符（如 `"okx/main"`, `"BTC/USDT"`）
- **变量名**：使用小写下划线命名（如 `max_position_usd`, `target_position`）

### 2. 变量组织

- **全局配置**：放在 `global` scope（如 `max_position_usd`, `weights`）
- **交易所级别**：放在 `exchange` scope（如 `equation_usd`, `amount`）
- **交易对级别**：放在 `trading_pair` scope（如 `target_position`, `speed`）

### 3. 使用条件变量缓存状态

```yaml
# ✅ 正确：使用条件变量缓存状态
scopes:
  trading_pair:
    vars:
      - name: entry_price
        value: mid_price
        on: rsi[-1] < 30  # 仅在 RSI < 30 时更新
        initial_value: null
```

## 常见问题

### Q1: 为什么需要 Scope 系统？

**A**: Scope 系统解决了以下问题：

1. **变量来源不明确**：明确定义变量的作用域层级
2. **无法表达层级关系**：支持多层级的变量继承和聚合
3. **复杂策略逻辑**：支持跨层级的计算（如分组聚合）

### Q2: 可以有多个 GlobalScope 吗？

**A**: 可以！Scope 系统支持多个根节点，形成森林结构。只要 `instance_id` 不同，就不会冲突。

### Q3: Scope 复用是什么意思？

**A**: 中间节点可以被多个父节点引用。例如，同一个 `exchange` 节点可以属于多个策略的 Scope 树。

### Q4: 如何调试 Scope 变量？

**A**: 可以使用 `scope.vars` 查看所有变量（包括继承的）：

```python
# 查看所有变量
print(dict(scope.vars))

# 查看当前 Scope 的变量（不包括继承的）
print(scope._vars)
```

## 相关文档

- [Feature 0012: Scope 系统](../features/0012-scope-system.md) - 详细设计文档
- [Example 001: 稳定币做市](../examples/001-stablecoin-market-making.md) - Scope 使用示例
- [Example 002: Executor 配置](../examples/002-executor-configurations.md) - Executor 中使用 Scope
- [Example 003: StaticPositions 策略](../examples/003-static-positions-strategy.md) - Strategy 中使用 Scope
- [docs/strategy.md](./strategy.md) - Strategy 文档
- [docs/executor.md](./executor.md) - Executor 文档
