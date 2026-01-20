# Feature 0012: Scope 系统

> **状态**：全部通过（Phase 3 暂缓，当前 Scope 系统已可用于 Strategy）

## 概述

引入分层的 **Scope 系统**，作为整个数据驱动架构的核心机制，替代当前扁平的变量注入方式。

**核心特性**：
1. **分层 Scope 体系**：支持多层级的变量作用域（global → exchange_class → exchange → trading_pair 等）
2. **ChainMap 继承**：子 Scope 自动继承父 Scope 的变量
3. **动态链路配置**：通过 `links` 配置灵活定义 Scope 链路
4. **双向访问**：通过 `parent` 和 `children` 访问上下游 Scope
5. **惰性初始化**：Scope 实例按需创建并永久缓存
6. **自定义扩展**：Strategy 可以定义自己的 Scope 类型

## 动机

### 当前问题

**问题 1：变量注入机制不清晰**

当前的变量注入是扁平的，缺乏层级结构：

```python
# Executor 中的变量来源混乱
	context = {
	    "current_position_usd": ...,  # 来自 Exchange
	    "mid_price": ...,             # 来自 Ticker DataSource
	    "rsi": ...,                   # 来自 Indicator
	    "strategies": ...,            # 来自 Strategy 输出的 list 口径（当前单策略）
	}
```

**问题**：
- 变量来源不明确（哪个层级？哪个实例？）
- 无法表达层级关系（如 exchange_class → exchange → trading_pair）
- 难以实现跨层级的聚合计算（如 `sum([child["amount"] for child in children])`）

**问题 2：无法支持复杂的策略逻辑**

MarketNeutralPositions 策略需要：
- 在 `trading_pair_class_group` 层级聚合多个 trading pair 的价格
- 在 `trading_pair_class` 层级计算 ratio
- 在 `trading_pair` 层级执行订单

当前架构无法表达这种多层级的计算流程。

**问题 3：Indicator 注入位置不明确**

不同 Indicator 应该注入到不同层级：
- `TickerDataSource` → `trading_pair_class` 层级（所有 exchange 共享）
- `EquationDataSource` → `exchange` 层级（每个 exchange 实例独立）
- `MedalAmountDataSource` → `exchange` 层级（账户余额）

当前架构无法明确表达这种层级关系。

### 设计目标

1. **清晰的层级结构**：明确定义变量的作用域层级
2. **灵活的链路配置**：支持多种 Scope 链路组合
3. **高效的变量继承**：使用 ChainMap 实现自动继承
4. **可扩展性**：Strategy 可以定义自定义 Scope 类型
5. **向后兼容**：现有配置可以平滑迁移

---

## 核心概念

### 1. Scope 层级

Scope 是一个变量作用域，具有以下特性：
- **Scope Class Name**：Scope 类名（如 `GlobalScope`, `ExchangeScope`）- 在代码中定义
- **Scope Class ID**：Scope 类型标识（如 `global`, `my_scope`）- **由用户在配置中自由定义**
- **Scope Instance ID**：Scope 实例标识（如 `okx`, `okx/main`, `BTC/USDT`）
- **Parent Scope**：父 Scope（单一值）
- **Children Scopes**：子 Scope 集合（字典 `{id: child_scope}`）
- **Variables**：当前 Scope 的变量（通过 `vars` 定义，支持条件变量）

**重要说明**：
- `scope_class_id` 是用户在配置文件中自由定义的标识符，**不是硬编码的**
- 用户可以使用任何名称作为 `scope_class_id`（如 `"global"`, `"my_custom_scope"`, `"层级1"` 等）
- 配置中需要指定使用哪个 Scope 类（通过 `class` 字段）

### 2. 标准 Scope 类

| Scope Class Name | 说明 | 典型 Class ID 示例 | Instance ID 示例 | 典型变量 |
|------------------|------|-------------------|------------------|----------|
| `GlobalScope` | 全局作用域 | `global`, `全局` | `"global"` | `max_position_usd`, `weights` |
| `ExchangeClassScope` | 交易所类型 | `exchange_class`, `交易所类` | `"okx"`, `"binance"` | `exchange_class` |
| `ExchangeScope` | 交易所实例 | `exchange`, `交易所` | `"okx/main"`, `"binance/spot"` | `exchange_id`, `equation_usd`, `amount` |
| `TradingPairClassScope` | 交易对类型 | `trading_pair_class`, `交易对类` | `"okx-BTC/USDT"` | `exchange_class`, `symbol`, `mid_price`, `fair_price` |
| `TradingPairScope` | 交易对实例 | `trading_pair`, `交易对` | `"okx/main-BTC/USDT"` | `exchange_id`, `symbol`, `current_position_usd` |

