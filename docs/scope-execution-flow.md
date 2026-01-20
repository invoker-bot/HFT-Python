# Scope 系统执行流程（重构规则）

本文档描述 Scope 系统在 **AppCore / Strategy / Executor** 中每个 tick 的执行流程与计算边界。配置与术语请先阅读 [docs/scope.md](scope.md)。

## 1. 初始化阶段（AppCore）

### 1.1 加载配置

| 配置文件 | 加载内容 |
|----------|----------|
| `conf/app/<app>.yaml` | exchanges / strategy / executor 路径引用；**`scopes:` 字段** |
| `conf/exchange/**.yaml` | 交易所实例 |
| `conf/strategy/<strategy>.yaml` | `links:`、`requires:`、`targets:` |
| `conf/executor/<executor>.yaml` | `vars:`、`scope:` |

### 1.2 构建 ScopeManager

- AppCore 初始化时构建一个全局 **ScopeManager**（生命周期与 AppCore 相同）
- ScopeManager 缓存语义：`(scope_class_id, scope_instance_id)` 全局唯一
- ScopeManager **不可 pickle**

### 1.3 注册 `get_all_instance_ids`

内置 ScopeClass 在模块加载时自动注册到全局字典；自定义 Scope 可通过装饰器注册。

## 2. Strategy tick（Scope + target 输出）

Strategy 每个 tick 的输出分为三个阶段：**LinkTree 构建** → **三遍计算** → **target 输出**。

### 2.1 构建 LinkTree（按 links 顺序）

输入来自 `conf/strategy/<strategy>.yaml`：

```yaml
links:
  - id: link_main
    value: [g, exchange_class, exchange, trading_pair]
```

**构建过程**：

```
对每条 link:
  1. 从 root 开始（第一个 scope_class_id，如 "g"）
  2. 调用 get_all_instance_ids(app_core, parent_scope) 获取实例列表
  3. Strategy 侧 filter（当前仅支持 include_symbols/exclude_symbols；exchange 选择由 AppConfig.exchanges 控制）
  4. 对每个 (scope_class_id, scope_instance_id):
     - ScopeManager.get_or_create(...) 获取/创建 scope 实例
     - LinkTree 负责绑定 parent/children（ScopeManager 不维护拓扑）
     - 注入特殊变量（instance_id, exchange_id, symbol 等）
  5. 递归处理下一层 scope
  6. 得到该 link 的 LinkTree（叶子节点为 TradingPairScope）
```

每个 link **独立一棵 LinkTree**。

### 2.2 每条 link 的三遍计算

对同一条 link，按以下三遍顺序执行（广度优先 + 去重）：

#### 第一遍：requires（Indicator 注入）

```
for indicator_id in strategy.requires:
    indicator = app_core.get_indicator(indicator_id)
    target_scope_class = indicator.scope_class  # Indicator 声明注入的 ScopeClass

    for scope in link_tree.get_all_scopes_of_class(target_scope_class):
        if scope not in computed_set and scope not in not_ready_set:
            result = app_core.query_indicator(indicator_id, scope.vars)

            if result is None or not result.is_ready:
                # Indicator not ready → 该 scope 及其所有 children 标记为 not ready
                scope.mark_not_ready()
                not_ready_set.add(scope)
                not_ready_set.update(scope.get_all_descendants())
            else:
                scope.update_vars(result.vars)
                computed_set.add(scope)
```

**级联 not ready 机制**：

如果某个 scope 的 Indicator **not ready**（数据未就绪），则：
- 该 scope 标记为 not ready
- 该 scope 的**所有 children scope 实例**也自动标记为 not ready
- not ready 的 scope 不参与后续的 vars 计算和 target 匹配
- 效果等同于该分支被整体裁剪

#### 第二遍：计算 `post: false` 的 vars

```
for scope in link_tree.breadth_first_traversal():
    if scope not in computed_set and scope not in not_ready_set:
        for var_def in scope.config.vars:
            if not var_def.post:  # 默认 false
                value = evaluate(var_def.value, scope.vars)
                scope.set_var(var_def.name, value)
        computed_set.add(scope)
```

