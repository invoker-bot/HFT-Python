# Example 004: MarketNeutralPositions 配置详解

## 概述

本文档详细介绍 **MarketNeutralPositions** 策略的配置方法和使用场景。

**策略特点**：
- 市场中性对冲策略（ratio 总和为 0）
- 支持三种套利模式（现货-现货/合约、现货/合约、合约/合约）
- 基于 Scope 系统实现多层级计算
- 自动计算开仓/平仓/持仓方向

**适用场景**：
- 跨平台套利（低买高卖 + 对冲）
- 资费率套利（合约资费收益）
- 合约价差套利（不同交易所合约价差）

---

## 基础概念

### 1. Trading Pair 分组

MarketNeutralPositions 策略通过 `group_id` 将交易对分组，同一组内的交易对进行套利。

**默认分组规则**：
```python
group_id = symbol.split('/')[0]  # ETH/USDT → ETH
```

**示例**：
```yaml
# 默认分组
ETH/USDT → ETH 组
ETH/USDT:USDT → ETH 组
BTC/USDT → BTC 组
```

**自定义分组**：
```yaml
trading_pair_group:
  WBETH/USDT: ETH  # WBETH 映射到 ETH 组
  STETH/USDT: ETH  # STETH 映射到 ETH 组
```

### 2. Fair Price（公平价格）

Fair Price 是用于衡量不同交易所/交易对之间价格差异的标准价格。

**计算方式**：
- 通过 `FairPriceIndicator` 计算
- 组内最小价格标准化为 1.0
- 其他价格按比例缩放

**示例**：
```
原始价格：
  ETH/USDT (okx):     2000 USD
  ETH/USDT (binance): 2010 USD
  WBETH/USDT (okx):   1990 USD

标准化后（最小价格 = 1.0）：
  ETH/USDT (okx):     1.005
  ETH/USDT (binance): 1.010
  WBETH/USDT (okx):   1.000
```

### 3. Direction（方向）

Direction 表示建议的交易方向：
- `-1`: Entry Short（建议开空仓）
- `0`: Exit（建议平仓）
- `1`: Entry Long（建议开多仓）
- `null`: Hold（建议持仓不动）

每个交易对有两个 direction：
- `delta_min_direction`: 相对于组内最低价的方向
- `delta_max_direction`: 相对于组内最高价的方向

### 4. Ratio（仓位比例）

Ratio 表示该交易对在总仓位中的比例，范围 `[-1, 1]`。

**市场中性条件**：
- 组内所有 `ratio` 总和为 0
- `ratio(Price_min) - ratio(Price_max) = 2`

**示例**：
```
ETH 组：
  ETH/USDT (okx):     ratio = -1.0  # 最低价，做多
  ETH/USDT (binance): ratio =  0.5  # 中间价
  WBETH/USDT (okx):   ratio =  0.5  # 接近最低价

验证：-1.0 + 0.5 + 0.5 = 0 ✓（市场中性）
```

---

## 配置示例

### 示例 1：基础配置（跨平台套利）

**场景**：在 OKX 和 Binance 之间进行 ETH 套利。

```yaml
# conf/strategy/market_neutral_positions/basic.yaml
class_name: market_neutral_positions

# 包含所有交易对
include_symbols: ['*']
exclude_symbols: []

# 包含所有交易所
exchanges: ['*']

# 依赖的 Indicator
requires:
  - medal_amount  # 账户余额
  - ticker        # 行情数据
  - fair_price    # 标准价格（注入 trading_pair_std_price）

# Scope 链路
links:
  - ["global", "exchange_class", "exchange", "trading_pair_class_group", "trading_pair_class", "trading_pair"]

# Scope 变量配置
scopes:
  global:
    vars:
      - name: max_trading_pair_groups
        value: 5  # 最多 5 个套利组
      - name: max_position_usd
        value: 10000  # 每组最大仓位 $10,000
      - name: weights
        value: {"okx/main": 0.5, "binance/spot": 0.5}  # 交易所权重

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

# 分组配置（使用默认规则）
default_trading_pair_group: symbol.split('/')[0]

# 阈值配置
entry_price_threshold: 0.002   # 0.2% 价差开仓
exit_price_threshold: 0.001    # 0.1% 价差平仓
score_threshold: 0.002         # 最小 score 阈值

# 目标配置
target_scope: trading_pair
target:
  vars:
    - name: position_usd
      value: ratio * max_position_usd
    - name: speed
      value: 0.5
  condition: ratio != 0
```

### 示例 2：自定义分组（包含衍生品）

**场景**：将 WBETH、STETH 等衍生品映射到 ETH 组。