**注意**：表格中的"典型 Class ID 示例"只是建议，用户可以使用任何名称。

### 2.1 计算顺序与 parent/children 访问

**计算顺序**：
1. **Indicator 注入**：首先注入所有 Indicator 提供的变量（如 `mid_price`, `rsi`, `amount` 等）
2. **vars 计算**：然后按照 Scope 树的层级顺序计算 vars（包括条件变量）

**parent/children 访问机制**：
- ✅ **自下而上聚合**：`parent` 可以访问 `children` 的 **indicator 注入的变量**
  - 因为 indicator 注入发生在 vars 计算之前
  - 示例：`sum([scope["amount"] for scope in children.values()])`

- ✅ **自上而下分配**：`child` 可以访问 `parent` 的 **vars 计算结果**
  - 因为 parent 的 vars 在 child 之前计算
  - 示例：`parent["total_budget"] * 0.5`

### 3. 多根与缓存（instance-level 严格树）

**原则**：
- Scope 的 **instance-level 拓扑是严格树**：每个实例只有一个 `parent`；`children` 由该 parent 持有。
- ScopeManager 的缓存 key 为 `(scope_class_id, scope_instance_id)`：同 key 永远返回同一实例。
- 因此同一个 `(scope_class_id, scope_instance_id)` **不能在不同父域语义下复用**；若需要不同父域下的“同类节点”，必须让 key 不冲突（例如 namespaced 的 `scope_instance_id`，或使用不同的 `scope_class_id`）。

#### 3.1 多个 GlobalScope（通过不同 scope_class_id）

多 root 的常用做法是声明多个 `scope_class_id`（用户命名），它们都指向 `GlobalScope`，从而形成多棵树：

```yaml
# conf/app/<app>.yaml（片段）
scopes:
  g_arbitrage:
    class: GlobalScope
    vars:
      - strategy_type="arbitrage"
      - max_position_usd=10000

  g_market_making:
    class: GlobalScope
    vars:
      - strategy_type="market_making"
      - max_position_usd=5000
```

```yaml
# conf/strategy/<strategy>.yaml（片段）
links:
  - id: arbitrage_link
    value: [g_arbitrage, exchange_class, exchange, trading_pair]
  - id: mm_link
    value: [g_market_making, exchange_class, exchange, trading_pair]
```

说明：
- `scope_instance_id` 由 ScopeClass 的实例发现逻辑生成；`instance_id` 是 Scope 的特殊变量（不是 YAML 字段）。
- 两个 root 彼此隔离，因为它们的 `scope_class_id` 不同。

### 4. 自定义 Scope 类型

Strategy 可以定义自己的 Scope 类型，例如：

| Scope Class Name | 说明 | Instance ID 示例 | 用途 |
|------------------|------|------------------|------|
| `TradingPairClassGroupScope` | 交易对分组 | `"ETH"`, `"BTC"` | MarketNeutralPositions 策略的分组聚合 |

### 5. Scope 链路（Links）

Scope 链路定义了从 root 到 leaf（最后一级 `TradingPairScope`）的路径。

**示例 1：标准链路**
```
global → exchange_class → exchange → trading_pair
```

**示例 2：交易对类型链路**
```
global → exchange_class → trading_pair_class → trading_pair
```

**示例 3：自定义分组链路**
```
global → exchange_class → trading_pair_class_group → trading_pair_class → trading_pair
```

一个 Strategy 可以定义多条链路，系统会为每条链路创建独立的 Scope 树。

---

## 设计细节

### 1. Scope 基类