#### 第三遍：计算 `post: true` 的 vars

```
for scope in link_tree.breadth_first_traversal():
    if scope not in computed_set and scope not in not_ready_set:
        for var_def in scope.config.vars:
            if var_def.post:
                value = evaluate(var_def.value, scope.vars)
                scope.set_var(var_def.name, value)
        computed_set.add(scope)
```

**用途**：`post: true` 用于需要访问 children 已计算完的 vars 的聚合表达式。

### 2.3 目标匹配与输出

Strategy 输出阶段：

```
output = {}

for trading_pair_scope in last_link.leaf_scopes:  # 必须是 TradingPairScope
    # 全局门控（可选）：在每个 TradingPairScope 上求值；
    # False/异常 → 跳过该 scope（等价于该 scope 不输出目标）
    if strategy.condition:
        if not evaluate(strategy.condition, trading_pair_scope.vars):
            continue
    # 跳过 not ready 的 scope
    if trading_pair_scope in not_ready_set:
        continue

    # 贪婪匹配：从前往后扫描 targets，取第一个匹配的
    matched_target = None
    for target in strategy.targets:
        if matches(target.exchange_id, trading_pair_scope.exchange_id) and \
           matches(target.symbol, trading_pair_scope.symbol):
            matched_target = target
            break

    if matched_target is None:
        continue  # 没有匹配，该 scope 被裁剪

    # 计算 condition
    if matched_target.condition:
        condition_result = evaluate(matched_target.condition, trading_pair_scope.vars)
        if not condition_result:
            continue  # condition 为 False，该 scope 被裁剪

    # 计算 target vars
    target_vars = {}
    for var_def in matched_target.vars:
        target_vars[var_def.name] = evaluate(var_def.value, trading_pair_scope.vars)

    # 输出
    key = (trading_pair_scope.exchange_id, trading_pair_scope.symbol)
    output[key] = {
        "scope": trading_pair_scope,  # 传递整个 scope 给 Executor
        **target_vars
    }

return output
```

**关键约束**：
- Strategy 的最后一条 link 的最后一个 scope 必须是 `TradingPairScope`
- 贪婪匹配：只取第一个匹配的 target
- 没有匹配或 condition 为 False → 该 scope 被裁剪（不执行）

## 3. Executor tick（只算必要 scope）

Executor 每个 tick 只对 Strategy 返回的目标执行计算与下单。

### 3.1 输入

Executor 输入为 Strategy 输出的目标集合，每个目标包含：
- `TradingPairScope` 实例
- target vars（已计算）

### 3.2 计算范围裁剪（节约资源）

**核心原则**：Executor 只计算 Strategy 返回的 `TradingPairScope` **及其祖先链**，不对其他分支做任何计算。

示意：

```
a0 -> b0 -> c0
 |     |-> c1
 |-> b1 -> c2
```

若 Strategy 只返回 `c2`，则 Executor 只处理 `(a0, b1, c2)` 这条祖先链。

**不计算的节点**（c0, c1, b0）：
- 不调用 `query_indicator`
- 不计算 vars
- 不占用任何资源

### 3.3 Executor 的三遍计算

与 Strategy 类似，但**范围限定在祖先链**：

```
for target in strategy_output.values():
    trading_pair_scope = target["scope"]
    ancestor_chain = [trading_pair_scope]

    # 收集祖先链
    current = trading_pair_scope.parent
    while current:
        ancestor_chain.insert(0, current)
        current = current.parent

    # 第一遍：requires（Indicator 注入）
    for scope in ancestor_chain:
        if scope not in computed_set:
            for indicator_id in executor.requires:
                vars = app_core.query_indicator(indicator_id, scope.vars)
                scope.update_vars(vars)
            computed_set.add(scope)

    # 第二遍：post=false 的 vars（只在 TradingPairScope 上计算）
    for var_def in executor.config.vars:
        if not var_def.post:
            value = evaluate(var_def.value, trading_pair_scope.vars)
            trading_pair_scope.set_var(var_def.name, value)

    # 第三遍：post=true 的 vars（只在 TradingPairScope 上计算）
    for var_def in executor.config.vars:
        if var_def.post:
            value = evaluate(var_def.value, trading_pair_scope.vars)
            trading_pair_scope.set_var(var_def.name, value)
```

