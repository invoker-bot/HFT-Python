# Scope 系统执行流程（重构规则）

本文档描述 Scope 系统在 **AppCore / Strategy / Executor** 中每个 tick 的执行流程与计算边界。配置与术语请先阅读 [docs/scope.md](scope.md)。

## 1. 初始化阶段（AppCore）

### 1.1 加载配置

| 配置文件 | 加载内容 |
|----------|----------|
| `conf/app/<app>.yaml` | exchanges / strategy / executor 路径引用；**`scopes:` 字段** |
| `conf/exchange/**.yaml` | 交易所实例 |
| `conf/strategy/<strategy>.yaml` | `flow:`、`requires:`、`targets:` |
| `conf/executor/<executor>.yaml` | `vars:`、`scope:` |

### 1.2 构建 ScopeManager

- AppCore 初始化时构建一个全局 **ScopeManager**（生命周期与 AppCore 相同）
- ScopeManager 缓存语义：`(scope_class_id, scope_instance_id)` 全局唯一
- ScopeManager 继承自 Listener，参与缓存序列化；拓扑在加载时通过 flow 执行重建

### 1.3 Scope 类注册

内置 ScopeClass 在模块加载时会注册 `get_all_instance_ids` 方法，用于实例发现。

## 2. Strategy tick（Scope + target 输出）

Strategy 每个 tick 的输出分为两个阶段：**Flow 执行** → **target 输出**。

### 2.1 执行 Flow（按层级顺序）

输入来自 `conf/strategy/<strategy>.yaml`：

```yaml
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

**执行过程**：

```
对每个 flow 层级:
  1. 调用 ScopeClass.get_all_instance_ids(app_core) 获取实例列表
  2. 根据前一层的结果建立映射关系（通过 flow_mapper）
     - 一对一：相同 instance_id 直接映射
     - 一对多：通过 instance_id_map_func 映射
     - 多对一：多个前驱节点聚合到一个节点
  3. 对每个 instance_id:
     - ScopeManager.get_or_create(...) 获取/创建 scope 实例
     - 创建 FlowScopeNode（包含 scope + prev 列表）
     - 应用 filter（前验条件）：False 则跳过
     - 注入 Indicator 变量（requires）
     - 执行变量赋值（vars）
     - 应用 condition（后验条件）：False 则跳过
  4. 进入下一层，重复上述过程
  5. 返回最后一层的节点字典
```

每个 flow 配置独立执行，最终返回最后一层的 `{instance_id: FlowScopeNode}` 字典。

### 2.2 目标匹配与输出

Strategy 输出阶段：

```
output = {}

for node in last_layer_nodes:  # 最后一层的节点
    scope = node.scope
    exchange_path = scope.get_var("exchange_path")
    symbol = scope.get_var("symbol")
    if not exchange_path or not symbol:
        continue

    # 全局门控（可选）
    if strategy.condition:
        if not evaluate(strategy.condition, node.vars):
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
        condition_result = evaluate(matched_target.condition, node.vars)
        if not condition_result:
            continue  # condition 为 False，该 scope 被裁剪

    # 计算 target vars
    target_vars = {}
    for var_def in matched_target.vars:
        target_vars[var_def.name] = evaluate(var_def.value, node.vars)

    # 输出
    key = (exchange_path, symbol)
    output[key] = target_vars

return output
```

**关键约束**：
- Strategy 的 flow 配置的最后一层必须包含 `exchange_path` 和 `symbol` 变量
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
│  │  1. 执行 Flow（按层级顺序）                                           │   │
│  │     - 调用 get_all_instance_ids 获取实例                             │   │
│  │     - 建立前驱节点映射关系                                           │   │
│  │     - 创建 FlowScopeNode                                             │   │
│  │     - 应用 filter（前验）                                            │   │
│  │     - 注入 Indicator 变量                                            │   │
│  │     - 执行 vars 计算                                                 │   │
│  │     - 应用 condition（后验）                                         │   │
│  │                                                                      │   │
│  │  2. target 匹配与输出                                                │   │
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
│  │  3. orders 展开与执行                                                │   │
│  │     - 计算 order condition                                           │   │
│  │     - 展开 order_levels                                              │   │
│  │     - 创建/取消订单                                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 5. 性能优化要点

### 5.1 条件过滤

- **filter**（前验）：在执行 vars 之前过滤，减少不必要的计算
- **condition**（后验）：在执行 vars 之后过滤，用于基于计算结果的裁剪

### 5.2 裁剪

- Strategy：仅在最后一层节点上注入 Indicator 和匹配 target
- Executor：只处理 Strategy 返回的目标键

### 5.3 缓存

- ScopeManager 缓存 scope 实例（全局唯一）
- Indicator 内部缓存计算结果
- FlowScopeNode 使用 cached_property 缓存 ChainMap

## 6. 相关文档

- [Scope 系统](scope.md) - 配置边界、术语、缓存语义
- [vars 系统](vars.md) - 变量系统详细说明
- [Strategy 文档](strategy.md) - Strategy 配置与输出格式
- [Executor 文档](executor.md) - Executor 配置与下单逻辑
