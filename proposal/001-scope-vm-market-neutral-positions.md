# Proposal 001: Scope/VirtualMachine + MarketNeutralPositions

本提案定义一套基于 `simpleeval.safe_eval` 的数据驱动求值体系（VirtualMachine + Scope），用于在不同层级注入变量、统一计算顺序，并支撑后续策略/执行器以“声明式配置”的方式工作。

## 1. 背景与范围

- 旧策略 `KeepBalances` / `Arbitrage` 计划移除（其中 `Arbitrage` 需要重写）。
- 新策略：`MarketNeutralPositions`（对冲市场中性策略）。

该策略当前设想支持三类套利/对冲路径：
1. 现货-现货/合约套利：跨平台低价买入 -> 链上转账 -> 高价卖出，同时开等值空合约对冲。
2. 现货/合约套利：资金费率套利（文中称“资费率套利”）。
3. 合约/合约套利：跨平台合约价差/资费率差等。

## 2. 核心特性：Scope + VirtualMachine（VM）

数据驱动架构依赖 `simpleeval` 的 `safe_eval` 表达式。为了在不同运行层级（global / exchange / symbol ...）注入不同变量，并保证求值路径一致，引入：
- **全局 VirtualMachine**：统一负责 `safe_eval` 的执行环境与缓存。
- **Scope 系统**：用 `ChainMap` 组合多层变量域，支持“上游默认值 + 下游覆盖”的自然遮蔽规则。

### 2.1 术语表

- **scope node id**：`app conf` 中 `scopes:` 下的 key（例如 `global`、`exchange_class`）。这是“节点标识”，不是实例。
- **scope class**：scope 的 Python 类（例如 `GlobalScope` / `ExchangeScope`）。
- **scope instance id**：某个 scope node 在运行时产生的实例标识（例如某个 exchange instance、某个 symbol）。
- **scope instance**：运行时对象，键通常可视为 `(scope node id, scope instance id)`；会被 VM 缓存并复用。
- **parent / children**：求值时用于跨层级显式访问的两个特殊变量：
  - `parent`：当前实例在“当前 link 路径”上的上游实例（通常不常用，因为 `ChainMap` 已能访问父域；但需要显式拿到实例对象时可用）。
  - `children`：`{id: child_scope_instance}` 的字典（用于聚合/遍历下游实例）。

### 2.2 层级与“箭头逆转”的问题

典型层级可能是：
- `global -> exchange_class -> exchange_instance -> trading_pair_instance`
- `global -> exchange_class -> trading_pair_class -> trading_pair_instance`

不同策略/加载路径如果“逆转箭头”（上游/下游顺序不一致）会产生矛盾；因此需要显式的 **links** 来声明计算链路（见第 4 节）。

## 3. App 配置：Scope 图

App conf 示例：

```yaml
scopes:
  global:
    class: GlobalScope
    children: ["exchange_class"]
  exchange_class:
    class: ExchangeClassScope
    children: ["exchange", "trading_pair_class", "trading_pair_class_group"]
  exchange:
    class: ExchangeScope
    children: ["trading_pair"]
  trading_pair: ...
  trading_pair_class:
    class: TradingPairClassScope
    children: ["trading_pair"]
  trading_pair_class_group:
    class: TradingPairClassGroupScope
    children: ["trading_pair_class"]
```

约定：
- `scopes:` 下的 key 是 **scope node id**，其值通过 `class:` 绑定到一个 scope class。
- 同一个 scope class 可以被多个 node id 复用；同一个 node id 在运行时可以产生多个 scope instances（不同 `scope instance id`）。
- Scope 图允许出现“一个节点被多个上游引用”的情况（DAG）；运行时 `parent` 的具体取值由当前 `links` 路径决定。

## 4. Strategy 配置：过滤、依赖、links、计算 vars、输出 targets

Strategy conf 示例：

