# Proposal 002: MarketNeutralPositions 策略

## 1. 背景与动机

### 1.1 当前问题

旧的 `ArbitrageStrategy` 存在以下问题：
- 缺乏统一的分组机制
- 没有 Fair Price 概念
- Ratio 平衡逻辑不清晰
- 无法支持复杂的多层级计算

### 1.2 设计目标

实现 **MarketNeutralPositions** 策略，这是一个市场中性对冲策略，通过在不同交易所/交易对之间建立对冲仓位来捕获价差套利机会。

**核心目标**：
- 保持 `ratio` 总和为 0（市场中性）
- 支持三种套利模式
- 基于 Scope 系统实现多层级计算

**策略特性**：
1. **Trading Pair 分组**：按 `group_id` 分组（如 ETH/USDT → ETH）
2. **Fair Price 计算**：通过 `FairPriceIndicator` 计算公平价格
3. **Direction 计算**：自动计算开仓/平仓/持仓方向
4. **Ratio 平衡**：确保组内 `ratio` 总和为 0，且满足对冲条件

---

## 2. 套利场景

### 2.1 场景 1：现货-现货/合约套利（跨平台）

```
低价平台买入现货 → 链上转账 → 高价平台卖出现货
同时：高价平台买入等值空合约（对冲）
```

**示例**：
- OKX ETH/USDT: 2000 USD（买入现货）
- Binance ETH/USDT: 2010 USD（卖出现货 + 做空合约）
- 价差收益：10 USD/ETH

### 2.2 场景 2：现货/合约套利（资费率套利）

```
资费率为正：做空合约 + 买入现货（收取资费）
资费率为负：做多合约 + 卖出现货（收取资费）
```

### 2.3 场景 3：合约/合约套利

```
不同交易所的合约价差套利
```

---

## 3. 核心概念

### 3.1 Trading Pair 分组

**分组规则**：
- 默认：`symbol.split('/')[0]`（如 ETH/USDT → ETH）
- 自定义：通过 `trading_pair_group` 配置映射

**配置示例**：
```yaml
default_trading_pair_group: symbol.split('/')[0]

trading_pair_group:
  WBETH/USDT: ETH  # WBETH 映射到 ETH 组
  STETH/USDT: ETH  # STETH 映射到 ETH 组
```

**分组结果**：
```python
{
    "ETH": [
        ("okx/main", "ETH/USDT"),
        ("okx/main", "WBETH/USDT"),
        ("binance/spot", "ETH/USDT"),
    ],
    "BTC": [
        ("okx/main", "BTC/USDT"),
        ("binance/spot", "BTC/USDT"),
    ],
}
```

### 3.2 Fair Price（公平价格）

**定义**：用于衡量不同交易所/交易对之间价格差异的标准价格。

**计算方式**：
- 通过 `FairPriceIndicator` 注入到 `trading_pair_class` Scope
- 返回 `None` 表示该交易对暂时不参与计算（mask 机制）
- FairPriceIndicator 返回原始 mid_price，不做标准化处理

**价格比较**：
- Strategy 层使用原始价格进行比较
- 组内最小/最大价格用于计算 delta 和 direction

### 3.3 Direction（方向）

**Direction 类型**：
- `-1`: Entry Short（建议开空仓）
- `0`: Exit（建议平仓）
- `1`: Entry Long（建议开多仓）
- `null`: Hold（建议持仓不动）

**每个 trading pair 有两个 direction**：
- `delta_min_direction`: 相对于组内最低价的方向
- `delta_max_direction`: 相对于组内最高价的方向

### 3.4 Ratio（仓位比例）

**定义**：该交易对在总仓位中的比例，范围 `[-1, 1]`。

**市场中性条件**：
- 组内所有 `ratio` 总和为 0
- `ratio(Price_min) - ratio(Price_max) = 2`

