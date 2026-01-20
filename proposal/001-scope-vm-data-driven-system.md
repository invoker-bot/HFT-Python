# Proposal 001: Scope/VirtualMachine 数据驱动系统

## 1. 背景与动机

### 1.1 当前问题

现有策略实现存在以下问题：
- 变量作用域不清晰，难以在不同层级共享数据
- 计算顺序不统一，容易出现依赖问题
- 缺乏声明式配置能力，代码耦合度高
- 难以支持复杂的多层级计算（如跨交易所聚合）

### 1.2 设计目标

引入基于 `simpleeval.safe_eval` 的数据驱动求值体系，实现：
1. **统一的变量作用域管理**：通过 Scope 系统管理多层级变量
2. **声明式配置**：策略/执行器以配置方式定义计算逻辑
3. **灵活的计算链路**：支持不同策略定义不同的计算路径
4. **安全的表达式求值**：基于 simpleeval 的安全沙箱环境

---

## 2. 核心概念

### 2.1 VirtualMachine (VM)

**定义**：表达式求值引擎，负责：
- 管理 `simpleeval.safe_eval` 的执行环境
- 提供安全的表达式求值接口
- 支持自定义函数和操作符

**职责**：
```python
class VirtualMachine:
    def eval(self, expression: str, names: dict) -> Any:
        """
        安全求值表达式

        Args:
            expression: 表达式字符串
            names: 变量字典（上下文）

        Returns:
            求值结果
        """
```

### 2.2 ScopeManager

**定义**：Scope 实例管理器，负责：
- 缓存 Scope 实例，避免重复创建
- 注册自定义 Scope 类型
- 提供 Scope 查找和创建接口
- **不维护 parent/children 拓扑**：parent/children 由 `links` 构建 LinkTree 时决定

**职责**：
```python
class ScopeManager:
    def get_or_create(
        self,
        scope_class_name: str,
        scope_class_id: str,
        scope_instance_id: str,
        **kwargs
    ) -> BaseScope:
        """获取或创建 Scope 实例（带缓存；不记录 parent/children）"""
```

### 2.3 Scope 系统

**定义**：用 `ChainMap` 组合多层变量域，支持"上游默认值 + 下游覆盖"的自然遮蔽规则。

**层级结构**：
```
GlobalScope
  └─ ExchangeClassScope
      ├─ ExchangeScope
      │   └─ TradingPairScope
      └─ TradingPairClassScope
          └─ TradingPairScope
```

**关键特性**：
- **变量继承**：子 Scope 可访问父 Scope 的变量
- **变量覆盖**：子 Scope 可覆盖父 Scope 的同名变量
- **跨层级访问**：通过 `parent` 和 `children` 显式访问其他层级

---

## 3. 术语表

| 术语 | 说明 | 示例 |
|------|------|------|
| **scope_class_id** | App 配置中 `scopes:` 下的 key（Scope 类型标识） | `global`, `exchange_class` |
| **scope class** | Scope 的 Python 类 | `GlobalScope`, `ExchangeScope` |
| **scope_class_name** | Scope 类的名称（字符串） | `"GlobalScope"`, `"ExchangeScope"` |
| **scope_instance_id** | 运行时实例标识 | `okx/main`, `BTC/USDT` |
| **scope instance** | 运行时对象 | Scope 实例 |
| **scope path** | LinkTree 内部的节点路径（**不是** ScopeManager 的缓存 key） | `trading_pair:BTC/USDT/exchange:okx/main/global:global` |
| **parent** | 当前实例在链路上的上游实例 | 用于显式访问父 Scope |
| **children** | `{id: child_scope_instance}` 的字典 | 用于聚合/遍历子 Scope |

---

## 4. App 配置：Scope 图

### 4.1 配置格式

```yaml
# conf/app/<app>.yaml
scopes:
  global:  # 这是scope class id，用户可以任意设定的一个值
    class: GlobalScope
    vars:  # 用户可以在此设置任意的变量，仅在scope创建时添加，用于初始值，可被覆盖
      - max_position_usd=10000
      - risk_ratio=0.6
  exchange_class:
    class: ExchangeClassScope
  exchange:
    class: ExchangeScope
  trading_pair_class:
    class: TradingPairClassScope
  trading_pair:
    class: TradingPairScope
    vars:
      - position_usd=max_position_usd * risk_ratio
```

### 4.2 配置规则

