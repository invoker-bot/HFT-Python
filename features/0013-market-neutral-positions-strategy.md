# Feature 0013: MarketNeutralPositions 策略

## 概述

实现 **MarketNeutralPositions** 策略，这是一个市场中性对冲策略，与 StaticPositions 对应。

**核心目标**：
- 保持 `ratio` 总和为 0（市场中性）
- 支持三种套利模式
- 基于 Scope 系统实现多层级计算

**策略特性**：
1. **Trading Pair 分组**：按 `group_id` 分组（如 ETH/USDT → ETH）
2. **Fair Price 计算**：通过 `FairPriceIndicator` 计算公平价格
3. **Direction 计算**：自动计算开仓/平仓/持仓方向
4. **Ratio 平衡**：确保组内 `ratio` 总和为 0，且满足对冲条件

## 动机

### 套利场景

**场景 1：现货-现货/合约套利（跨平台）**
```
低价平台买入现货 → 链上转账 → 高价平台卖出现货
同时：高价平台买入等值空合约（对冲）
```

**场景 2：现货/合约套利（资费率套利）**
```
资费率为正：做空合约 + 买入现货（收取资费）
资费率为负：做多合约 + 卖出现货（收取资费）
```

**场景 3：合约/合约套利**
```
不同交易所的合约价差套利
```

### 当前问题

**问题 1：旧 ArbitrageStrategy 设计不合理**
- 缺乏统一的分组机制
- 没有 Fair Price 概念
- Ratio 平衡逻辑不清晰
- 无法支持复杂的多层级计算

**问题 2：需要 Scope 系统支持**
- 需要在 `trading_pair_class_group` 层级聚合价格
- 需要在 `trading_pair_class` 层级计算 direction
- 需要在 `trading_pair` 层级执行订单

### 设计目标

1. **清晰的分组机制**：通过 `trading_pair_group` 配置灵活分组
2. **公平价格计算**：通过 `FairPriceIndicator` 统一计算
3. **自动 Direction 计算**：根据价差自动判断开仓/平仓/持仓
4. **Ratio 平衡算法**：确保市场中性（ratio 总和为 0）
5. **基于 Scope 系统**：利用 Scope 的多层级计算能力

---

## 核心概念

### 1. Trading Pair 分组

**分组规则**：
- 默认：`symbol.split('/')[0]`（如 ETH/USDT → ETH）
- 自定义：通过 `trading_pair_group` 配置映射

**示例**：
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

### 2. Fair Price（公平价格）

**定义**：用于衡量不同交易所/交易对之间价格差异的标准价格。

**计算方式**：
- 通过 `FairPriceIndicator` 注入到 `trading_pair_class` Scope
- 返回 `None` 表示该交易对暂时不参与计算（mask 机制）

**标准化**：
- 组内最小价格标准化为 1.0
- 其他价格按比例缩放

**示例**：
```python
# 原始价格
ETH/USDT (okx): 2000 USD
ETH/USDT (binance): 2010 USD
WBETH/USDT (okx): 1990 USD

# 标准化后（最小价格 = 1.0）
ETH/USDT (okx): 1.005
ETH/USDT (binance): 1.010
WBETH/USDT (okx): 1.000
```

### 3. Direction（方向）

**Direction 类型**：
- `-1`: Entry Short（建议开空仓）
- `0`: Exit（建议平仓）
- `1`: Entry Long（建议开多仓）
- `null`: Hold（建议持仓不动）

**每个 trading pair 有两个 direction**：
- `delta_min_direction`: 相对于组内最低价的方向
- `delta_max_direction`: 相对于组内最高价的方向

### 4. Ratio（仓位比例）

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

## 配置格式

### Strategy 配置