```python
class BaseScope:
    """
    Scope 基类（实现参考：hft/core/scope/base.py）

    特性：
    - vars 使用 ChainMap 实现变量继承
    - children 维护直接子节点（由 LinkTree / links 构建时挂接；ScopeManager 不维护拓扑）
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        parent: Optional['BaseScope'] = None,
    ):
        self.scope_class_id = scope_class_id
        self.scope_instance_id = scope_instance_id
        self.parent = parent
        self.children: dict[str, 'BaseScope'] = {}
        self._vars: dict[str, Any] = {}

    @property
    def vars(self) -> ChainMap:
        """
        返回变量的 ChainMap（包含父 Scope 的变量）
        """
        if self.parent is None:
            return ChainMap(self._vars)
        return ChainMap(self._vars, self.parent.vars)

    def set_var(self, name: str, value: Any):
        """设置当前 Scope 的变量"""
        self._vars[name] = value

    def get_var(self, name: str, default=None) -> Any:
        """获取变量（自动从父 Scope 继承）"""
        return self.vars.get(name, default)

    def add_child(self, child: 'BaseScope') -> None:
        """添加子 Scope（由 LinkTree / links 构建逻辑调用）"""
        self.children[child.scope_instance_id] = child

    def __getitem__(self, name: str) -> Any:
        """支持 scope['var'] 访问"""
        return self.get_var(name)

    def __setitem__(self, name: str, value: Any) -> None:
        """支持 scope['var'] = value 写入"""
        self.set_var(name, value)
```

### 2. 标准 Scope 实现

实现参考：`hft/core/scope/scopes.py`。标准 Scope 类都使用统一构造函数签名：

`__init__(scope_class_id: str, scope_instance_id: str, parent: Optional[BaseScope] = None, **kwargs)`

并在构造时注入少量“约定变量”（便于表达式/策略引用）：

- `GlobalScope`：通常作为根节点，注入 `app_core`（AppCore 引用；不需要在 vars 中显式声明）
- `ExchangeClassScope`：注入 `exchange_class = scope_instance_id`
- `ExchangeScope`：注入 `exchange_id = scope_instance_id`
- `TradingPairClassScope`：注入 `exchange_class`/`symbol`（可从 `exchange_class-symbol` 解析，或由 kwargs 传入）
- `TradingPairScope`：注入 `exchange_id`/`symbol`（可从 `exchange_id-symbol` 解析，或由 kwargs 传入）
- `TradingPairClassGroupScope`：注入 `group_id = scope_instance_id`（供 MarketNeutralPositions 使用）

### 3. Scope 管理器

实现参考：`hft/core/scope/manager.py`。

关键点：
- Scope 类型注册以 **ScopeClass（Python 类）** 为主，由 `conf/app/*.yaml` 的 `scopes.*.class` 指定
- Scope 实例缓存 key 为 `(scope_class_id, scope_instance_id)`；同 key 永远返回同一实例
  - instance-level 为严格树，因此同 key 不允许出现“不同 parent 语义”的复用（见 `docs/scope.md`）
- Scope 树构建入口：`build_scope_tree(link, scope_configs, instance_ids_provider) -> list[BaseScope]`
  - `instance_ids_provider` 签名：`(scope_class_id: str, parent_scope: Optional[BaseScope]) -> list[str]`

```python
# 关键接口（伪代码）
scope = scope_manager.get_or_create(
    scope_class_id="exchange",          # scope_class_id（来自 links；用户自定义）
    scope_instance_id="okx/main",       # scope_instance_id（由 get_all_instance_ids 生成）
    parent=parent_scope,
)
```

### 4. 配置格式

#### App 配置

```yaml
# conf/app/example.yaml（仅示例：Scope 节点只允许在 app 配置里声明）
scopes:
  g:
    class: GlobalScope
    vars:
      - max_trading_pair_groups=10
      - max_position_usd=2000
      - weights={"okx/a": 0.1, "okx/b": 0.1}

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope

  trading_pair_class_group:
    class: TradingPairClassGroupScope
    vars:
      - group_min_price=min([scope["mid_price"] for scope in children.values()])
      - group_max_price=max([scope["mid_price"] for scope in children.values()])
      - score=group_max_price - group_min_price

  trading_pair_class:
    class: TradingPairClassScope
    vars:
      - delta_min_price=trading_pair_std_price - parent["group_min_price"]
      - delta_max_price=parent["group_max_price"] - trading_pair_std_price

  trading_pair:
    class: TradingPairScope
    vars:
      - ratio_est=weight * (group_min_price * amount) / max_position_usd
```

#### Strategy 配置

