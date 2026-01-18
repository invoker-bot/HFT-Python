# Feature 0012: Scope 系统

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
    "strategies": ...,            # 来自 StrategyGroup
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
- 配置中需要指定使用哪个 Scope 类（通过 `class_name` 字段）

### 2. 标准 Scope 类

| Scope Class Name | 说明 | 典型 Class ID 示例 | Instance ID 示例 | 典型变量 |
|------------------|------|-------------------|------------------|----------|
| `GlobalScope` | 全局作用域 | `global`, `全局` | `"global"` | `max_position_usd`, `weights` |
| `ExchangeClassScope` | 交易所类型 | `exchange_class`, `交易所类` | `"okx"`, `"binance"` | `exchange_class` |
| `ExchangeScope` | 交易所实例 | `exchange`, `交易所` | `"okx/main"`, `"binance/spot"` | `exchange_path`, `equation_usd`, `amount` |
| `TradingPairClassScope` | 交易对类型 | `trading_pair_class`, `交易对类` | `"BTC/USDT"` | `symbol`, `mid_price`, `fair_price` |
| `TradingPairScope` | 交易对实例 | `trading_pair`, `交易对` | `"okx/main:BTC/USDT"` | `exchange_path`, `symbol`, `current_position_usd` |

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

### 3. 多个根节点和 Scope 复用

**重要特性**：Scope 系统支持多个根节点（如多个 GlobalScope），形成**森林结构**而非单一树结构。

#### 3.1 多个 GlobalScope

用户可以创建多个 GlobalScope，用于不同的策略或场景：

```yaml
scopes:
  global_arbitrage:  # scope_class_id
    class_name: GlobalScope
    instance_id: "arbitrage"  # 必须不同
    vars:
      - name: strategy_type
        value: "arbitrage"
      - name: max_position_usd
        value: 10000

  global_market_making:  # scope_class_id
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
- 每个 GlobalScope 必须有不同的 `instance_id`（用于缓存区分）
- 不同的 GlobalScope 形成独立的 Scope 树
- Scope 是“单父节点”结构：同一个 Scope 实例不会被多个 parent 共享（避免 parent 冲突）

#### 3.2 Scope 复用

ScopeManager 只会在**同一条完整 Scope 路径**（包含 parent 链）上复用实例：

```yaml
# 两棵不同的 Scope 树（两个根）
links:
  - ["global_1", "exchange", "trading_pair"]  # 策略 1
  - ["global_2", "exchange", "trading_pair"]  # 策略 2
```

**缓存机制**：
- 缓存 key 是 `scope_path`（由 `scope_class_id:scope_instance_id` + parent 的 `scope_path` 递归组成）
- 同一 `scope_path` 命中缓存 → 返回同一个实例（复用）
- 不同 parent 链 → 创建不同实例（从根上避免“一个 Scope 多个 parent”的不一致状态）

### 4. 自定义 Scope 类型

Strategy 可以定义自己的 Scope 类型，例如：

| Scope Class Name | 说明 | Instance ID 示例 | 用途 |
|------------------|------|------------------|------|
| `TradingPairClassGroupScope` | 交易对分组 | `"ETH"`, `"BTC"` | MarketNeutralPositions 策略的分组聚合 |

### 5. Scope 链路（Links）

Scope 链路定义了从 `global` 到 `target_scope` 的路径。

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
    - children 维护直接子节点（由 ScopeManager 创建时挂接）
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
        """添加子 Scope（由 ScopeManager 调用）"""
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

- `GlobalScope`：通常作为根节点，无固定注入变量
- `ExchangeClassScope`：注入 `exchange_class = scope_instance_id`
- `ExchangeScope`：注入 `exchange_path = scope_instance_id`
- `TradingPairClassScope`：注入 `symbol = scope_instance_id`
- `TradingPairScope`：注入 `exchange_path`/`symbol`（支持从 `exchange_path:symbol` 解析，或由 kwargs 传入）
- `TradingPairClassGroupScope`：注入 `group_id = scope_instance_id`（供 MarketNeutralPositions 使用）

### 3. Scope 管理器

实现参考：`hft/core/scope/manager.py`。

关键点：
- Scope 类型注册以 **Scope Class Name** 为主（如 `"GlobalScope"`），由配置的 `class_name` 指定
- Scope 实例缓存以 `scope_path` 为 key（包含完整 parent 链），避免同一个实例出现多个 parent
- Scope 树构建入口：`build_scope_tree(link, scope_configs, instance_ids_provider) -> list[BaseScope]`
  - `instance_ids_provider` 签名：`(scope_class_id: str, parent_scope: Optional[BaseScope]) -> list[str]`

```python
# 关键接口（伪代码）
scope = scope_manager.get_or_create(
    scope_class_name="ExchangeScope",   # Scope 类名（来自配置）
    scope_class_id="exchange",          # Scope Class ID（来自 links；用户可自定义）
    scope_instance_id="okx/main",       # Instance ID（由 provider 提供）
    parent=parent_scope,
)
```

### 4. 配置格式

#### App 配置

```yaml
# conf/app/example.yaml
scopes:
  global:
    class_name: GlobalScope
    children: ["exchange_class"]

  exchange_class:
    class_name: ExchangeClassScope
    children: ["exchange", "trading_pair_class", "trading_pair_class_group"]

  exchange:
    class_name: ExchangeScope
    children: ["trading_pair"]

  trading_pair_class:
    class_name: TradingPairClassScope
    children: ["trading_pair"]

  trading_pair:
    class_name: TradingPairScope

  # 自定义 Scope（由 Strategy 定义）
  trading_pair_class_group:
    class_name: TradingPairClassGroupScope
    children: ["trading_pair_class"]