**示例**：
```python
# ETH 组（3个交易对）
WBETH/USDT (okx):   ratio =  1.0  # 最低价，做多
ETH/USDT (okx):     ratio =  0.0  # 中间价
ETH/USDT (binance): ratio = -1.0  # 最高价，做空

# 验证：1.0 + 0.0 - 1.0 = 0 ✓
# 验证：ratio(Price_min) - ratio(Price_max) = 1.0 - (-1.0) = 2 ✓
```

---

## 4. 配置格式

### 4.1 Strategy 配置

```yaml
# conf/strategy/market_neutral_positions/<name>.yaml
class_name: market_neutral_positions

# 包含/排除交易对
include_symbols: ['*']  # 默认包含所有
exclude_symbols: []     # 排除列表

# 依赖的 Indicator
requires:
  - medal_amount  # 账户余额（注入到 exchange scope）
  - ticker        # 行情数据（注入到 trading_pair_class scope）
  - fair_price    # 标准价格（注入 trading_pair_std_price 到 trading_pair_class scope）

# Scope 链路（按顺序构建 ChainMap）
links:
  - id: main
    value: [g, exchange_class, exchange, trading_pair_class_group, trading_pair_class, trading_pair]

# 分组配置
default_trading_pair_group: symbol.split('/')[0]
trading_pair_group:
  WBETH/USDT: ETH
  STETH/USDT: ETH

# 阈值配置
max_trading_pair_groups: 10    # 最大交易对分组数量
entry_price_threshold: 0.001   # 0.1% 价差开仓
exit_price_threshold: 0.0005   # 0.05% 价差平仓
score_threshold: 0.001         # 最小 score 阈值

# 目标配置（在最后一级 TradingPairScope 上匹配与计算）
targets:
  - exchange_id: "*"
    symbol: "*"
    condition: "ratio != 0"
    vars:
      - position_usd=ratio * max_position_usd
```

### 4.2 App 配置（Scope 定义）

```yaml
# conf/app/<app>.yaml（片段：Scope 节点只允许在 app 配置里声明）
scopes:
  g:
    class: GlobalScope
    vars:
      - max_position_usd=2000
      - weights={"okx/main": 0.5, "binance/spot": 0.5}

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope

  trading_pair_class_group:
    class: TradingPairClassGroupScope
    vars:
      - fair_price_min=min([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - fair_price_max=max([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - score=fair_price_max - fair_price_min
      - ratio_est=sum([scope["ratio_est"] for scope in children.values()])

  trading_pair_class:
    class: TradingPairClassScope
    vars:
      - delta_min_price=trading_pair_std_price - parent["fair_price_min"]
      - delta_max_price=parent["fair_price_max"] - trading_pair_std_price
      - ratio_est=sum([scope["ratio_est_instance"] for scope in children.values()])

  trading_pair:
    class: TradingPairScope
    vars:
      - weight=weights.get(exchange_id, 1.0)
      - ratio_est_instance=weight * (parent.parent["fair_price_min"] * amount) / max_position_usd
```

---
## 5. 计算流程

### 5.1 阶段 1：Scope 树构建和变量计算

**步骤 1.1：构建 Scope 树**
```
global
  └─ exchange_class (okx)
      └─ exchange (okx/main)
          └─ trading_pair_class_group (ETH)
              ├─ trading_pair_class (ETH/USDT)
              │   └─ trading_pair (okx/main, ETH/USDT)
              └─ trading_pair_class (WBETH/USDT)
                  └─ trading_pair (okx/main, WBETH/USDT)
```

**步骤 1.2：Indicator 注入**
- `MedalAmountDataSource` → `exchange` scope（注入 `amount` 变量）
- `TickerDataSource` → `trading_pair_class` scope（注入 `mid_price` 变量）
- `FairPriceIndicator` → `trading_pair_class` scope（注入 `trading_pair_std_price` 变量）