```yaml
# conf/strategy/example.yaml
class_name: market_neutral_positions

# 包含/排除交易对
include_symbols: ['*']  # 默认包含所有
exclude_symbols: []     # 排除列表

# 交易所选择由 AppConfig.exchanges 控制；Strategy 侧当前仅提供 symbol 过滤（include_symbols/exclude_symbols）

# 依赖的 Indicator
requires:
  - medal_amount  # MedalAmountDataSource（注入到 exchange scope）
  - ticker        # TickerDataSource（注入到 trading_pair_class scope）
  - fair_price    # FairPriceIndicator（注入到 trading_pair_class scope）

# Scope 链路配置
links:
  - id: link_main
    value: [g, exchange_class, exchange, trading_pair_class_group, trading_pair_class, trading_pair]

# 目标配置（在最后一级 TradingPairScope 上匹配与计算）
targets:
  - exchange_id: "*"
    symbol: "*"
    condition: "ratio != 0"
    vars:
      - position_usd=ratio * max_position_usd
```

#### Executor 配置

```yaml
# conf/executor/example.yaml
class_name: limit

# 订单配置
order:
  vars:
    - name: order_amount
      value: delta_usd / mid_price
  condition: abs(delta_usd) > 10
```

---

## Strategy 集成

### 1. BaseStrategy 变更

实现参考：`hft/strategy/base.py`。

现状（已落地的最小骨架）：
- `BaseStrategy` 新增：`scope_manager`、`scope_trees`、`_register_custom_scopes()`、`_build_scope_trees()`、`get_output()`
- `on_start()` 在配置了 `links` 时，从 `root.scope_manager` 获取 ScopeManager 并构建 scope 树
- `get_output()` 以最后一级 `TradingPairScope` 为输出粒度返回 `StrategyOutput`；scope vars/target vars/condition 的表达式计算仍需补齐（见 Phase 2）

---

## 向后兼容性

### 1. 非 Scope 配置兼容

对于不使用 Scope 系统的旧配置，保持完全兼容：

```yaml
# 旧配置（不使用 Scope）
class_name: static_positions
targets:
  - symbol: BTC/USDT
    position_usd: 1000
    speed: 0.5
```

**兼容策略**：
- 如果配置中没有 `links` 字段，使用旧的变量注入机制
- 旧 Strategy 继续实现 `get_target_positions_usd()`
- 新 Strategy 可以在自己的 `get_target_positions_usd()` 内调用 `get_output()` 并返回 `StrategyOutput`

### 2. 渐进式迁移

支持混合使用 Scope 和非 Scope 配置：
- 旧 Strategy 继续使用 `collect_context_vars()` 方法
- 新 Strategy 使用 Scope 系统
- 两种方式可以在同一个 App 中共存

---

## 任务列表

### Phase 1: Scope 基础设施（P0）

- [x] 实现 `BaseScope` 基类（已通过）
- [x] 实现标准 Scope 类型（已通过）
  - [x] `GlobalScope`（已通过）
  - [x] `ExchangeClassScope`（已通过）
  - [x] `ExchangeScope`（已通过）
  - [x] `TradingPairClassScope`（已通过）
  - [x] `TradingPairScope`（已通过）
- [x] 实现 `ScopeManager`（已通过）
  - [x] Scope 类型注册（已通过）
  - [x] Scope 实例缓存（已通过：cache key 使用 `(scope_class_id, scope_instance_id)`，不含 parent）
  - [x] Scope 树构建（已通过：`build_scope_tree` + 递归构建已实现）
  - [x] 支持创建 `TradingPairScope`（已通过：支持 kwargs 传参 + `exchange_id-symbol` 解析）
- [x] 单元测试：Scope 基础功能（已通过：test_scope_vars.py 包含 10 个测试，全部通过）

### Phase 2: Strategy 集成（P0）

- [x] 重构 `BaseStrategy`（已通过）
  - [x] 添加 `scope_manager` 属性（已通过）
  - [x] 添加 `_register_custom_scopes()` 方法（已通过）
  - [x] 添加 `_build_scope_trees()` 方法（已通过：已完整实现所有 Scope 类型的动态获取）
  - [x] 实现 `get_output()` 方法（已通过：实现了按需创建 + 两遍遍历 + 表达式求值 + condition gate）
  - [x] 实现 `_get_or_create_scope_for_target()` 方法（已通过：支持所有 Scope 类型的按需创建）
  - [x] 实现 `_inject_indicator_vars_to_scope()` 方法（已通过：第一遍遍历注入 Indicator 变量）
  - [x] 实现 `_compute_scope_vars()` 方法（已通过：计算 scope config 中的 vars）
  - [x] 实现 `_evaluate_targets()` 方法（已通过：匹配并求值 targets 配置）
  - [x] 在 `on_start()` 中初始化 Scope 系统（已通过）
