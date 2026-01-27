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
- ScopeManager 继承自 Listener，参与缓存序列化；children 拓扑在加载时通过 `get_or_create` 重建

### 1.3 实例发现注册器（当前未接入 Strategy）

内置 ScopeClass 在模块加载时会注册实例发现函数，但当前 Strategy 构建树时不直接使用注册器；
如需使用需在 Strategy 层自行接入。

## 2. Strategy tick（Scope + target 输出）

Strategy 每个 tick 的输出分为三个阶段：**LinkedScopeTree 构建** → **三遍计算** → **target 输出**。

### 2.1 构建 LinkedScopeTree（按 links 顺序）

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
  2. 调用 BaseStrategy._get_instance_ids(scope_class_id, parent_node) 获取实例列表
  3. filter 由 _get_filtered_symbols 完成（include/exclude）；exchange 选择由 AppConfig.exchanges 控制
  4. 对每个 (scope_class_id, scope_instance_id):
     - ScopeManager.get_or_create(...) 获取/创建 scope 实例
     - LinkedScopeNode 负责绑定 parent/children（ScopeManager 不维护拓扑）
     - 特殊变量由 Scope.initialize 设置（instance_id / class_id / app_core 等）
  5. 递归处理下一层 scope
  6. 得到该 link 的 LinkedScopeTree（叶子节点通常为 TradingPairScope）
```

每个 link **独立一棵 LinkedScopeTree**。

### 2.2 每条 link 的三遍计算

对同一条 link，按以下三遍顺序执行（广度优先 + 去重）：

#### 第一遍：requires（Indicator 注入）

```
for node in all_nodes:
    if node.scope.not_ready:
        continue
    # 对当前节点注入 requires（内部会按 exchange_path/symbol 定位 indicator）
    inject_indicator_vars(node.scope)
```

**级联 not ready 机制**：

如果某个 scope 的 Indicator **not ready**（数据未就绪），则：
- 仅标记当前 scope 为 not ready
- not ready 的 scope 不参与后续的 vars 计算和 target 匹配
- 如需级联裁剪，需显式调用 `LinkedScopeTree.mark_not_ready(...)`

#### 第二遍：计算 `post: false` 的 vars

```
for node in all_nodes:
    node_id = id(node)
    if node.scope.not_ready or node_id in computed_set:
        continue
    compute_scope_vars(node.scope, post=False, node=node, tree=tree)
    computed_set.add(node_id)
```

#### 第三遍：计算 `post: true` 的 vars

```
for node in all_nodes:
    node_id = id(node)
    if node.scope.not_ready or node_id in computed_set:
        continue
    compute_scope_vars(node.scope, post=True, node=node, tree=tree)
    computed_set.add(node_id)
```

**用途**：`post: true` 用于需要访问 children 已计算完的 vars 的聚合表达式。

### 2.3 目标匹配与输出

Strategy 输出阶段：

```
output = {}

for node in leaf_nodes:  # 叶子节点通常为 TradingPairScope
    scope = node.scope
    exchange_path = scope.get_var("exchange_id") or scope.get_var("exchange_path")
    symbol = scope.get_var("symbol")
    if not exchange_path or not symbol:
        continue
    # 全局门控（可选）：在每个 TradingPairScope 上求值；
    # False/异常 → 跳过该 scope（等价于该 scope 不输出目标）
    if strategy.condition:
        if not evaluate(strategy.condition, tree.get_vars(node)):
            continue
    # 跳过 not ready 的 scope
    if node.scope.not_ready:
        continue

    # 贪婪匹配：从前往后扫描 targets，取第一个匹配的
    matched_target = None
    for target in strategy.targets:
        if matches(target.exchange_id, exchange_path) and \
           matches(target.symbol, symbol):
            matched_target = target
            break

    if matched_target is None:
        continue  # 没有匹配，该 scope 被裁剪

    # 计算 condition
    if matched_target.condition:
        condition_result = evaluate(matched_target.condition, tree.get_vars(node))
        if not condition_result:
            continue  # condition 为 False，该 scope 被裁剪

    # 计算 target vars
    target_vars = {}
    for var_def in matched_target.vars:
        target_vars[var_def.name] = evaluate(var_def.value, tree.get_vars(node))

    # 输出
    key = (exchange_path, symbol)
    output[key] = target_vars

return output
```

**关键约束**：
- Strategy 的最后一条 link 的最后一个 scope 必须是 `TradingPairScope`
- 贪婪匹配：只取第一个匹配的 target
- 没有匹配或 condition 为 False → 该 scope 被裁剪（不执行）

## 3. Executor tick（只算必要 scope）

Executor 每个 tick 只对 Strategy 返回的目标执行计算与下单（当前不再遍历 Scope 树）。

### 3.1 输入

Executor 输入为 Strategy 输出的目标集合：
- key: `(exchange_path, symbol)`
- value: target vars（已计算）

### 3.2 计算范围裁剪（节约资源）

**核心原则**：Executor 只处理 Strategy 返回的目标键，不再遍历 Scope 祖先链。

### 3.3 Executor 的三遍计算

Executor 不再做 Scope 级的三遍计算；它直接使用 Strategy 聚合后的 target vars 执行下单逻辑。

**注意**：Executor 的 vars 来自自身配置与 Strategy 输出聚合，Scope vars 在 Strategy 阶段已经计算完成。

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
│  │  1. 构建 LinkedScopeTree（按 links 顺序）                            │   │
│  │     - _get_instance_ids → filter → get_or_create                    │   │
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
│  │  1. 接收 Strategy 输出的目标集合                                     │   │
│  │                                                                      │   │
│  │  2. 仅处理目标集合（不遍历 Scope 祖先链）                             │   │
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

- Strategy：仅在含 `exchange_path` / `symbol` 的 scope 上注入 Indicator
- Executor：只处理 Strategy 返回的目标键

### 5.3 缓存

- ScopeManager 缓存 scope 实例（全局唯一）
- Indicator 内部缓存计算结果

## 6. 相关文档

- [Scope 系统](scope.md) - 配置边界、术语、缓存语义
- [vars 系统](vars.md) - 变量系统详细说明
- [Strategy 文档](strategy.md) - Strategy 配置与输出格式
- [Executor 文档](executor.md) - Executor 配置与下单逻辑
