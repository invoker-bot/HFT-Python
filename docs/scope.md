# Scope 系统（重构规则）

本文档定义 Scope 系统的**配置边界**、**实例发现**、**缓存语义**、**变量继承（ChainMap）**与**执行流程**。任何与本文档不一致的行为应视为实现缺陷。

## 1. 配置边界（必须严格遵守）

Scope 相关配置分布在不同的配置文件中，**同名字段在不同文件里含义不同**，不可混用：

| 配置文件 | 允许的 Scope 字段 | 说明 |
|----------|------------------|------|
| `conf/app/*.yaml` | `scopes:` | **唯一允许声明 `scopes:` 的地方**（声明 scope 类型、类、初值） |
| `conf/strategy/*.yaml` | `links:`, `requires:`, `targets:` | 策略逻辑；**不允许声明 `scopes:`** |
| `conf/executor/*.yaml` | `vars:`, `scope:` | 消费 Strategy 返回的 Scope；**不声明 Scope 节点** |
| `conf/exchange/*.yaml` | 无 | 交易所配置，不包含 Scope 系统配置 |

## 2. 术语与不变量

### 2.1 Scope 的两类"ID"

| 术语 | 来源 | 示例 | 说明 |
|------|------|------|------|
| `scope_class_id` | 用户在 `conf/app/*.yaml` 的 `scopes` 字段 key 中定义 | `g`, `exchange`, `trading_pair` | 用户命名的 scope 标识符；在 `links` 中引用它 |
| `scope_instance_id` | 由 ScopeClass 的 `get_all_instance_ids` 在运行时生成 | `global`, `okx/a`, `okx/a-ETH/USDT` | Scope 实例 ID |

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

- 继承链由 `links` 中该节点的**顺序**决定
- 更靠后的 scope（更"内层"）覆盖更靠前的 scope（更"外层"）

示例 link：`["g", "exchange_class", "exchange", "trading_pair"]`

则 `trading_pair` 节点的查找顺序为：

```python
ChainMap(trading_pair_vars, exchange_vars, exchange_class_vars, g_vars)
```

### 2.5 特殊变量（由 Scope 系统自动提供）

所有 Scope 都拥有特殊变量 `instance_id`（等于 `scope_instance_id`）。

各 ScopeClass 的额外特殊变量：

| ScopeClass | 特殊变量 | 说明 |
|------------|----------|------|
| `GlobalScope` | `app_core` | AppCore 实例引用 |
| `ExchangeClassScope` | `exchange_class` | 交易所类名（如 `"okx"`, `"binance"`） |
| `ExchangeScope` | `exchange_id`, `exchange` | exchange path（如 `"okx/a"`）及 exchange 实例 |
| `TradingPairClassScope` | `symbol`, `exchange_class` | 交易对符号及继承的 exchange_class |
| `TradingPairScope` | `exchange_id`, `symbol` | exchange path 及交易对符号 |

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
      - exchange_weight=weights.get(exchange_id, 0.5)

  trading_pair:
    class: TradingPairScope
    vars:
      - target_position=max_position_usd * exchange_weight
```

**说明**：
- `g` 只是用户取的名字；Scope 的 `instance_id` 由 ScopeClass 的 `get_all_instance_ids` 决定
- `vars` 表示 scope 创建时的初始变量（strategy/executor 中同名 vars 会覆盖）
- 不支持 `group_condition` 字段（出现即视为配置错误）；如需过滤 group，请在 vars 中计算布尔量（如 `group_enabled`）并在 leaf `targets[*].condition` 中引用

### 3.1 实例发现：`get_all_instance_ids`

每条 parent→child 的实例展开由 ScopeClass 通过注册机制提供：

```python
# 注册在全局字典中，签名：
get_all_instance_ids(app_core, parent_scope_instance, ...) -> list[str]
```

支持自定义装饰器：

```python
@register_get_all_instance_ids(ParentScopeClass, ScopeClass)
def my_instance_ids(app_core, parent_scope):
    return [...]
