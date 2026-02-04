# Scope 系统（重构规则）

本文档定义 Scope 系统的**配置边界**、**实例发现**、**缓存语义**、**变量继承（ChainMap）**与**执行流程**。任何与本文档不一致的行为应视为实现缺陷。

## 1. 配置边界（必须严格遵守）

Scope 相关配置分布在不同的配置文件中，**同名字段在不同文件里含义不同**，不可混用：

| 配置文件 | 允许的 Scope 字段 | 说明 |
|----------|------------------|------|
| `conf/app/*.yaml` | `scopes:` | **唯一允许声明 `scopes:` 的地方**（声明 scope 类型、类、初值） |
| `conf/strategy/*.yaml` | `flow:`, `requires:`, `targets:` | 策略逻辑；**不允许声明 `scopes:`** |
| `conf/executor/*.yaml` | `vars:`, `scope:` | 消费 Strategy 返回的 Scope；**不声明 Scope 节点** |
| `conf/exchange/*.yaml` | 无 | 交易所配置，不包含 Scope 系统配置 |

## 2. 术语与不变量

### 2.1 Scope 的两类"ID"

| 术语 | 来源 | 示例 | 说明 |
|------|------|------|------|
| `scope_class_id` | 用户在 `conf/app/*.yaml` 的 `scopes` 字段 key 中定义 | `g`, `exchange`, `trading_pair` | 用户命名的 scope 标识符；在 `flow` 中通过 `class_name` 引用 |
| `scope_instance_id` | 由 Scope 类的 `get_all_instance_ids` 方法生成 | `("global",)`, `("okx/a",)`, `("okx/a", "ETH/USDT")` | Scope 实例 ID（元组格式） |

### 2.2 ScopeClass（Python 类）

ScopeClass 是 Python 中的 Scope 实现类：

| ScopeClass | 说明 |
|------------|------|
| `GlobalScope` | 全局作用域（根节点） |
| `ExchangeClassScope` | 交易所类型 |
| `ExchangeScope` | 交易所实例 |
| `TradingPairClassScope` | 交易对类型（跨 exchange） |
| `TradingPairScope` | 交易对实例（最细粒度） |

### 2.3 缓存语义（强约束）

ScopeManager 必须满足：

```text
get_or_create(scope_class_id, scope_instance_id) -> 同一个 Scope 实例（全局唯一）
```

缓存 key 为 `(scope_class_id, scope_instance_id)`；**不包含 parent 链**。

为保证"实例拓扑为严格树"：

1. 对任意 `(scope_class_id, scope_instance_id)`，其 parent 关系是确定且稳定的
2. 若同一 ScopeClass 需要在不同 parent 类型下复用，必须保证 `scope_instance_id` **不会冲突**

### 2.4 ScopeVars 与 ChainMap

每个 Scope 在表达式求值时拥有一个"变量上下文"（`scope_vars`），其核心是 **ChainMap**：

- 继承链由 `flow` 中的前驱节点（`prev`）决定
- 当前节点的变量覆盖前驱节点的变量
- 实际上下文会先注入特殊变量（如 `prev`），再合并本节点 vars，最后合并前驱节点 vars

示例 flow 层级：`GlobalScope → ExchangeScope → TradingPairScope`

则 `TradingPairScope` 节点的查找顺序为：

```python
ChainMap(injected_vars, trading_pair_vars, exchange_vars, global_vars)
```

### 2.5 特殊变量（由 Scope 系统自动提供）

所有 Scope 都拥有特殊变量 `instance_id`（等于 `scope_instance_id`）与 `class_id`（等于 `scope_class_id`），并且都会注入 `app_core`（实例创建时传入）。

各 ScopeClass 的额外特殊变量：

| ScopeClass | 特殊变量 | 说明 |
|------------|----------|------|
| `GlobalScope` | `app_core` | AppCore 实例引用（所有 Scope 都会注入 `app_core`） |
| `ExchangeClassScope` | `exchange_class` | 交易所类名（如 `"okx"`, `"binance"`） |
| `ExchangeScope` | `exchange_path` | exchange path（如 `"okx/a"`）；exchange 实例需按需从 `app_core.exchange_group` 查找 |
| `TradingPairClassScope` | `symbol`, `exchange_class` | 交易对符号与 exchange_class（由 `instance_id` 解析） |
| `TradingPairScope` | `exchange_path`, `symbol` | exchange path 及交易对符号 |

## 3. AppConfig: `scopes` 声明

`conf/app/*.yaml` 中的 `scopes:` 用于声明可用的 scope 节点类型。