```yaml
class_name: market_neutral_positions

# 包含/排除交易对
include_symbols: ['*']  # 默认包含所有
exclude_symbols: []     # 排除列表

# 交易所过滤
exchanges: ['*']  # 默认包含所有

# 依赖的 Indicator
requires:
  - medal_amount  # 账户余额（注入到 exchange scope）
  - ticker        # 行情数据（注入到 trading_pair_class scope）
  - fair_price    # 标准价格（注入 trading_pair_std_price 到 trading_pair_class scope）

# Scope 链路
links:
  - ["global", "exchange_class", "exchange", "trading_pair_class_group", "trading_pair_class", "trading_pair"]

# Scope 变量配置
scopes:
  global:
    vars:
      - name: max_trading_pair_groups
        value: 10
      - name: max_position_usd
        value: 2000
      - name: weights
        value: {"okx/main": 0.5, "binance/spot": 0.5}

  trading_pair_class_group:
    vars:
      - name: fair_price_min
        value: min([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - name: fair_price_max
        value: max([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - name: score
        value: fair_price_max - fair_price_min
      - name: ratio_est
        value: sum([scope["ratio_est"] for scope in children.values()])
    group_condition: null  # 可选的组级过滤条件

  trading_pair_class:
    vars:
      - name: delta_min_price
        value: trading_pair_std_price - parent["fair_price_min"]
      - name: delta_max_price
        value: parent["fair_price_max"] - trading_pair_std_price
      - name: ratio_est
        value: sum([scope["ratio_est_instance"] for scope in children.values()])

  trading_pair:
    vars:
      - name: weight
        value: weights.get(exchange_path, 1.0)
      - name: ratio_est_instance
        value: weight * (parent.parent["fair_price_min"] * amount) / max_position_usd

# 分组配置
default_trading_pair_group: symbol.split('/')[0]
trading_pair_group:
  WBETH/USDT: ETH
  STETH/USDT: ETH

# 阈值配置
entry_price_threshold: 0.001   # 0.1% 价差开仓
exit_price_threshold: 0.0005   # 0.05% 价差平仓
score_threshold: 0.001         # 最小 score 阈值

# 目标配置
target_scope: trading_pair
target:
  vars:
    - name: position_usd
      value: ratio * max_position_usd
  condition: ratio != 0
```

> 注意：当前求值器（`simpleeval`）不支持 list comprehension，上述 `min([ ... for ... ])` 仅表达意图；实现需要提供 children 聚合 helper（见 Feature 0012 的“表达式能力限制”）。

---

## 计算流程

### 阶段 1：Scope 树构建和变量计算

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
1. `global` scope: 计算 `max_trading_pair_groups`, `max_position_usd`, `weights`
2. `trading_pair` scope: 计算 `weight`, `ratio_est_instance`
3. `trading_pair_class` scope: 计算 `ratio_est`（聚合 children 的 `ratio_est_instance`）
4. `trading_pair_class_group` scope: 计算 `fair_price_min`, `fair_price_max`, `score`, `ratio_est`（聚合 children 的 `ratio_est`）

**步骤 1.4：过滤无效 trading pair**
- 排除 `trading_pair_std_price` 为 `None` 的 trading pair
- 排除 `group_condition` 为 `False` 的 group
- 排除 `len(group) < 2` 的 group（单个交易对无法套利）

**步骤 1.5：重新计算 ratio_est（第二遍）**

过滤后，重新计算 `ratio_est`：
1. `trading_pair` scope: 重新计算 `ratio_est_instance`
2. `trading_pair_class` scope: 重新计算 `ratio_est`（聚合 children）
3. `trading_pair_class_group` scope: 重新计算 `ratio_est`（聚合 children）

**注意**：`ratio_est` 在 `trading_pair_class` level 是一个**特殊字段**，用于后续的 ratio 计算。

### 阶段 2：专用计算流程（MarketNeutralPositions 特有）

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

### 阶段 3：生成 Strategy 输出

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
    output[(exchange_path, symbol)] = {
        "position_usd": position_usd,
        "ratio": ratio,
        "delta_min_direction": delta_min_direction,
        "delta_max_direction": delta_max_direction,
    }