```yaml
# 过滤字段
include_symbols: ["..."]     # 默认为 ["*"]
exclude_symbols: ["..."]
exchanges: ["*"]             # "*" 表示 app 中定义的所有 exchanges，默认 ["*"]

# 依赖加载（datasource/indicator 的集合）
requires: ["medal_amount", "ticker", "fair_price"]
# - MedalAmountDataSource：汇总合约/现货账户的真实存量，形成标准 amount 字段
#   注入到 ExchangeScope（exchange instance level）
# - TickerDataSource：注入 mid_price 到 trading_pair_class scope
# - FairPriceIndicator：注入 trading_pair_std_price 到 trading_pair_class scope

# 显式声明"计算链路"
links:
  - ["global", "exchange_class", "exchange", "trading_pair_class_group", "trading_pair_class", "trading_pair"]
  - "..."

# 每个 scope node 的计算定义（vars 支持条件变量）
scopes:
  global:
    vars:
      max_trading_pair_groups: 10
      max_position_usd: 2000
      weights:
        okx/a: 0.1
        okx/b: 0.1

  trading_pair_class_group:
    vars:
      fair_price_min: min([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      fair_price_max: max([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      score: fair_price_max - fair_price_min
      ratio_est: sum([scope["ratio_est"] for scope in children.values()])
    group_condition: null  # 可选的组级过滤条件

  trading_pair_class:
    vars:
      delta_min_price: trading_pair_std_price - parent["fair_price_min"]
      delta_max_price: parent["fair_price_max"] - trading_pair_std_price
      ratio_est: sum([scope["ratio_est_instance"] for scope in children.values()])

  trading_pair:
    vars:
      weight: weights.get(exchange_path, 1.0)
      ratio_est_instance: weight * (parent.parent["fair_price_min"] * amount) / max_position_usd

# 输出定义
target_scope: trading_pair
target:
  vars:
    position_usd: ratio * max_position_usd
  condition: ratio != 0
```

字段语义：
- `vars`：对当前 scope instance 计算并写入变量（可被下游通过 `ChainMap` 读取）。支持条件变量（通过 `on` 字段）。
- `target.condition`：target 的特殊字段，用于决定 executor 是否对该 pair 执行后续逻辑。

**关键变量说明**：

1. **ratio_est 两阶段计算**：
   - **第一阶段**（trading_pair level）：`ratio_est_instance` = weight × (fair_price_min × amount) / max_position_usd
     - 计算每个 trading pair instance 的初始 ratio 估计值
     - 基于账户余额（amount）和权重（weight）
   - **第二阶段**（trading_pair_class level）：`ratio_est` = sum(children 的 ratio_est_instance)
     - 聚合所有 trading pair instances 的 ratio_est_instance
     - 这是一个**特殊字段**，用于后续 ratio 计算

2. **weight 变量**：
   - 从 global scope 的 `weights` 字典中获取
   - 格式：`weights.get(exchange_path, 1.0)`
   - 用于控制不同交易所的仓位权重

3. **trading_pair_std_price**：
   - 由 FairPriceIndicator 注入
   - 标准化价格（组内最小价格 = 1.0）
   - 可为 None（用于 mask 机制，排除不满足条件的交易对）

## 5. 计算流程：按每条 link 惰性建图、注入、求值、再做专用计算

对每条 `links[*]`，执行同样的流程：
1. **惰性初始化 scope instances 并缓存**：
   - 以 `(scope node id, scope instance id)` 为 key 创建/复用 scope instance，并缓存到 VM。
   - scope instance 的创建由 Strategy 决定；也可由 scope class 提供默认创建逻辑。
   - 例：`ExchangeClassScope` 可根据全局 exchange class + load marks 返回可交易 pairs，并创建 `trading_pair_class` 实例（其 `scope instance id` 可按 symbol 命名）。
   - 为支持更多 scope 类型，策略可继承 `BaseScope` 自定义创建方法；`TradingPairClassGroupScope` 是策略自定义 scope，不在标准实现范围内。
2. **indicator / datasource 注入**：把依赖产生的变量注入到对应 scope level（例：`ticker` 注入 `mid_price`；`medal_amount` 注入 `amount`）。
3. **沿 link 从前到后计算 `vars`**：每一层先计算其 `children` 的 vars，再计算本层 vars（包括条件变量）。
4. **进入专用策略计算流程**：例如下文的 `MarketNeutralPositions` group/ratio/筛选逻辑。
6. **输出 targets 给 executor**：最终把目标展开/传给 executor（传递到最底层 trading pair instance level scope 中）。

## 6. Executor 对接点：执行 scope 与订单字段

对接结论与字段：
- executor 的执行 scope：**trading pair instance level scope**（对每个真实账户的交易对实例进行订单操作/管理）。
- 新增字段示意：

```yaml
order_scope: trading_pair_class
order_levels: ...
order: ...
orders: ...
entry_order_scope: trading_pair_class
exit_order_scope: trading_pair_class
```