```yaml
# conf/app/<app>.yaml（仅 app 配置允许 scopes 字段）
scopes:
  g:                    # scope_class_id（用户命名）
    class: GlobalScope  # ScopeClass（Python 类名）
    vars:               # scope 创建时初值
      - max_position_usd=10000
      - weights={"okx/a": 0.6, "okx/b": 0.4}

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope
    vars:
      - exchange_weight=weights.get(exchange_path, 0.5)

  trading_pair:
    class: TradingPairScope
    vars:
      - target_position=max_position_usd * exchange_weight
```

**说明**：
- `g` 只是用户取的名字；Scope 的 `instance_id` 由 Strategy 的实例发现逻辑决定（或由 `scopes.*.instance_id` 直接指定）
- `vars` 表示 scope 创建时的初始变量（strategy/executor 中同名 vars 会覆盖）
- 不支持 `group_condition` 字段（出现即视为配置错误）；如需过滤 group，请在 vars 中计算布尔量（如 `group_enabled`）并在 leaf `targets[*].condition` 中引用

### 3.1 实例发现（当前实现）

当前 Strategy 构建 Scope 树时使用 `BaseStrategy._get_instance_ids` 进行实例发现，不直接调用注册器 `get_all_instance_ids`。  
若需要接入注册器，需自行在 Strategy 层重写实例发现逻辑。

### 3.2 当前默认展开（BaseStrategy）

| ScopeClass | 返回示例 | 由什么决定 |
|------------|----------|-----------|
| `GlobalScope` | `["global"]` | 固定返回 1 个 |
| `ExchangeClassScope` | `["okx", "binance"]` | `app_core.exchange_group` 中实例的 `class_name` |
| `ExchangeScope` | `["okx/a", "okx/b"]` | `app_core.exchange_group` 中实例的 `config.path` |
| `TradingPairClassScope` | `["ETH/USDT"]` | `include_symbols/exclude_symbols` 过滤后的 symbol 集合 |
| `TradingPairScope` | `["okx/a:ETH/USDT"]` | 由父节点与 `include_symbols/exclude_symbols` 展开得到（当前 Strategy 使用 `exchange_path:symbol`） |

**说明**：
- `TradingPairClassScope` 当前 Scope 类解析使用 `exchange_class-symbol`，而 Strategy 默认只生成 `symbol`，需要对齐（否则会触发解析错误）。
- `TradingPairScope` 当前 Scope 类解析使用 `exchange_path-symbol`，而 Strategy 默认生成 `exchange_path:symbol`，需要对齐（否则会触发解析错误）。
- 实例发现目前不校验 exchange 是否支持某个 symbol（由上层调用方决定是否需要额外过滤）。

**两条路径**：

```
路径1: GlobalScope → ExchangeClassScope → ExchangeScope → TradingPairScope
路径2: GlobalScope → ExchangeClassScope → TradingPairClassScope → TradingPairScope
```

## 4. StrategyConfig: `flow` 与 FlowScopeNode

Strategy 的 `flow` 定义 Scope 的计算流程。每个 flow 层级独立计算，支持过滤和条件判断。

```yaml
# conf/strategy/<strategy>.yaml
flow:
  - class_name: GlobalScope
    vars:
      - max_position_usd=10000
  - class_name: ExchangeScope
    filter: "exchange_class == 'okx'"
    vars:
      - exchange_weight=0.6
  - class_name: TradingPairScope
    vars:
      - target_position=max_position_usd * exchange_weight
    condition: "target_position != 0"
```

### 4.1 Flow 执行过程

1. 从第一层开始（如 GlobalScope）
2. 调用 `ScopeClass.get_all_instance_ids(app_core)` 获取实例列表
3. 根据前一层的结果建立映射关系（通过 `flow_mapper`）
4. 对每个 instance_id：
   - 调用 `ScopeManager.get_or_create(...)` 获取/创建 scope 实例
   - 创建 `FlowScopeNode`，包含当前 scope + 前驱节点列表（`prev`）
   - 应用 `filter`（前验条件）
   - 执行变量赋值（`vars`）
   - 应用 `condition`（后验条件）
5. 进入下一层，重复上述过程

### 4.2 FlowScopeNode

FlowScopeNode 是临时的计算节点，包含：
- `scope`: 实际的 BaseScope 实例
- `prev`: 前驱节点列表（可以有多个）
- `injected_vars`: 注入的特殊变量（如 `prev`）

变量查找通过 ChainMap 实现继承：
```python
ChainMap(injected_vars, scope.vars, prev[0].vars, ...)
```

### 4.3 Filter 和 Condition

每个 flow 层级支持两种过滤机制：

| 字段 | 时机 | 说明 |
|------|------|------|
| `filter` | 前验条件 | 在执行 vars 之前过滤，False 则跳过该节点 |
| `condition` | 后验条件 | 在执行 vars 之后过滤，False 则跳过该节点 |

**注意**：被过滤掉的节点不会传递到下一层。

## 5. 每个 tick 的执行流程（Strategy）

对每个 flow 配置，每个 tick 的计算分为**逐层执行**：