```

---

## 实现细节

实现参考（当前代码状态）：
- `hft/core/scope/scopes.py`：已提供 `TradingPairClassGroupScope`（只负责注入 `group_id`；Scope 创建/children 挂接由 ScopeManager 统一处理，不在 Scope 内实现 `create_child()`）
- `hft/strategy/market_neutral_positions.py`：当前仅提供骨架，核心计算流程仍为 TODO（见任务列表 Phase 3）

---

## 任务列表

### Phase 1: 依赖组件（P0）

- [ ] 实现 `FairPriceIndicator`（审核不通过：按 Feature 0006 约定 `window: null` 语义等价 `0`（单值），但当前代码未 normalize `None -> 0` 会运行时报错；且 `calculate_vars()` 恒返回 None，无法提供 trading_pair_std_price）
  - [ ] 计算公平价格（审核不通过：缺少从 TickerDataSource/mid_price 获取值的实现路径；当前恒返回 None）
  - [ ] 支持返回 `None`（审核不通过：当前恒返回 None，等价“永远 mask”，缺少“有数据时返回价格”的分支）
  - [ ] 注入到 `trading_pair_class` scope（待实现：依赖 Feature 0012 Phase 4 + Strategy scope vars 计算链路）
- [ ] 实现 `MedalAmountDataSource`（审核不通过：`on_tick()` 返回 True 导致运行一次就停止；`calculate_vars()` 调用 `HealthyDataArray.get_latest()`（不存在））
  - [ ] 获取合约/现货账户余额（审核不通过：虽然调用 medal_fetch_total_balance_usd()，但 on_tick 返回 True 导致不会周期更新）
  - [ ] 注入 `amount` 变量到 `exchange` scope（审核不通过：calculate_vars 有 bug；Scope 注入链路也未实现）
- [ ] 单元测试：FairPriceIndicator（待实现）
- [ ] 单元测试：MedalAmountDataSource（待实现）

### Phase 2: TradingPairClassGroupScope（P0）

- [ ] 明确 `trading_pair_class_group` 的聚合范围与 links 位置（待商议：要覆盖“跨平台套利”场景，需要 group 能聚合跨 exchange 的 trading pairs；需确定最终链路/聚合方式）
- [x] 实现 `TradingPairClassGroupScope`（已通过：ScopeManager 负责创建/挂接 children，不需要 `create_child()`）
  - [x] 继承 `BaseScope`（已通过）
  - [x] 不实现 `create_child()`（已通过）
  - [x] 注入 `group_id` 变量（已通过）
- [ ] 单元测试：TradingPairClassGroupScope（待实现）

### Phase 3: MarketNeutralPositionsStrategy（P0）

- [ ] 实现 `MarketNeutralPositionsStrategy`（审核不通过：当前 `get_target_positions_usd()` 仍为 TODO，返回空输出）
  - [x] 继承 `BaseStrategy`（已通过）
  - [ ] 实现 `_register_custom_scopes()`（待实现）
  - [ ] 实现 `_compute_directions()`（待实现）
  - [ ] 实现 `_select_top_groups()`（待实现）
  - [ ] 实现 `_compute_ratios()`（待实现）
  - [ ] 实现 `_balance_ratios()`（待实现）
  - [ ] 实现 `_adjust_hedge_ratios()`（待实现）
  - [ ] 实现 `_generate_output()`（待实现）
- [ ] 实现 `MarketNeutralPositionsStrategyConfig`（审核不通过：当前为非 pydantic 字段写法，且缺失关键配置字段）
  - [ ] 添加 `include_symbols` / `exclude_symbols` 字段（审核不通过）
  - [ ] 添加 `default_trading_pair_group` 字段（审核不通过）
  - [ ] 添加 `trading_pair_group` 字段（审核不通过）
  - [ ] 添加 `entry_price_threshold` / `exit_price_threshold` 字段（审核不通过）
  - [ ] 添加 `score_threshold` 字段（审核不通过）
- [ ] 单元测试：MarketNeutralPositionsStrategy（待实现）

### Phase 4: 清理旧代码（P1）

- [ ] 删除 `hft/strategy/keep_balances.py`（待实现）
- [ ] 删除 `hft/strategy/arbitrage/` 目录（待实现）
- [ ] 更新配置注册（待实现）

### Phase 5: 文档和示例（P2）

- [ ] 编写 `examples/004-market-neutral-positions-strategy.md`（待实现）
- [ ] 更新 `docs/strategy.md`（待实现）
- [ ] 更新 `REVIEW.md`（待实现）

---

## 影响范围

### 核心模块

| 模块 | 影响 | 说明 |
|------|------|------|
| `hft/strategy/market_neutral_positions.py` | **新增** | MarketNeutralPositions 策略实现 |
| `hft/core/scope/scopes.py` | **新增** | TradingPairClassGroupScope 实现 |
| `hft/indicator/fair_price_indicator.py` | **新增** | FairPriceIndicator 实现 |
| `hft/datasource/medal_amount_datasource.py` | **新增** | MedalAmountDataSource 实现 |
| `hft/strategy/keep_balances.py` | **删除** | 旧策略，已废弃 |
| `hft/strategy/arbitrage/` | **删除** | 旧套利策略，已废弃 |

### 测试文件

| 文件 | 影响 | 说明 |
|------|------|------|
| `tests/test_market_neutral_positions.py` | **新增** | MarketNeutralPositions 单元测试 |
| `tests/test_fair_price_indicator.py` | **新增** | FairPriceIndicator 单元测试 |
| `tests/test_medal_amount_datasource.py` | **新增** | MedalAmountDataSource 单元测试 |

### 文档

| 文件 | 影响 | 说明 |
|------|------|------|
| `examples/004-market-neutral-positions-strategy.md` | **新增** | MarketNeutralPositions 配置示例 |
| `docs/strategy.md` | **中等** | 添加 MarketNeutralPositions 章节 |

---

## 相关文档

- [Feature 0012: Scope 系统](./0012-scope-system.md)
- [Feature 0008: Strategy 数据驱动](./0008-strategy-data-driven.md)
- [Feature 0011: Strategy Target 展开式与去特殊化](./0011-strategy-target-expansion.md)
- [Example 004: MarketNeutralPositions 配置详解](../examples/004-market-neutral-positions-strategy.md)