字段用途：
- `order_scope`：订单参数计算/展开所在的 scope level（不同 executor 可选择不同层级）。
- `order_levels`/`order(s)`：用于表达多档/分层订单的声明式结构（展开规则在订单 spec 中定义）。

## 7. MarketNeutralPositions 专用计算（运行在 trading_pair_class / group level）

### 7.1 运行层级与关键变量

以下变量运行在不同的 scope level：

**Global level**：
```yaml
max_trading_pair_groups: 10           # 最多返回的套利组数量
max_position_usd: 2000                # 每组最大仓位（USD）
entry_price_threshold: 0.001          # 开仓阈值（0.1%）
exit_price_threshold: 0.0005          # 平仓阈值（0.05%）
score_threshold: 0.001                # 最小 score 阈值
# 约束：entry_price_threshold > exit_price_threshold >= 0
```

**Trading_pair_class level**：
```yaml
trading_pair_std_price: <from FairPriceIndicator>  # 标准化价格，可为 None 用于 mask
delta_min_price: trading_pair_std_price - fair_price_min
delta_max_price: fair_price_max - trading_pair_std_price
ratio_est: sum([scope["ratio_est_instance"] for scope in children.values()])
```

**Trading_pair_class_group level**：
```yaml
fair_price_min: min([scope["trading_pair_std_price"] for scope in children.values()])
fair_price_max: max([scope["trading_pair_std_price"] for scope in children.values()])
score: fair_price_max - fair_price_min
ratio_est: sum([scope["ratio_est"] for scope in children.values()])
group_condition: null                 # 动态舍弃某些 pairs 的机制
```

### 7.2 计算流程

前置：先完成 links 链路上的 `vars` 计算（包括条件变量），然后进入本节流程。

1) 在 **trading_pair_class level**，按顺序计算：
   - `ratio_est -> trading_pair_std_price`
   - 剔除 `trading_pair_std_price is None` 的交易对（mask）。
   - 若 group 剩余 `len(group) == 0`：该 group 不参与后续计算，也不会传给 executor。
   - 重新计算 `ratio_est`（因为剔除后 children 变了）。
   - 对 `group_condition` 评估为 False 的 group：同样不参与后续计算。

2) 在 **trading_pair_class_group level**，对每个 group：
   - 取 `children` 中 `trading_pair_std_price` 最大/最小者：
     - `fair_price_min`
     - `fair_price_max`
   - `score = fair_price_max - fair_price_min`

3) 回到 **trading_pair_class level**，对每个 trading_pair_class：
   - 计算：
     - `delta_min_price = trading_pair_std_price - fair_price_min`
     - `delta_max_price = fair_price_max - trading_pair_std_price`
   - 定义方向变量：
     - `direction in {-1, 0, 1, null}`
     - `-1/1`：entry 方向（建议开仓）
     - `0`：exit（建议平仓）
     - `null`：hold（建议维持）
   - 若 `len(group) == 1`：`delta_min_direction = 0` 且 `delta_max_direction = 0`（rate 为 0）。
   - 若 `len(group) >= 2`：按阈值表决定 `delta_*_direction`：

| 条件（对 delta_min_price / delta_max_price 各自判断） | 结果 direction |
| --- | --- |
| `> entry_price_threshold` | `-1`（min side） / `1`（max side） |
| `> exit_price_threshold`  | `0` |
| else | `null` |

4) Group 选择（返回哪些 group 参与交易）：
   - 受 `max_trading_pair_groups` 限制，最多返回 N 个 group。
   - 优先选择两类 group：
     1. 在任意包含的 exchange 上，存在仓位的币种（合约 positions + 现货 balance）；其 group key 对应的 group 优先。
     2. 对所有 group：按 scope 排序（高->低），且 `score >= score_threshold`，直到达到 `max_trading_pair_groups` 或遍历完。

5) 在 **trading_pair_class level** 计算 `ratio`（每个 trading_pair_class 一个 ratio）：
   - 若 `len(group) <= 1`：`ratio = 0`
   - 若 `len(group) >= 2`：
     1. 第一遍：`ratio = clip(ratio_est, -1, 1)`
     2. 根据 `(delta_min_direction, delta_max_direction)` 的组合（最多 16 种）调整 ratio：