**注意**：Executor 的 vars 只在 `TradingPairScope` 上计算（最后一层），因此 `post` 参数实际上无影响。

### 3.4 下单展开（Executor 内部）

Executor 在完成 vars 计算后：

1. 计算 Executor 的本地 vars/condition
2. 展开 orders（如 `order_levels` 展开得到 `level` 局部变量）
3. 创建/取消订单

```yaml
# conf/executor/<executor>.yaml
vars:
  - name: delta_usd
    value: "position_usd - current_position_usd"
  - name: order_amount
    value: "delta_usd / mid_price"

order:
  condition: "abs(delta_usd) > 10"
  order_amount: "order_amount"
  spread: "0.0002 * mid_price"
```

## 4. 完整时序图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              每个 tick 的执行流程                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  AppCore.tick()                                                             │
│       │                                                                     │
│       ▼                                                                     │
│  StrategyGroup.tick()（当前仅管理单条 Strategy）                              │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Strategy.tick()                                                     │   │
│  │                                                                      │   │
│  │  1. 构建 LinkTree（按 links 顺序）                                   │   │
│  │     - get_all_instance_ids → filter → get_or_create                 │   │
│  │     - 每个 link 独立一棵树                                           │   │
│  │                                                                      │   │
│  │  2. 三遍计算（广度优先 + 去重）                                      │   │
│  │     - 第一遍：requires（Indicator 注入）                             │   │
│  │     - 第二遍：post=false 的 vars                                     │   │
│  │     - 第三遍：post=true 的 vars                                      │   │
│  │                                                                      │   │
│  │  3. target 匹配与输出                                                │   │
│  │     - 贪婪匹配第一个符合的 target                                    │   │
│  │     - 计算 condition，False 则裁剪                                   │   │
│  │     - 计算 target vars，收集到输出                                   │   │
│  │     - 输出：{(exchange_id, symbol): {scope, vars...}}               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│       │                                                                     │
│       ▼                                                                     │
│  ExecutorGroup.tick()                                                       │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Executor.tick()                                                     │   │
│  │                                                                      │   │
│  │  1. 接收 Strategy 输出的 TradingPairScope 集合                       │   │
│  │                                                                      │   │
│  │  2. 计算范围裁剪                                                     │   │
│  │     - 只处理返回的 scope 及其祖先链                                  │   │
│  │     - 其他分支不计算                                                 │   │
│  │                                                                      │   │
│  │  3. 三遍计算（只在祖先链上）                                         │   │
│  │     - 第一遍：requires（Indicator 注入）                             │   │
│  │     - 第二遍：executor vars（只在 TradingPairScope）                 │   │
│  │     - 第三遍：executor vars（post=true，实际无影响）                 │   │
│  │                                                                      │   │
│  │  4. orders 展开与执行                                                │   │
│  │     - 计算 order condition                                           │   │
│  │     - 展开 order_levels                                              │   │
│  │     - 创建/取消订单                                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 5. 性能优化要点

### 5.1 去重

- 同一 scope 在同一遍中只计算一次
- 使用 `computed_set` 追踪已计算的 scope

### 5.2 裁剪

- Strategy：只对被 target 激活的 scope 调用 `query_indicator`
- Executor：只处理 Strategy 返回的 scope 及其祖先链

### 5.3 缓存

- ScopeManager 缓存 scope 实例（全局唯一）
- Indicator 内部缓存计算结果

## 6. 相关文档

- [Scope 系统](scope.md) - 配置边界、术语、缓存语义
- [vars 系统](vars.md) - 变量系统详细说明
- [Strategy 文档](strategy.md) - Strategy 配置与输出格式
- [Executor 文档](executor.md) - Executor 配置与下单逻辑