### 5.1 Indicator 注入

对每个节点执行一次指标注入：

1. 从 `requires` 中逐个取 Indicator
2. 按当前节点的 `exchange_path` / `symbol` 查找对应 Indicator
3. 将返回的变量写入当前 scope 的变量空间

**级联 not ready 机制**：

如果某个 scope 的 Indicator **not ready**（数据未就绪），则：
- 标记当前 scope 为 not ready
- not ready 的 scope 会跳过 vars 计算与 target 匹配

### 5.2 变量计算

按 flow 层级顺序执行变量计算：

```yaml
vars:
  - name: foo
    value: "mid_price * 0.5"
```

### 5.3 条件变量

支持条件更新的变量：

```yaml
vars:
  - name: target_position
    value: "1000 if volume > 1000000 else 0"
    on: "time() - get_var_update_time('target_position') > 60"  # 条件更新
    initial_value: 0
```

## 6. targets 配置与匹配

### 6.1 targets 格式

```yaml
# conf/strategy/<strategy>.yaml
condition: null           # 全局门控（可选；默认 null=True；False/异常时跳过全部 targets）
targets:
  - exchange_id: "*"        # 匹配所有 exchange（默认）
    symbol: "*"             # 匹配所有 symbol（默认）
    condition: "rsi < 30"   # 条件表达式（可选，默认 True）
    vars:
      - name: position_usd
        value: "max_position_usd * 0.5"
      - name: speed
        value: "0.8"
```

### 6.2 匹配逻辑

每个最后一层的节点从前往后扫描 `targets` 列表，**贪婪匹配**。只有包含 `exchange_path` 与 `symbol` 的 scope 会产生输出：

1. 匹配到第一个符合的 target → 使用该 target 的 vars 和 condition
2. 如果没有匹配到任何 target → 该 scope 不执行（被裁剪掉）

### 6.3 target 输出

Strategy 输出阶段：

1. 基于 flow 的计算结果得到最后一层的节点
2. 若 `strategy.condition` 不为空：在该节点上求值；False/异常则忽略该 scope
3. 对每个节点：
   - 匹配 target，计算 condition
   - condition 为 True → 计算 target vars，收集到输出
   - condition 为 False 或没有匹配 → 忽略该 scope

**约束**：Strategy 的 flow 配置的最后一层**必须包含 `exchange_path` 和 `symbol` 变量**（通常是 `TradingPairScope`）。

## 7. Executor 的处理范围（当前实现）

Executor 直接消费 StrategyGroup 聚合后的目标输出（键为 `(exchange_path, symbol)`，值为 target vars），
不再遍历 Scope 树或祖先链。Scope vars 与 targets 的计算已在 Strategy 阶段完成。

Executor 仍会基于自身配置计算本地 vars/condition 并执行 `order`/`orders` 展开。

## 8. ScopeManager

ScopeManager 是 AppCore 的属性（全局单例），负责：

1. **get_or_create**：缓存 key 为 `(scope_class_id, scope_instance_id)`
2. **实例缓存**：按 key 复用 scope 实例（不维护 parent/children 拓扑；拓扑由 flow 执行时动态构建）
3. **缓存管理**：Scope 实例永久缓存（生命周期与 AppCore 相同）

**注意**：ScopeManager 继承自 Listener，参与缓存序列化；拓扑在加载时通过 flow 执行重建。

## 9. vars 字段语法

vars 支持条件变量：

```yaml
vars:
  - name: foo
    value: "mid_price * 0.5"
    on: "rsi < 30"              # 条件表达式（默认 True）
    initial_value: 0            # 初始值（条件从未满足时使用）
    post: false                 # 是否延后到第三遍计算（默认 false）
```

vars 也支持 dict/list[str] 等简化写法；为避免歧义与顺序问题，推荐标准格式。详见 [vars.md](vars.md)。

## 10. 常见误区（必须避免）

| 误区 | 正确做法 |
|------|----------|
| 在 strategy/executor 配置中写 `scopes:` | `scopes:` 只允许在 `conf/app/*.yaml` |
| 认为 `instance_id` 是 YAML 字段 | `instance_id` 是特殊变量，由 Scope 类的 `get_all_instance_ids` 方法生成 |
| 手动复制 parent vars 来模拟继承 | 使用 ChainMap，继承链由 flow 中的前驱节点（prev）决定 |
| 以为 Executor 还会遍历 Scope 树 | Executor 只处理 Strategy 输出的目标键，不再遍历 Scope 祖先链 |
| 误以为 not ready 自动级联 | 当前实现仅标记当前 scope；如需级联请在 flow 的 filter/condition 中处理 |

## 11. 相关文档

- [Scope 执行流程](scope-execution-flow.md)
- [vars 系统文档](vars.md)
- [Strategy 文档](strategy.md)
- [Executor 文档](executor.md)
- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