```

### 3.2 内置标准展开（默认）

| (ParentClass, ScopeClass) | 返回示例 | 由什么决定 |
|---------------------------|----------|-----------|
| `(None, GlobalScope)` | `["global"]` | 固定返回 1 个 |
| `(GlobalScope, ExchangeClassScope)` | `["okx", "binance"]` | `app_core.exchanges` 的 groups |
| `(ExchangeClassScope, ExchangeScope)` | `["okx/a", "okx/b"]` | `app_core` 中该类型的 exchange 实例 |
| `(ExchangeScope, TradingPairScope)` | `["okx/a-ETH/USDT", "okx/b-BTC/USDT:USDT"]` | exchange 实例支持的 symbols |
| `(ExchangeClassScope, TradingPairClassScope)` | `["okx-ETH/USDT"]` | exchange class 下所有 symbols（去重） |
| `(TradingPairClassScope, TradingPairScope)` | `["okx/a-ETH/USDT", "okx/b-BTC/USDT:USDT"]` | 该 symbol 在各 exchange 实例的具体交易对 |

**两条路径**：

```
路径1: GlobalScope → ExchangeClassScope → ExchangeScope → TradingPairScope
路径2: GlobalScope → ExchangeClassScope → TradingPairClassScope → TradingPairScope
```

## 4. StrategyConfig: `links` 与 LinkTree

Strategy 的 `links` 定义 Scope 的计算拓扑。每个 link 独立形成一棵 `LinkTree`。

```yaml
# conf/strategy/<strategy>.yaml
links:
  - id: link_main
    value:
      - g
      - exchange_class
      - exchange
      - trading_pair
```

### 4.1 LinkTree 构建过程

1. 从 root 开始（第一个 scope_class_id）
2. 对每个 parent_scope，调用 `get_all_instance_ids(app_core, parent_scope)`
3. Strategy 侧 filter（当前仅支持 `include_symbols`/`exclude_symbols`；exchange 选择由 AppConfig.exchanges 控制）
4. 对每个 `(scope_class_id, scope_instance_id)`，调用 `ScopeManager.get_or_create(...)`
5. 根据前后顺序构建 ChainMap，注入 `parent` 和 `children`

### 4.2 LinkNode

LinkNode 存储 `(scope_class_id, scope_instance_id)` 对，用于标识 LinkTree 中的节点。

### 4.3 Filter 机制

Strategy 配置中的 filter：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `include_symbols` | `["*"]` | 包含的 symbol 列表 |
| `exclude_symbols` | `[]` | 排除的 symbol 列表 |

**注意**：当前 filter 仅对 symbol 进行过滤；exchange 维度由 AppConfig 的 `exchanges` 选择器决定。

## 5. 每个 tick 的三遍计算（Strategy）

对每条 link（LinkTree），每个 tick 的计算有**三遍**：

### 5.1 第一遍：requires（Indicator 注入）

对 `requires` 中声明的每个 Indicator：

1. 找到该 Indicator 注入的 ScopeClass（由 Indicator 自己声明）
2. 对 LinkTree 中所有该 ScopeClass 的 scope 实例：
   - 调用 `app_core.query_indicator(scope_vars)`
   - 将返回的变量写入该 scope 的变量空间

**级联 not ready 机制**：

如果某个 scope 的 Indicator **not ready**（数据未就绪），则：
- 该 scope 标记为 not ready
- 该 scope 的**所有 children scope 实例**也自动标记为 not ready
- not ready 的 scope 不参与后续的 vars 计算和 target 匹配
- 效果等同于该分支被整体裁剪

### 5.2 第二遍：计算 `post: false` 的 vars

从前到后遍历 link 的 scope 链，计算所有 `post: false`（默认）的 vars。

```yaml
vars:
  - name: foo
    value: "mid_price * 0.5"
    post: false  # 默认值，第二遍计算