- [x] 更新 `BaseStrategyConfig`（已通过）
  - [x] 添加 `links` 字段（已通过）
  - [x] ~~添加 `scopes` 字段~~（Scope 节点声明仅在 AppConfig `scopes`；Strategy 配置只引用 `links`）（已通过）
  - [ ] 添加 `target_scope` 字段（审核不通过：目标层级固定为最后一级 `TradingPairScope`，无需额外字段）
  - [x] 添加 `include_symbols` / `exclude_symbols` 字段（已通过）
- [x] 单元测试：Strategy Scope 集成（已通过）

### Phase 3: Executor 集成（P1）

- [ ] 重构 `BaseExecutor`（待实现：Phase 3 暂缓，当前 Scope 系统已可用于 Strategy）
  - [ ] 集成 Scope 系统（待实现）
  - [ ] 从 Scope 读取变量（待实现）
- [ ] 更新 `BaseExecutorConfig`（待实现）
  - [ ] 添加 `order_scope` 字段（审核不通过：订单层级固定为最后一级 `TradingPairScope`）
  - [ ] 添加 `entry_order_scope` / `exit_order_scope` 字段（审核不通过：同上）
  - [ ] 单元测试：Executor Scope 集成（待实现）

### Phase 4: Indicator 集成（P1）

- [x] 重构 `BaseIndicator`（已通过）
  - [x] 添加 `scope_level` 属性（已通过）
  - [x] 支持注入到指定 Scope（已通过：Strategy._inject_indicator_vars_to_scope 已实现）
- [x] 更新现有 Indicator（已通过）
  - [x] `TickerDataSource` → `trading_pair_class` scope（已通过）
  - [x] `EquationDataSource` → `exchange` scope（已通过）
- [x] 单元测试：Indicator Scope 注入（已通过）

### Phase 5: AppCore 集成（P1）

- [x] 重构 `AppCore`（已通过）
  - [x] 添加 `scope_manager` 属性（已通过）
  - [x] 初始化 Scope 系统（已通过：已在 __init__ 中初始化）
- [x] 更新 `AppConfig`（已通过）
  - [x] 添加 `scopes` 配置（已通过）
- [x] 单元测试：AppCore Scope 初始化（已通过）

### Phase 6: 适配现有 Strategy（P2）

- [x] 适配 `StaticPositionsStrategy`（已通过）
  - [x] 支持 Scope 配置（可选）（已通过：继承自 BaseStrategy，自动支持）
  - [x] 保持向后兼容（已通过：旧配置格式仍然支持）
- [x] 单元测试：StaticPositions Scope 支持（已通过）

### Phase 7: 文档和示例（P2）

- [x] 编写 `docs/scope.md` 用户指南（已通过）
- [x] 编写 `docs/vars.md` 用户指南（已通过）
- [x] 编写 `docs/scope-execution-flow.md` 执行流程文档（已通过）
- [x] 更新 `docs/strategy.md`（已通过）
- [x] 更新 `docs/executor.md`（已通过）
- [x] 更新 `docs/indicator.md`（已通过）
- [x] 更新 `docs/architecture.md`（已通过）
- [x] 编写 `examples/005-scope-system-guide.md`（已通过）

---

## 影响范围

### 核心模块

| 模块 | 影响 | 说明 |
|------|------|------|
| `hft/core/scope/` | **新增** | Scope 系统核心模块 |
| `hft/core/app/base.py` | **重大** | 集成 ScopeManager |
| `hft/strategy/base.py` | **重大** | 集成 Scope 系统 |
| `hft/strategy/config.py` | **重大** | 添加 `links` 等 Scope 引用字段（Scope 节点声明在 AppConfig） |
| `hft/executor/base.py` | **重大** | 集成 Scope 系统 |
| `hft/executor/config.py` | **中等** | 添加 Scope 配置字段 |
| `hft/indicator/base.py` | **中等** | 支持 Scope 注入 |