```yaml
# conf/strategy/market_neutral_positions/custom_group.yaml
class_name: market_neutral_positions

include_symbols: ['ETH/USDT', 'WBETH/USDT', 'STETH/USDT', 'BTC/USDT']
exchanges: ['*']

requires:
  - medal_amount
  - ticker
  - fair_price

links:
  - ["global", "exchange_class", "exchange", "trading_pair_class_group", "trading_pair_class", "trading_pair"]

scopes:
  global:
    vars:
      - name: max_trading_pair_groups
        value: 3
      - name: max_position_usd
        value: 20000
      - name: weights
        value: {"okx/main": 0.4, "binance/spot": 0.6}

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
    group_condition: null

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

# 自定义分组：将衍生品映射到主币种
default_trading_pair_group: symbol.split('/')[0]
trading_pair_group:
  WBETH/USDT: ETH  # Wrapped Beacon ETH → ETH
  STETH/USDT: ETH  # Staked ETH → ETH
  WBTC/USDT: BTC   # Wrapped BTC → BTC

entry_price_threshold: 0.003
exit_price_threshold: 0.0015
score_threshold: 0.003

target_scope: trading_pair
target:
  vars:
    - name: position_usd
      value: ratio * max_position_usd
    - name: speed
      value: 0.6
  condition: ratio != 0
```

### 示例 3：资费率套利

**场景**：利用合约资费率进行套利（现货 + 合约对冲）。

```yaml
# conf/strategy/market_neutral_positions/funding_rate.yaml
class_name: market_neutral_positions

# 只包含永续合约
include_symbols: ['ETH/USDT:USDT', 'BTC/USDT:USDT']
exchanges: ['okx/main', 'binance/futures']

requires:
  - medal_amount
  - ticker
  - fair_price
  - funding_rate  # 资费率 Indicator

links:
  - ["global", "exchange_class", "exchange", "trading_pair_class_group", "trading_pair_class", "trading_pair"]

scopes:
  global:
    vars:
      - name: max_trading_pair_groups
        value: 2
      - name: max_position_usd
        value: 50000
      - name: weights
        value: {"okx/main": 0.5, "binance/futures": 0.5}
      - name: min_funding_rate
        value: 0.0001  # 最小资费率阈值（0.01%）

  trading_pair_class_group:
    vars:
      - name: fair_price_min
        value: min([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - name: fair_price_max
        value: max([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - name: score
        value: fair_price_max - fair_price_min
      - name: avg_funding_rate
        value: avg([scope["funding_rate"] for scope in children.values() if scope["funding_rate"] is not None])
      - name: ratio_est
        value: sum([scope["ratio_est"] for scope in children.values()])
    # 只选择资费率足够高的组
    group_condition: abs(avg_funding_rate) > min_funding_rate

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

default_trading_pair_group: symbol.split('/')[0]

# 资费率套利的阈值更宽松
entry_price_threshold: 0.005   # 0.5%
exit_price_threshold: 0.0025   # 0.25%
score_threshold: 0.005

target_scope: trading_pair
target:
  vars:
    - name: position_usd
      value: ratio * max_position_usd
    - name: speed
      value: 0.3  # 资费率套利不急
  condition: ratio != 0
```

---

## 配置字段详解

### 1. 交易对过滤

```yaml
# 包含所有交易对
include_symbols: ['*']

# 包含特定交易对
include_symbols: ['ETH/USDT', 'BTC/USDT', 'SOL/USDT']

# 排除特定交易对
exclude_symbols: ['DOGE/USDT', 'SHIB/USDT']

# 交易所过滤
exchanges: ['*']  # 所有交易所
exchanges: ['okx/main', 'binance/spot']  # 特定交易所
```

### 2. Scope 变量配置

#### global scope

```yaml
scopes:
  global:
    vars:
      - name: max_trading_pair_groups
        value: 10  # 最多返回 10 个套利组
      - name: max_position_usd
        value: 10000  # 每组最大仓位
      - name: weights
        value: {"okx/main": 0.5, "binance/spot": 0.5}  # 交易所权重
```

**字段说明**：
- `max_trading_pair_groups`: 最多返回的套利组数量
- `max_position_usd`: 每个套利组的最大仓位（USD）
- `weights`: 各交易所的权重（用于计算 `ratio_est`）

#### trading_pair_class_group scope

```yaml
scopes:
  trading_pair_class_group:
    vars:
      - name: fair_price_min
        value: min([scope["fair_price"] for scope in children.values() if scope["fair_price"] is not None])
      - name: fair_price_max
        value: max([scope["fair_price"] for scope in children.values() if scope["fair_price"] is not None])
      - name: score
        value: fair_price_max - fair_price_min
    condition: len([s for s in children.values() if s["fair_price"] is not None]) >= 2
```