**步骤 1.3：计算 Scope vars（第一遍，从上到下）**
1. `global` scope: 计算 `max_position_usd`, `weights`
2. `trading_pair` scope: 计算 `weight`, `ratio_est_instance`
3. `trading_pair_class` scope: 计算 `ratio_est`（聚合 children 的 `ratio_est_instance`）
4. `trading_pair_class_group` scope: 计算 `fair_price_min`, `fair_price_max`, `score`, `ratio_est`

**步骤 1.4：过滤无效 trading pair**
- 排除 `trading_pair_std_price` 为 `None` 的 trading pair
- 排除 `len(group) < 2` 的 group（单个交易对无法套利）

**步骤 1.5：重新计算 ratio_est（第二遍）**

过滤后，重新计算 `ratio_est`：
1. `trading_pair` scope: 重新计算 `ratio_est_instance`
2. `trading_pair_class` scope: 重新计算 `ratio_est`（聚合 children）
3. `trading_pair_class_group` scope: 重新计算 `ratio_est`（聚合 children）

### 5.2 阶段 2：专用计算流程（MarketNeutralPositions 特有）

**步骤 2.1：计算 Direction 和 delta_min_price / delta_max_price**

在 `trading_pair_class` scope 层级：

1. 计算 `delta_min_price` 和 `delta_max_price`：
   ```python
   delta_min_price = trading_pair_std_price - parent["fair_price_min"]
   delta_max_price = parent["fair_price_max"] - trading_pair_std_price
   ```

2. 根据价差计算 direction：

| Condition | delta_min_price → delta_min_direction | delta_max_price → delta_max_direction |
|-----------|---------------------------------------|---------------------------------------|
| > entry_price_threshold | -1 (Entry Short) | 1 (Entry Long) |
| > exit_price_threshold | 0 (Exit) | 0 (Exit) |
| else | null (Hold) | null (Hold) |

**特殊情况**：
- `len(group) == 1`: 两个 direction 都设为 `0`（Exit），ratio 设为 `0`

**步骤 2.2：选择 Top Groups**

选择需要返回的 group（最多 `max_trading_pair_groups` 个）：

1. **优先级 1**：包含已有仓位的 group
   - 检查所有 exchange 的 positions 和 balance
   - 计算其 `group_id`
   - 这些 group 优先选择

2. **优先级 2**：按 `score` 排序
   - `score >= score_threshold`
   - 从高到低排序
   - 选择直到达到 `max_trading_pair_groups`

**步骤 2.3：计算 Ratio（第一遍）**

在 `trading_pair_class` scope 层级：

```python
# 初始 ratio
ratio = clip(ratio_est, -1, 1)
```

**步骤 2.4：根据 Direction 调整 Ratio**

根据 `(delta_min_direction, delta_max_direction)` 组合调整 ratio：

| (delta_min_direction, delta_max_direction) | Ratio 调整 |
|---------------------------------------------|-----------|
| (-1, -1) | raise ValueError（不应出现） |
| (-1, 0) | min(ratio, 0) |
| (-1, 1) | ratio（不变） |
| (-1, null) | -1 |
| (0, -1) | raise ValueError（不应出现） |
| (0, 0) | ratio（不变） |
| (0, 1) | max(ratio, 0) |
| (0, null) | min(ratio, 0) |
| (1, -1) | raise ValueError（不应出现） |
| (1, 0) | raise ValueError（不应出现） |
| (1, 1) | raise ValueError（不应出现） |
| (1, null) | raise ValueError（不应出现） |
| (null, -1) | raise ValueError（不应出现） |
| (null, 0) | max(ratio, 0) |
| (null, 1) | 1 |
| (null, null) | ratio（不变） |

**步骤 2.5：Ratio 平衡（组内总和归零）**

在 `trading_pair_class_group` scope 层级：