1. **Scope 节点只能在 App 配置中声明**
   - Strategy 配置中只能引用（通过 `links`）
   - 避免配置分散，便于全局管理

2. **Scope 图允许 DAG 结构**
   - 同一个 scope class 可被多个 node id 复用
   - 同一个 node id 可被多个上游引用
   - 运行时 `parent` 由当前 `links` 路径决定

3. **vars 支持表达式**
   - 使用 `simpleeval.safe_eval` 求值
   - 可引用父 Scope 变量（通过 ChainMap）
   - 可引用 `parent` 和 `children`

---

## 5. Strategy 配置：links 和 vars

### 5.1 配置格式

```yaml
# conf/strategy/<strategy>/<name>.yaml
class_name: static_positions

# 依赖的 Indicator/DataSource
requires:
  - equation  # 注入 equation_usd 到 exchange scope
  - ticker    # 注入 mid_price 到 trading_pair_class scope

# 计算链路（显式声明）
links:
  - id: main
    value: [global, exchange_class, exchange, trading_pair]

# 全局过滤条件（可选）
condition: null

# 目标配置
targets:
  - exchange_id: "*"
    symbol: "BTC/USDT:USDT"
    condition: "mid_price > 0"
    vars:
      - position_usd=max_position_usd * risk_ratio
```

### 5.2 links 的作用

**问题**：不同策略可能需要不同的计算路径，如何避免"箭头逆转"？

**解决方案**：通过 `links` 显式声明计算链路。

**示例**：
```yaml
# 路径 1：global → exchange_class → exchange → trading_pair
links:
  - id: path1
    value: [global, exchange_class, exchange, trading_pair]

# 路径 2：global → exchange_class → trading_pair_class → trading_pair
links:
  - id: path2
    value: [global, exchange_class, trading_pair_class, trading_pair]
```

---

## 6. 计算流程

### 6.1 整体流程（三遍计算）

对每条 `links[*]`，执行以下流程：

```
1. 构建 Scope 树
   ↓
2. 第一遍：Indicator/DataSource 注入
   ↓
3. 第二遍：计算 pre vars (post=false)
   ↓
4. 第三遍：计算 post vars (post=true)
   ↓
5. 策略专用计算（可选）
   ↓
6. 输出 targets 给 Executor
```

### 6.2 详细步骤

#### 步骤 1：构建 Scope 树

Scope 树（LinkTree）由 `links` 驱动构建；ScopeManager **只负责 scope 实例缓存**：

**实例发现**：
- 通过 `instance_ids_provider` 自定义函数
- 或使用注册的 `get_all_instance_ids()` 函数
- 支持 `symbol_filter` 和 `exchange_filter`

**递归构建**：
- 从根节点开始，逐层构建子节点
- 使用 `ScopeManager.get_or_create()` 获取或创建 scope 实例（带缓存）
- parent/children 关系由 LinkTree（由 links 构建）维护，ScopeManager **不记录** parent/children

**缓存机制**：
- ScopeManager 缓存 key：`(scope_class_id, scope_instance_id)`（不包含 parent）
- 要求：同一个 `(scope_class_id, scope_instance_id)` 的 parent 必须是确定且稳定的；如确需在不同 parent 下复用，必须保证 `scope_instance_id` 不冲突

**示例**：
```python
# links 构建 LinkTree，ScopeManager 仅提供 scope 实例缓存
# link_tree = build_link_tree(links=..., scope_manager=scope_manager, app_core=app_core, ...)
```

#### 步骤 2：Indicator/DataSource 注入

根据 `requires` 声明，将依赖产生的变量注入到对应 scope level。

**示例**：
```yaml
requires:
  - equation  # 注入 equation_usd 到 exchange scope
  - ticker    # 注入 mid_price 到 trading_pair_class scope
```

#### 步骤 3：计算 pre vars

沿 link 从前到后计算每一层的 `vars`（`post=false` 的变量）：
- 先计算 `children` 的 vars
- 再计算本层 vars（可引用 children 的结果）

**示例**：
```yaml
trading_pair_class:
  vars:
    - avg_price=sum([s["mid_price"] for s in children.values()]) / len(children)
```

#### 步骤 4：计算 post vars

计算 `post=true` 的变量（通常用于需要等待所有 pre vars 计算完成后的聚合）。

#### 步骤 5：策略专用计算（可选）