**字段说明**：
- `fair_price_min`: 组内最低公平价格
- `fair_price_max`: 组内最高公平价格
- `score`: 价差（用于排序选择 Top Groups）
- `condition`: 组过滤条件（至少 2 个有效交易对）

#### trading_pair_class scope

```yaml
scopes:
  trading_pair_class:
    vars:
      - name: delta_min_price
        value: fair_price - parent["fair_price_min"]
      - name: delta_max_price
        value: parent["fair_price_max"] - fair_price
```

**字段说明**：
- `delta_min_price`: 与组内最低价的差值（用于计算 `delta_min_direction`）
- `delta_max_price`: 与组内最高价的差值（用于计算 `delta_max_direction`）

#### trading_pair scope

```yaml
scopes:
  trading_pair:
    vars:
      - name: ratio_est
        value: weight * (parent["fair_price_min"] * amount) / max_position_usd
```

**字段说明**：
- `ratio_est`: 初始 ratio 估计值（基于账户余额和权重）

### 3. 分组配置

```yaml
# 默认分组规则（表达式）
default_trading_pair_group: symbol.split('/')[0]

# 自定义分组映射
trading_pair_group:
  WBETH/USDT: ETH
  STETH/USDT: ETH
  WBTC/USDT: BTC
```

**说明**：
- `default_trading_pair_group`: 默认分组规则（Python 表达式）
- `trading_pair_group`: 自定义映射（优先级高于默认规则）

### 4. 阈值配置

```yaml
entry_price_threshold: 0.002   # 开仓阈值（0.2%）
exit_price_threshold: 0.001    # 平仓阈值（0.1%）
score_threshold: 0.002         # 最小 score 阈值
```

**说明**：
- `entry_price_threshold`: 价差超过此值时建议开仓
- `exit_price_threshold`: 价差低于此值时建议平仓
- `score_threshold`: 组的 score 必须超过此值才会被选中
- **约束**：`entry_price_threshold > exit_price_threshold >= 0`

### 5. 目标配置

```yaml
target_scope: trading_pair  # 目标输出层级

target:
  vars:
    - name: position_usd
      value: ratio * max_position_usd
    - name: speed
      value: 0.5
  condition: ratio != 0  # 只输出 ratio 非零的交易对
```

**说明**：
- `target_scope`: 策略输出的 Scope 层级（通常是 `trading_pair`）
- `target.vars`: 在 target_scope 层级计算的变量
- `target.condition`: 输出过滤条件

---

## 常见问题

### Q1: 如何调整套利的激进程度？

**A**: 调整阈值参数：

```yaml
# 激进策略（更频繁交易）
entry_price_threshold: 0.001   # 0.1%
exit_price_threshold: 0.0005   # 0.05%

# 保守策略（更少交易）
entry_price_threshold: 0.005   # 0.5%
exit_price_threshold: 0.0025   # 0.25%
```

### Q2: 如何限制套利组的数量？

**A**: 调整 `max_trading_pair_groups`：

```yaml
scopes:
  global:
    vars:
      - name: max_trading_pair_groups
        value: 3  # 最多 3 个套利组
```

### Q3: 如何排除某些交易对？

**A**: 使用 `exclude_symbols` 或 `FairPriceIndicator` 返回 `None`：

```yaml
# 方法 1：直接排除
exclude_symbols: ['DOGE/USDT', 'SHIB/USDT']

# 方法 2：通过 FairPriceIndicator 的 condition 排除
# （在 Indicator 配置中设置条件）
```

### Q4: 如何处理已有仓位？

**A**: 策略会自动优先选择包含已有仓位的组：

```python
# 步骤 2.2：选择 Top Groups
# 优先级 1：包含已有仓位的 group
# 优先级 2：按 score 排序
```

### Q5: Ratio 平衡算法如何工作？

**A**: 分三步：

1. **初始 ratio**: `ratio = clip(ratio_est, -1, 1)`
2. **根据 Direction 调整**: 根据 16 种组合调整 ratio
3. **平衡调整**:
   - 确保组内 ratio 总和为 0
   - 确保 `ratio(Price_min) - ratio(Price_max) = 2`

---

## 相关文档

- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
- [Feature 0013: MarketNeutralPositions 策略](../features/0013-market-neutral-positions-strategy.md)
- [Example 003: StaticPositionsStrategy 配置详解](./003-static-positions-strategy.md)