```

### 5.3 第三遍：计算 `post: true` 的 vars

从前到后遍历 link 的 scope 链，计算所有 `post: true` 的 vars。

```yaml
vars:
  - name: aggregated_amount
    value: "sum(child_values(children, 'amount'))"
    post: true  # 第三遍计算（此时 children 的 vars 已计算完毕）
```

### 5.4 去重规则（性能关键）

- 广度优先 + 去重
- **同一个 scope 实例在同一遍里只计算一次**（即使被多个 child 触达）

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

每个 `TradingPairScope` 从前往后扫描 `targets` 列表，**贪婪匹配**：

1. 匹配到第一个符合的 target → 使用该 target 的 vars 和 condition
2. 如果没有匹配到任何 target → 该 scope 不执行（被裁剪掉）

### 6.3 target 输出

Strategy 输出阶段：

1. 基于 links 的计算结果得到"被选中的 groups"
2. 展开得到 concrete 的 `target_pairs`（不再包含 `*`）
3. 若 `strategy.condition` 不为空：在该 TradingPairScope 上求值；False/异常则忽略该 scope
4. 对每个 TradingPairScope：
   - 匹配 target，计算 condition
   - condition 为 True → 计算 target vars，收集到输出
   - condition 为 False 或没有匹配 → 忽略该 scope

**约束**：Strategy 的最后一条 link 的最后一个 scope **必须是 `TradingPairScope`**。

## 7. Executor 的计算范围裁剪（节约资源）

Executor 只处理 Strategy 返回的 `TradingPairScope`（及其祖先），不会对整棵 LinkTree 重复计算。

### 7.1 计算范围

```text
a0 -> b0 -> c0
 |     |-> c1
 |-> b1 -> c2
```

若 Strategy 只返回 `c2`，则 Executor 只处理 `(a0, b1, c2)` 这条祖先链。

### 7.2 Executor 的三遍计算

与 Strategy 类似，但**只在 Strategy 返回的 TradingPairScope 及其祖先上执行**：

1. **第一遍**：requires（Indicator 注入）—— 只对涉及的 scope 调用 `query_indicator`
2. **第二遍**：计算 `post: false` 的 vars
3. **第三遍**：计算 `post: true` 的 vars

### 7.3 Executor vars 计算范围

Executor config 中定义的 vars **只在 TradingPairScope 上计算**（与 Strategy 返回的 target scope 对应）。

因为只有一层，所以 executor 中的 `post` 参数实际上无影响。

### 7.4 orders 展开

Executor 在完成 vars 计算后，基于 TradingPairScope 进行 `order`/`orders` 展开。

## 8. ScopeManager

ScopeManager 是 AppCore 的属性（全局单例），负责：

1. **get_or_create**：缓存 key 为 `(scope_class_id, scope_instance_id)`
2. **实例缓存**：按 key 复用 scope 实例（不维护 parent/children 拓扑；拓扑由 links 构建 LinkTree 时挂接）
3. **缓存管理**：Scope 实例永久缓存（生命周期与 AppCore 相同）

**注意**：ScopeManager 不可 pickle（不包含在缓存中）。

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
| 认为 `instance_id` 是 YAML 字段 | `instance_id` 是特殊变量，由 `get_all_instance_ids` 生成 |
| 手动复制 parent vars 来模拟继承 | 使用 ChainMap，继承链由 links 顺序定义 |
| 同一 scope 在同一遍中重复计算 | 广度优先 + 去重 |
| Executor 计算整棵 LinkTree | Executor 只计算 Strategy 返回的 scope 及其祖先 |
| 忽略 Indicator not ready 的级联影响 | not ready 的 scope 及其所有 children 都会被裁剪 |

## 11. 相关文档

- [Scope 执行流程](scope-execution-flow.md)
- [vars 系统文档](vars.md)
- [Strategy 文档](strategy.md)
- [Executor 文档](executor.md)
- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