某些策略可能需要额外的计算逻辑（如 MarketNeutralPositions 的 ratio 平衡）。

#### 步骤 6：输出 targets

根据 `targets` 配置，展开并传递给 Executor。

---

## 7. Executor 对接

### 7.1 执行 Scope

**Executor 的执行 scope**：`trading_pair` level（最底层）

**原因**：
- Executor 需要对每个真实账户的每个交易对实例执行订单操作
- 订单管理、仓位跟踪都在 trading_pair instance level

### 7.2 订单字段（扩展）

```yaml
# 订单参数计算所在的 scope level
order_scope: trading_pair_class

# 多档订单配置
order_levels: ...
order: ...
orders: ...

# 进场/出场订单 scope
entry_order_scope: trading_pair_class
exit_order_scope: trading_pair_class
```

---

## 8. 条件变量（Conditional Variables）

### 8.1 定义

条件变量允许在满足特定条件时才更新变量值。

### 8.2 配置格式

```yaml
vars:
  - name: direction
    value: 1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else 0)
    on: rsi[-1] < 30 or rsi[-1] > 70
    initial_value: 0
```

**字段说明**：
- `value`：新值表达式
- `on`：触发条件（可选，默认 `True`）
- `initial_value`：初始值（可选，默认 `None`）

### 8.3 求值规则

1. 首次求值：使用 `initial_value`
2. 后续求值：
   - 若 `on` 为 `True`：计算 `value` 并更新
   - 若 `on` 为 `False`：保持上次的值

---

## 9. 安全性与限制

### 9.1 simpleeval 限制

- 禁止导入模块
- 禁止访问私有属性
- 禁止执行危险函数（如 `eval`, `exec`）
- 支持有限的 comprehension（受 `MAX_COMPREHENSION_LENGTH` 限制）

### 9.2 表达式复杂度

**建议**：
- 简单表达式：直接在配置中编写
- 复杂逻辑：通过 helper 函数或预计算注入

**示例**：
```yaml
# 简单表达式 ✓
vars:
  - position_usd=max_position_usd * risk_ratio

# 复杂表达式（不推荐）
vars:
  - result=sum([f(x) for x in children.values() if g(x) > threshold])

# 推荐：通过 helper 注入
vars:
  - result=calculate_complex_result()  # helper 函数
```

---

## 10. 示例：完整配置

### 10.1 App 配置

```yaml
# conf/app/my_app.yaml
class_name: app

exchanges:
  - okx/main
  - binance/spot

strategy: static_positions/btc_hold

scopes:
  global:
    class: GlobalScope
    vars:
      - max_position_usd=10000
      - risk_ratio=0.6

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope

  trading_pair:
    class: TradingPairScope
    vars:
      - position_usd=max_position_usd * risk_ratio
```

### 10.2 Strategy 配置

```yaml
# conf/strategy/static_positions/btc_hold.yaml
class_name: static_positions

requires:
  - equation
  - ticker

links:
  - id: main
    value: [global, exchange_class, exchange, trading_pair]

targets:
  - exchange_id: "*"
    symbol: "BTC/USDT:USDT"
    condition: "mid_price > 0"
    vars:
      - position_usd=max_position_usd * risk_ratio
```

---

## 11. FAQ

### Q1: 为什么需要 VirtualMachine？

**A**: 统一管理表达式求值环境，提供安全的沙箱执行。

### Q2: 为什么需要 ScopeManager？

**A**: 统一管理 Scope 实例的创建和缓存，避免重复创建和不一致。

### Q3: Scope 图可以有环吗？

**A**: 不可以。Scope 图必须是 DAG（有向无环图）。

### Q4: 如何调试表达式求值错误？

**A**:
1. 检查日志中的求值错误信息
2. 使用 `debug: true` 开启调试模式
3. 简化表达式，逐步排查

### Q4: 条件变量的初始值何时使用？

**A**: 只在首次求值时使用。后续求值会根据 `on` 条件决定是否更新。

### Q5: 如何在 Scope 之间传递数据？

**A**:
1. 通过 ChainMap 自动继承父 Scope 变量
2. 通过 `parent` 显式访问父 Scope
3. 通过 `children` 聚合子 Scope 数据

---

## 12. 相关文档

- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
- [Feature 0008: Strategy 数据驱动](../features/0008-strategy-data-driven.md)
- [docs/scope.md](../docs/scope.md)
- [docs/vars.md](../docs/vars.md)

---