| (delta_min_direction, delta_max_direction) | ratio 调整规则 |
| --- | --- |
| (-1, -1) | raise |
| (-1, 0)  | `min(ratio, 0)` |
| (-1, 1)  | `ratio` |
| (-1, null) | `-1` |
| (0, -1) | raise |
| (0, 0)  | `ratio` |
| (0, 1)  | `max(ratio, 0)` |
| (0, null) | `min(ratio, 0)` |
| (1, -1) | raise |
| (1, 0)  | raise |
| (1, 1)  | raise |
| (1, null) | raise |
| (null, -1) | raise |
| (null, 0)  | `max(ratio, 0)` |
| (null, 1)  | `1` |
| (null, null) | `ratio` |

     3. 在 **trading_pair_class_group level** 进行“总和对齐为 0”：
        - 令 `S = sum(ratio_i)`。
        - 若 `S > 0`：选择 `trading_pair_std_price` 最大的那个 trading_pair_class，把它的 `ratio -= S`。
        - 若 `S < 0`：选择 `trading_pair_std_price` 最小的那个 trading_pair_class，把它的 `ratio -= S`（等价于 `ratio += abs(S)`）。
        - 结果：group 内 `sum(ratio_i) == 0`。
     4. 再做一次“极值差对齐”：令
        - `delta_ratio = (ratio(price_min) - ratio(price_max)) / 2 - 1`
        - `ratio(price_min) -= delta_ratio`
        - `ratio(price_max) += delta_ratio`
        - 结果：满足 `ratio(price_min) - ratio(price_max) == 2`。

6) 输出：
   - 根据选中的所有 group，展开得到 `target_pairs`。
   - 与 `target` 模板合并后，传回 executor。

---

## 8. FAQ（常见问题）

### Q1: Executor 的执行 scope 是哪个？

**A**: Trading pair instance level scope。

因为 executor 需要对每个真实账户的每个交易对实例执行订单操作和管理。

### Q2: Indicator 的 scope 是哪个？

**A**: 由 indicator 的特性决定。

- **指标类**（如 FairPriceIndicator）：通常处于 **trading_pair_class level scope**
- **数据源类**（如 MedalAmountDataSource）：通常处于 **exchange instance level scope**
- **方程类**（如 EquationDataSource）：可能处于 **exchange instance level scope**

### Q3: TradingPairClassGroupScope 处于哪个 level？

**A**: 它处于 **exchange_class level scope** 的下游。

层级关系：`exchange_class → trading_pair_class_group → trading_pair_class`

### Q4: ratio_est 为什么要计算两次？

**A**: 两次计算有不同的目的：

1. **第一次**（trading_pair level）：
   - 计算每个 trading pair instance 的初始 ratio 估计值
   - 基于账户余额和权重
   - 变量名：`ratio_est_instance`

2. **第二次**（trading_pair_class level）：
   - 在过滤掉 `trading_pair_std_price is None` 的交易对后
   - 重新聚合剩余的 trading pair instances
   - 变量名：`ratio_est`（特殊字段）

### Q5: 为什么需要 group_condition？

**A**: 提供组级别的动态过滤能力。

例如：
- 过滤掉资费率不满足阈值的组
- 过滤掉流动性不足的组
- 过滤掉价差过小的组

### Q6: trading_pair_std_price 和 mid_price 有什么区别？

**A**:
- **mid_price**：原始市场价格（由 TickerDataSource 注入）
- **trading_pair_std_price**：标准化价格（由 FairPriceIndicator 计算）
  - 组内最小价格标准化为 1.0
  - 其他价格按比例缩放
  - 用于跨交易所/跨币种的价格比较

---

## 9. 完整计算流程示例

假设有以下配置：

**交易对**：
- OKX: ETH/USDT (价格 2000 USD)
- Binance: ETH/USDT (价格 2010 USD)
- OKX: WBETH/USDT (价格 1990 USD)

**账户余额**：
- OKX: 1 ETH, 1 WBETH
- Binance: 1 ETH

**配置参数**：
- max_position_usd: 10000
- weights: {okx/main: 0.5, binance/spot: 0.5}
- entry_price_threshold: 0.002
- exit_price_threshold: 0.001

### 步骤 1: 标准化价格（FairPriceIndicator）

```
最小价格 = 1990 USD (WBETH/USDT)

标准化后：
- OKX ETH/USDT:     2000/1990 = 1.005
- Binance ETH/USDT: 2010/1990 = 1.010
- OKX WBETH/USDT:   1990/1990 = 1.000
```