```python
# 计算组内 ratio 总和
ratio_sum = sum([child["ratio"] for child in children.values()])

if ratio_sum > 0:
    # 总和为正，从最高价的 ratio 中减去
    max_price_pair = max(children.values(), key=lambda s: s["trading_pair_std_price"])
    max_price_pair["ratio"] -= ratio_sum
elif ratio_sum < 0:
    # 总和为负，从最低价的 ratio 中加上（减去负数）
    min_price_pair = min(children.values(), key=lambda s: s["trading_pair_std_price"])
    min_price_pair["ratio"] -= ratio_sum  # 等价于 += abs(ratio_sum)
```

**步骤 2.6：Ratio 对冲调整（满足 ratio_min - ratio_max = 2）**

在 `trading_pair_class_group` scope 层级：

```python
# 找到最小和最大价格的 pair
min_price_pair = min(children.values(), key=lambda s: s["trading_pair_std_price"])
max_price_pair = max(children.values(), key=lambda s: s["trading_pair_std_price"])

# 计算调整量
delta_ratio = (min_price_pair["ratio"] - max_price_pair["ratio"]) / 2 - 1

# 调整
min_price_pair["ratio"] -= delta_ratio
max_price_pair["ratio"] += delta_ratio
```

**验证**：
```python
# 验证 1：总和为 0
assert sum([child["ratio"] for child in children.values()]) == 0

# 验证 2：对冲条件
assert min_price_pair["ratio"] - max_price_pair["ratio"] == 2
```

### 5.3 阶段 3：生成 Strategy 输出

**步骤 3.1：展开 target_pairs**

将选中的 group 中的所有 `trading_pair` 展开为 `target_pairs`。

**步骤 3.2：计算 target vars**

在 `trading_pair` scope 层级，计算 `target` 配置中的 vars：

```python
position_usd = ratio * max_position_usd
```

**步骤 3.3：检查 target condition**

```python
if ratio != 0:
    # 输出该 target
    output[(exchange_id, symbol)] = {
        "position_usd": position_usd,
        "ratio": ratio,
        "delta_min_direction": delta_min_direction,
        "delta_max_direction": delta_max_direction,
    }
```

---

## 6. 关键变量说明

### 6.1 代码引用的特殊字段

这些字段由代码直接引用，必须在 Strategy 配置中定义：

| 字段 | 类型 | 说明 |
|------|------|------|
| `max_trading_pair_groups` | int | 最大交易对分组数量 |
| `entry_price_threshold` | float | 开仓价差阈值 |
| `exit_price_threshold` | float | 平仓价差阈值 |
| `score_threshold` | float | 最小 score 阈值 |
| `default_trading_pair_group` | str | 默认分组表达式 |
| `trading_pair_group` | dict | 自定义分组映射 |

### 6.2 用户定义的通用值

这些变量由用户在 Scope vars 中定义，可以自由命名：

| 变量 | 建议名称 | 说明 |
|------|----------|------|
| 最大仓位 | `max_position_usd` | 每个分组的最大仓位（USD） |
| 交易所权重 | `weights` | 交易所权重配置 |
| 单个权重 | `weight` | 从 weights 字典中获取 |

### 6.3 ratio_est 两阶段计算

**第一阶段**（trading_pair level）：
```python
ratio_est_instance = weight × (fair_price_min × amount) / max_position_usd
```
- 计算每个 trading pair instance 的初始 ratio 估计值
- 基于账户余额（amount）和权重（weight）

**第二阶段**（trading_pair_class level）：
```python
ratio_est = sum([scope["ratio_est_instance"] for scope in children.values()])
```
- 聚合所有 trading pair instances 的 ratio_est_instance
- 这是一个**特殊字段**，用于后续 ratio 计算

---

## 7. 相关文档

- [Proposal 001: Scope/VirtualMachine 数据驱动系统](./001-scope-vm-data-driven-system.md)
- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
- [Feature 0013: MarketNeutralPositions 策略](../features/0013-market-neutral-positions-strategy.md)
- [Example 004: MarketNeutralPositions 配置详解](../examples/004-market-neutral-positions-strategy.md)