```

#### Strategy 配置

```yaml
# conf/strategy/example.yaml
class_name: market_neutral_positions

# 包含/排除交易对
include_symbols: ['*']  # 默认包含所有
exclude_symbols: []     # 排除列表

# 交易所过滤
exchanges: ['*']  # 默认包含所有 app 中定义的 exchanges

# 依赖的 Indicator
requires:
  - medal_amount  # MedalAmountDataSource（注入到 exchange scope）
  - ticker        # TickerDataSource（注入到 trading_pair_class scope）
  - fair_price    # FairPriceIndicator（注入到 trading_pair_class scope）

# Scope 链路配置
links:
  - ["global", "exchange_class", "exchange", "trading_pair_class_group", "trading_pair_class", "trading_pair"]

# 每个 Scope 层级的变量配置
scopes:
  global:
    vars:
      - name: max_trading_pair_groups
        value: 10
      - name: max_position_usd
        value: 2000
      - name: weights
        value: {"okx/a": 0.1, "okx/b": 0.1}

  trading_pair_class_group:
    vars:
      - name: group_min_price
        value: min([scope["mid_price"] for scope in children.values()])
      - name: group_max_price
        value: max([scope["mid_price"] for scope in children.values()])
      - name: score
        value: group_max_price - group_min_price

  trading_pair_class:
    vars:
      - name: delta_min_price
        value: trading_pair_std_price - parent["group_min_price"]
      - name: delta_max_price
        value: parent["group_max_price"] - trading_pair_std_price

  trading_pair:
    vars:
      - name: ratio_est
        value: weight * (group_min_price * amount) / max_position_usd

# 目标 Scope 层级（Strategy 输出的层级）
target_scope: trading_pair

# 目标配置（在 target_scope 层级计算）
target:
  vars:
    - name: position_usd
      value: ratio * max_position_usd
  condition: ratio != 0
```

#### Executor 配置

```yaml
# conf/executor/example.yaml
class_name: limit

# 订单 Scope 层级（Executor 执行的层级）
order_scope: trading_pair

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
- `get_output()` 当前仅提取 `target_scope` 层级的 `scope._vars` 作为 `StrategyOutput` 返回；scope vars/target vars/condition 的表达式计算仍待补齐（见 Phase 2）

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
  - [x] Scope 实例缓存（已通过：cache key 使用 `scope_path`（含完整 parent 链））
  - [x] Scope 树构建（已通过：`build_scope_tree` + 递归构建已实现）
  - [x] 支持创建 `TradingPairScope`（已通过：支持 kwargs 传参 + `exchange_path:symbol` 解析）
- [ ] 单元测试：Scope 基础功能（待实现：需要添加 parent 冲突场景和树构建测试）

### Phase 2: Strategy 集成（P0）

- [ ] 重构 `BaseStrategy`（审核不通过：Scope 树构建的 instance_ids_provider 仍为 TODO，且 `get_output()` 未实现 vars/condition 的求值链路）
  - [x] 添加 `scope_manager` 属性（已通过）
  - [x] 添加 `_register_custom_scopes()` 方法（已通过）
  - [ ] 添加 `_build_scope_trees()` 方法（待审核：已完整实现所有 Scope 类型的动态获取，包括 GlobalScope/ExchangeClassScope/ExchangeScope/TradingPairClassScope/TradingPairScope）
  - [ ] 实现 `get_output()` 方法（审核不通过：当前仅提取 `scope._vars`，未计算 scopes/target 的表达式，也未做 condition gate）
  - [x] 在 `on_start()` 中初始化 Scope 系统（已通过）
- [x] 更新 `BaseStrategyConfig`（已通过）
  - [x] 添加 `links` 字段（已通过）
  - [x] 添加 `scopes` 字段（已通过）
  - [x] 添加 `target_scope` 字段（已通过）
  - [x] 添加 `include_symbols` / `exclude_symbols` 字段（已通过）
- [ ] 单元测试：Strategy Scope 集成（待实现）