### 测试文件

| 文件 | 影响 | 说明 |
|------|------|------|
| `tests/test_scope_system.py` | **新增** | Scope 系统单元测试 |
| `tests/test_strategy_scope.py` | **新增** | Strategy Scope 集成测试 |
| `tests/test_executor_scope.py` | **新增** | Executor Scope 集成测试 |

### 文档

| 文件 | 影响 | 说明 |
|------|------|------|
| `docs/scope.md` | **新增** | Scope 系统用户指南 |
| `docs/strategy.md` | **重大** | 添加 Scope 配置章节 |
| `docs/executor.md` | **中等** | 添加 Scope 配置章节 |
| `docs/indicator.md` | **中等** | 添加 Scope 注入说明 |
| `docs/architecture.md` | **重大** | 添加 Scope 架构图 |
| `examples/005-scope-system-guide.md` | **新增** | Scope 使用示例 |

---

## 关键问题

### 1. Scope 实例 ID 生成规则

**问题**：如何为不同层级的 Scope 生成唯一的 `scope_instance_id`？

**方案**：
- `global`: 固定为 `"global"`
- `exchange_class`: 使用 `exchange_class` 名称（如 `"okx"`）
- `exchange`: 使用 `exchange_id`（如 `"okx/main"`）
- `trading_pair_class_group`: 建议使用 namespaced id（如 `"okx-ETH"`），避免跨 `exchange_class` 的 group_id 冲突
- `trading_pair_class`: 建议使用 namespaced id（如 `"okx-BTC/USDT"`），避免跨 `exchange_class` 的 symbol 冲突
- `trading_pair`: 建议使用 namespaced id（如 `"okx/main-BTC/USDT:USDT"` 或 `"okx/a-ETH/USDT"`），避免跨 exchange 实例冲突

备注：ScopeManager 的缓存 key 固定为 `(scope_class_id, scope_instance_id)`；因此 `scope_instance_id` 必须在其所属的 `scope_class_id` 命名空间内保持唯一。

### 2. ChainMap 性能优化

**问题**：ChainMap 的查找性能是否会成为瓶颈？

**方案**：
- 使用缓存优化频繁访问的变量
- 限制 Scope 层级深度（建议不超过 5 层）
- 对于性能敏感的场景，提供 `flatten()` 方法将 ChainMap 展平为普通 dict

### 3. Scope 树的内存占用

**问题**：大量 Scope 实例是否会占用过多内存？

**方案**：
- 使用弱引用（weakref）管理 Scope 实例
- 提供 `clear_cache()` 方法清理不再使用的 Scope
- 对于不活跃的 Scope，使用延迟加载策略

### 4. 自定义 Scope 的注册时机

**问题**：Strategy 的自定义 Scope 何时注册？

**方案**：
- 在 `BaseStrategy.on_start()` 中调用 `_register_custom_scopes()`
- 子类重写此方法，注册自己的 Scope 类型
- 注册必须在 `_build_scope_trees()` 之前完成

### 5. 表达式能力与安全限制（simpleeval）

**现状**：表达式求值使用 `simpleeval.EvalWithCompoundTypes`（受函数白名单控制），支持 compound types（dict/list/tuple/set 等）与简单 comprehension（受 `MAX_COMPREHENSION_LENGTH` 限制）。

**建议**：
- 表达式保持“短 + 可读”，避免在 expression 内写大循环/深层嵌套（即使语法支持也可能触发长度限制或带来性能开销）。
- 对 children 聚合场景，优先提供/使用 helper（推荐），例如：`child_values(children, "mid_price")`、`min_non_none(...)`、`sum_values(children, "ratio_est")`；或在实现层预先计算统计量并注入。

---

## 相关文档

- [Feature 0008: Strategy 数据驱动](./0008-strategy-data-driven.md)
- [Feature 0010: Executor vars 系统](./0010-executor-vars-system.md)
- [Feature 0011: Strategy Target 展开式与去特殊化](./0011-strategy-target-expansion.md)
- [Feature 0013: MarketNeutralPositions 策略](./0013-market-neutral-positions-strategy.md)
- [Issue 0009: Strategy 方法名与"去特殊化"设计冲突](../issue/0009-strategy-method-name-conflicts-with-despecialization.md)