### 步骤 2: 计算 ratio_est_instance（trading_pair level）

```python
# OKX ETH/USDT
weight = 0.5
amount = 1 ETH
ratio_est_instance = 0.5 * (1.0 * 1) / 10000 = 0.00005

# Binance ETH/USDT
weight = 0.5
amount = 1 ETH
ratio_est_instance = 0.5 * (1.0 * 1) / 10000 = 0.00005

# OKX WBETH/USDT
weight = 0.5
amount = 1 WBETH
ratio_est_instance = 0.5 * (1.0 * 1) / 10000 = 0.00005
```

### 步骤 3: 聚合 ratio_est（trading_pair_class level）

```python
# ETH/USDT (OKX)
ratio_est = 0.00005  # 只有一个 instance

# ETH/USDT (Binance)
ratio_est = 0.00005  # 只有一个 instance

# WBETH/USDT (OKX)
ratio_est = 0.00005  # 只有一个 instance
```

### 步骤 4: 计算 group level 变量

```python
# ETH group (包含 ETH/USDT 和 WBETH/USDT)
fair_price_min = 1.000  # WBETH/USDT
fair_price_max = 1.010  # Binance ETH/USDT
score = 1.010 - 1.000 = 0.010
```

### 步骤 5: 计算 delta 和 direction

```python
# OKX ETH/USDT
delta_min_price = 1.005 - 1.000 = 0.005
delta_max_price = 1.010 - 1.005 = 0.005
# delta_min_price (0.005) > entry_price_threshold (0.002) → delta_min_direction = -1
# delta_max_price (0.005) > entry_price_threshold (0.002) → delta_max_direction = 1

# Binance ETH/USDT
delta_min_price = 1.010 - 1.000 = 0.010
delta_max_price = 1.010 - 1.010 = 0.000
# delta_min_price (0.010) > entry_price_threshold (0.002) → delta_min_direction = -1
# delta_max_price (0.000) < exit_price_threshold (0.001) → delta_max_direction = null

# OKX WBETH/USDT
delta_min_price = 1.000 - 1.000 = 0.000
delta_max_price = 1.010 - 1.000 = 0.010
# delta_min_price (0.000) < exit_price_threshold (0.001) → delta_min_direction = null
# delta_max_price (0.010) > entry_price_threshold (0.002) → delta_max_direction = 1
```

### 步骤 6: 计算并平衡 ratio

```python
# 初始 ratio = clip(ratio_est, -1, 1)
# 根据 direction 调整：
# OKX ETH/USDT: (-1, 1) → ratio 不变
# Binance ETH/USDT: (-1, null) → ratio = -1
# OKX WBETH/USDT: (null, 1) → ratio = 1

# 平衡总和为 0：
# sum = ratio(OKX ETH) + ratio(Binance ETH) + ratio(OKX WBETH)
#     = 0.00005 + (-1) + 1 = 0.00005
# 调整最高价（Binance ETH）：ratio -= 0.00005 → ratio = -1.00005

# 平衡对冲条件：
# ratio(price_min) - ratio(price_max) = 2
# ratio(WBETH) - ratio(Binance ETH) = 1 - (-1.00005) = 2.00005
# delta_ratio = (2.00005 / 2) - 1 = 0.000025
# ratio(WBETH) -= 0.000025 → 0.999975
# ratio(Binance ETH) += 0.000025 → -0.999975
```

### 步骤 7: 输出 targets

```python
{
    ("okx/main", "ETH/USDT"): {
        "position_usd": 0.00005 * 10000 = 0.5,
        "ratio": 0.00005,
    },
    ("binance/spot", "ETH/USDT"): {
        "position_usd": -0.999975 * 10000 = -9999.75,
        "ratio": -0.999975,
    },
    ("okx/main", "WBETH/USDT"): {
        "position_usd": 0.999975 * 10000 = 9999.75,
        "ratio": 0.999975,
    },
}
```

**验证**：
- 总和：0.00005 + (-0.999975) + 0.999975 = 0 ✓（市场中性）
- 对冲条件：0.999975 - (-0.999975) = 1.99995 ≈ 2 ✓

---

## 10. 相关文档

- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
- [Feature 0013: MarketNeutralPositions 策略](../features/0013-market-neutral-positions-strategy.md)
- [Example 004: MarketNeutralPositions 配置详解](../examples/004-market-neutral-positions-strategy.md)