### Phase 3: Executor 集成（P1）

- [ ] 重构 `BaseExecutor`（待实现：Phase 3 暂缓，当前 Scope 系统已可用于 Strategy）
  - [ ] 集成 Scope 系统（待实现）
  - [ ] 从 Scope 读取变量（待实现）
- [ ] 更新 `BaseExecutorConfig`（待实现）
  - [ ] 添加 `order_scope` 字段（待实现）
  - [ ] 添加 `entry_order_scope` / `exit_order_scope` 字段（待实现）
- [ ] 单元测试：Executor Scope 集成（待实现）

### Phase 4: Indicator 集成（P1）

- [ ] 重构 `BaseIndicator`（待实现：Scope 注入链路尚未实现）
  - [x] 添加 `scope_level` 属性（已通过）
  - [ ] 支持注入到指定 Scope（待实现：需要 Strategy 在计算 Scope vars 时调用 Indicator.calculate_vars()）
- [ ] 更新现有 Indicator（待实现）
  - [ ] `TickerDataSource` → `trading_pair_class` scope（待实现）
  - [ ] `EquationDataSource` → `exchange` scope（待实现）
- [ ] 单元测试：Indicator Scope 注入（待实现）

### Phase 5: AppCore 集成（P1）

- [x] 重构 `AppCore`（已通过）
  - [x] 添加 `scope_manager` 属性（已通过）
  - [x] 初始化 Scope 系统（已通过：已在 __init__ 中初始化）
- [ ] 更新 `AppConfig`（待实现）
  - [ ] 添加 `scopes` 配置（待实现）
- [ ] 单元测试：AppCore Scope 初始化（待实现）

### Phase 6: 适配现有 Strategy（P2）

- [ ] 适配 `StaticPositionsStrategy`（待实现）
  - [ ] 支持 Scope 配置（可选）（待实现）
  - [ ] 保持向后兼容（待实现）
- [ ] 单元测试：StaticPositions Scope 支持（待实现）

### Phase 7: 文档和示例（P2）

- [ ] 编写 `docs/scope.md` 用户指南（待实现）
- [ ] 更新 `docs/strategy.md`（待实现）
- [ ] 更新 `docs/executor.md`（待实现）
- [ ] 更新 `docs/indicator.md`（待实现）
- [ ] 更新 `docs/architecture.md`（待实现）
- [ ] 编写 `examples/005-scope-system-guide.md`（待实现）

---

## 影响范围

### 核心模块

| 模块 | 影响 | 说明 |
|------|------|------|
| `hft/core/scope/` | **新增** | Scope 系统核心模块 |
| `hft/core/app/base.py` | **重大** | 集成 ScopeManager |
| `hft/strategy/base.py` | **重大** | 集成 Scope 系统 |
| `hft/strategy/config.py` | **重大** | 添加 Scope 配置字段 |
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
- `exchange`: 使用 `exchange_path`（如 `"okx/main"`）
- `trading_pair_class_group`: 建议使用 namespaced id（如 `"okx:ETH"`），避免跨 `exchange_class` 的 group_id 冲突
- `trading_pair_class`: 建议使用 namespaced id（如 `"okx:BTC/USDT"`），避免跨 `exchange_class` 的 symbol 冲突
- `trading_pair`: 使用 `f"{exchange_path}:{symbol}"`（如 `"okx/main:BTC/USDT"`）

如果希望 `trading_pair_class` 仍保持仅 `symbol`，则 `ScopeManager` 的 cache key 需要包含 parent（或完整 scope path），否则无法同时表达：
`exchange_class(okx) -> trading_pair_class(BTC/USDT)` 与 `exchange_class(binance) -> trading_pair_class(BTC/USDT)`。

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

### 5. 表达式能力限制（simpleeval）

**问题**：当前求值器（`simpleeval`）不支持 list/dict comprehension（例如 `min([scope["mid_price"] for scope in children.values()])` 会报错），但本文档示例大量依赖该写法做 children 聚合。

**方案**：
- 提供 VM 内置聚合函数（推荐），例如：`child_values(children, "mid_price")`、`min_non_none(...)`、`sum_values(children, "ratio_est")`
- 或在实现层预先把需要的聚合列表/统计量计算好，再注入到 scope vars，避免在表达式里做遍历

---

## 相关文档

- [Feature 0008: Strategy 数据驱动](./0008-strategy-data-driven.md)
- [Feature 0010: Executor vars 系统](./0010-executor-vars-system.md)
- [Feature 0011: Strategy Target 展开式与去特殊化](./0011-strategy-target-expansion.md)
- [Feature 0013: MarketNeutralPositions 策略](./0013-market-neutral-positions-strategy.md)
- [Issue 0009: Strategy 方法名与"去特殊化"设计冲突](../issue/0009-strategy-method-name-conflicts-with-despecialization.md)
