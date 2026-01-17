# Executor 配置详解

本文档详细说明各种 Executor 的配置方式，展示如何利用数据驱动的表达式实现精妙的执行逻辑。

---

## 核心概念：统一的 Order 配置

所有 Executor 使用统一的 order 配置机制：

```yaml
orders:  # 或 order + order_levels
  - price: ...              # 绝对价格（可选）
    spread: ...             # 价差（当 price 未定义时使用）
    order_usd: ...          # 订单金额
    order_amount: ...       # 订单数量（正=买，负=卖）
    refresh_tolerance: ...  # 刷新容忍度（比例）
    refresh_tolerance_usd: ... # 刷新容忍度（绝对值）
    timeout: ...            # 订单超时
    condition: ...          # 挂单条件
    vars: ...               # 订单级变量
    conditional_vars: ...   # 订单级条件变量
```

### Order 展开机制

```yaml
# 方式一：显式列表
orders:
  - price: 99.5
    order_amount: 0.1
  - price: 99.0
    order_amount: 0.2

# 方式二：level 扩展
order_levels: 3  # 生成 level ∈ {-3, -2, -1, 1, 2, 3}
order:
  price: 'mid_price - 0.3 * level'
  order_amount: '0.1 * abs(level)'
  condition: 'level < 0'  # 仅卖单

# 方式三：entry/exit 分离（MarketMakingExecutor、PCAExecutor）
entry_orders:  # 或 entry_order + entry_order_levels
  - spread: 0.0003
    order_usd: 100
exit_orders:   # 或 exit_order + exit_order_levels
  - spread: 0.0003
    order_usd: 100
```

> **注意**：
> - `entry_orders` 用于趋近目标（入场）
> - `exit_orders` 用于偏离目标（出场）
> - 使用 entry/exit 写法时不需要 `reverse` 参数

---

## 1. MarketExecutor - 市价单执行器

市价单是最简单的执行方式，立即以市场价格成交。

### 基础配置

```yaml
# conf/executor/market/basic.yaml
class_name: market

requires:
  - ticker

condition: 'abs(delta_usd) > 10'  # 差值大于 10 USD 才执行

order:
  order_usd: 'abs(delta_usd)'  # 全部用市价单吃掉
  condition: # 也支持order level condition
```

### 分批执行

```yaml
# conf/executor/market/batched.yaml
class_name: market

requires:
  - ticker

vars:
  - name: batch_size
    value: 'min(abs(delta_usd), 1000)'  # 每批最多 1000 USD

order_levels: 1
order:
  order_usd: 'batch_size'
  condition: 'abs(delta_usd) > 10'
```

### 根据 Spread 动态调整

```yaml
# conf/executor/market/adaptive.yaml
class_name: market

requires:
  - ticker

vars:
  - name: spread_pct
    value: '(best_ask - best_bid) / mid_price'
  - name: is_liquid
    value: 'spread_pct < 0.001'  # Spread < 0.1% 算流动性好

order:
  order_usd: 'abs(delta_usd) if is_liquid else min(abs(delta_usd), 500)'
  condition: 'is_liquid or abs(delta_usd) > 100'
```

**说明**：流动性好时全部市价成交，否则限制单笔最大 500 USD。

---

## 2. LimitExecutor - 限价单执行器

限价单提供更好的价格，但可能无法立即成交。

### 2.1 固定 Spread（FixedSpread）

```yaml
# conf/executor/limit/fixed_spread.yaml
class_name: limit

requires:
  - ticker

vars:
  - name: fixed_spread
    value: '0.0002 * mid_price'  # 固定 0.02% 的价差

orders:
  - spread: 'fixed_spread'
    order_usd: 'abs(delta_usd)'
    refresh_tolerance: 0.5
    timeout: 30s
```

### 2.2 基于波动率的 Spread（StdSpread）

```yaml
# conf/executor/limit/std_spread.yaml
class_name: limit

requires:
  - ticker
  - volatility  # 假设有波动率 Indicator

vars:
  - name: base_spread
    value: '0.0005 * mid_price'
  - name: vol_spread
    value: 'volatility * mid_price * 2'  # 2 倍标准差
  - name: dynamic_spread
    value: 'clip(base_spread + vol_spread, 0.0001 * mid_price, 0.01 * mid_price)'

orders:
  - spread: 'dynamic_spread'
    order_usd: 'abs(delta_usd)'
    refresh_tolerance: 0.5
    timeout: 1m
```

**说明**：
- `base_spread`: 基础价差 0.05%
- `vol_spread`: 根据波动率动态调整
- `clip`: 限制在 0.01% ~ 1% 之间

### 2.3 Avellaneda-Stoikov Spread（ASSpread）

```yaml
# conf/executor/limit/as_spread.yaml
class_name: limit

requires:
  - ticker
  - volatility
  - intensity  # 订单到达强度估计indicator，由TradeIntensityIndicator计算

vars:
  - name: gamma
    value: 0.1  # 风险厌恶系数
  - name: T
    value: 300  # 时间窗口（秒）
  - name: inventory
    value: 'current_position_usd / max_position_usd'  # 库存比例
  - name: sigma
    value: 'volatility'
  - name: k
    value: 'intensity'

  # AS 模型公式
  - name: vol_component
    value: 'gamma * sigma * sigma * T'
  - name: order_component
    value: '(2 / gamma) * log(1 + gamma / k) if gamma > 0 else 0'
  - name: base_spread
    value: 'vol_component + order_component'

  # 库存调整
  - name: inventory_adjustment
    value: 'gamma * inventory * sigma * T'
  - name: bid_spread
    value: '(base_spread + inventory_adjustment) * mid_price'
  - name: ask_spread
    value: '(base_spread - inventory_adjustment) * mid_price'

orders:
  # 买单：库存多时 spread 增大
  - spread: 'bid_spread'
    order_amount: 'abs(delta_usd / mid_price) if delta_usd > 0 else 0'
    condition: 'delta_usd > 0'
    refresh_tolerance: 0.5
    timeout: 30s

  # 卖单：库存多时 spread 减小
  - spread: 'ask_spread'
    order_amount: '-abs(delta_usd / mid_price) if delta_usd < 0 else 0'
    condition: 'delta_usd < 0'
    refresh_tolerance: 0.5
    timeout: 30s
```

**说明**：
- `vol_component`: 波动率贡献
- `order_component`: 订单强度贡献
- `inventory_adjustment`: 库存风险调整

### 2.4 多层挂单（Grid-like）

```yaml
# conf/executor/limit/multi_level.yaml
class_name: limit

requires:
  - ticker

vars:
  - name: base_spread
    value: '0.0002 * mid_price'

order_levels: 3
order:
  spread: 'base_spread * abs(level)'
  order_usd: '100 * abs(level)'
  refresh_tolerance: 0.5
  timeout: 1h
```

**等价于**：
```yaml
orders:
  - spread: '0.0002 * mid_price * 1'  # level = -3, -2, -1, 1, 2, 3
    order_usd: 100
  - spread: '0.0002 * mid_price * 2'
    order_usd: 200
  - spread: '0.0002 * mid_price * 3'
    order_usd: 300
```

### 2.5 仅买单或仅卖单

```yaml
# conf/executor/limit/buy_only.yaml
class_name: limit

requires:
  - ticker

order_levels: 5
order:
  spread: '0.0002 * mid_price * abs(level)'
  order_usd: '100 * abs(level)'
  condition: 'level < 0'  # 仅买单（负 level）
  refresh_tolerance: 0.5
  timeout: 1h
```

### 2.6 真正的网格交易（Grid Trading）

使用 `conditional_vars` 缓存中心价格，实现真正的网格交易策略。

```yaml
# conf/executor/limit/true_grid.yaml
class_name: limit

requires:
  - ticker

conditional_vars:
  # 每隔 7 天缓存一次中心价格
  center_price:
    value: mid_price
    on: 'duration > 7 * 24 * 3600'  # 7 天
    default: mid_price

vars:
  - name: grid_spacing
    value: '0.0002 * center_price'  # 0.02% 网格间距

order_levels: 5
order:
  vars:
    - name: grid_price
      value: 'center_price + grid_spacing * level'
    - name: is_buy
      value: 'grid_price < mid_price'  # 低于当前价格挂买单
    - name: is_sell
      value: 'grid_price > mid_price'  # 高于当前价格挂卖单

  condition: 'is_buy or is_sell'
  price: 'grid_price'
  order_usd: '100 * abs(level)'
  order_amount: '(100 * abs(level) / grid_price) if is_buy else -(100 * abs(level) / grid_price)'
  refresh_tolerance: 2.0  # 高容忍度，避免频繁刷新
  timeout: 7d
```

**工作原理**：

1. **中心价格缓存**：
   ```yaml
   conditional_vars:
     center_price:
       value: mid_price
       on: 'duration > 7 * 24 * 3600'
       default: mid_price
   ```
   - `duration > 7d` 时更新 `center_price`
   - 其他时间保持上次值，形成稳定的网格中心

2. **网格价格计算**：
   ```yaml
   grid_price: 'center_price + grid_spacing * level'
   ```
   - `level ∈ {-5, -4, -3, -2, -1, 1, 2, 3, 4, 5}`
   - 生成 10 个网格价格，围绕 `center_price`

3. **动态买卖判断**：
   ```yaml
   is_buy: 'grid_price < mid_price'   # 低于当前价格
   is_sell: 'grid_price > mid_price'  # 高于当前价格
   ```
   - 市场价格上涨：低档位自动变为买单
   - 市场价格下跌：高档位自动变为卖单

4. **订单数量**：
   ```yaml
   order_amount: '(... / grid_price) if is_buy else -(... / grid_price)'
   ```
   - 买单：正数量
   - 卖单：负数量

**示例场景**：

```
初始状态（center_price = 1.0000）:
  level -5: 0.9990 < 1.0000 → 买单
  level -4: 0.9992 < 1.0000 → 买单
  ...
  level +4: 1.0008 > 1.0000 → 卖单
  level +5: 1.0010 > 1.0000 → 卖单

价格上涨到 1.0005:
  level -5: 0.9990 < 1.0005 → 买单
  level -4: 0.9992 < 1.0005 → 买单
  ...
  level +1: 1.0002 < 1.0005 → 买单（自动切换）
  level +2: 1.0004 < 1.0005 → 买单（自动切换）
  level +3: 1.0006 > 1.0005 → 卖单
  level +4: 1.0008 > 1.0005 → 卖单
  level +5: 1.0010 > 1.0005 → 卖单
```

**高级技巧：动态网格间距**

```yaml
conditional_vars:
  center_price:
    value: mid_price
    on: 'duration > 7 * 24 * 3600'
    default: mid_price
  volatility_snapshot:
    value: volatility
    on: 'duration > 7 * 24 * 3600'
    default: 0.01

vars:
  - name: grid_spacing
    value: 'volatility_snapshot * center_price * 2'  # 2x 波动率
```

**说明**：网格间距根据缓存的波动率动态调整，适应市场波动变化。

---

## 3. MarketMakingExecutor - 做市商执行器

专门用于做市策略，使用 `reverse` 参数控制订单方向。

### 3.1 对称做市

```yaml
# conf/executor/market_making/symmetric.yaml
class_name: market_making

requires:
  - ticker

vars:
  - name: spread
    value: '0.0003 * mid_price'
  - name: order_size
    value: 100

orders:
  # 买单：趋近目标
  - spread: 'spread'
    order_usd: 'order_size'
    reverse: false
    refresh_tolerance: 0.5
    timeout: 30s

  # 卖单：趋近目标
  - spread: 'spread'
    order_usd: 'order_size'
    reverse: true
    refresh_tolerance: 0.5
    timeout: 30s
```

**说明**：
- `reverse: false` - 趋近目标（当前 < 目标时买入）
- `reverse: true` - 偏离目标（当前 < 目标时卖出）

**等价写法（entry/exit）**：

```yaml
# conf/executor/market_making/symmetric_v2.yaml
class_name: market_making

requires:
  - ticker

vars:
  - name: spread
    value: '0.0003 * mid_price'
  - name: order_size
    value: 100

# 入场订单（趋近目标）
entry_orders:
  - spread: 'spread'
    order_usd: 'order_size'
    refresh_tolerance: 0.5
    timeout: 30s

# 出场订单（偏离目标）
exit_orders:
  - spread: 'spread'
    order_usd: 'order_size'
    refresh_tolerance: 0.5
    timeout: 30s
```

> **注意**：`entry_orders` 等价于 `reverse: false`，`exit_orders` 等价于 `reverse: true`。
> 使用 entry/exit 写法时不需要 `reverse` 参数，语义更清晰。

### 3.2 库存偏斜（Inventory Skew）

```yaml
# conf/executor/market_making/inventory_skew.yaml
class_name: market_making

requires:
  - ticker

vars:
  - name: inventory_ratio
    value: 'current_position_usd / max_position_usd'
  - name: base_spread
    value: '0.0003 * mid_price'

  # 库存多时买单 spread 增大，卖单 spread 减小
  - name: skew
    value: 'inventory_ratio * base_spread * 0.5'
  - name: bid_spread
    value: 'base_spread + skew'
  - name: ask_spread
    value: 'base_spread - skew'

  # 库存多时买单金额减小，卖单金额增大
  - name: bid_size
    value: '100 * (1 - inventory_ratio * 0.5)'
  - name: ask_size
    value: '100 * (1 + inventory_ratio * 0.5)'

orders:
  - spread: 'bid_spread'
    order_usd: 'bid_size'
    reverse: false
    refresh_tolerance: 0.5
    timeout: 30s

  - spread: 'ask_spread'
    order_usd: 'ask_size'
    reverse: true
    refresh_tolerance: 0.5
    timeout: 30s
```

### 3.3 多层做市

```yaml
# conf/executor/market_making/multi_layer.yaml
class_name: market_making

requires:
  - ticker

vars:
  - name: base_spread
    value: '0.0002 * mid_price'

order_levels: 3
order:
  spread: 'base_spread * abs(level)'
  order_usd: '50 * abs(level)'
  reverse: 'level > 0'  # level > 0 为远离订单
  refresh_tolerance: 0.8
  timeout: 1h
```

**说明**：
- `level ∈ {-3, -2, -1, 1, 2, 3}`
- `level < 0`: 趋近订单（买入追涨，卖出杀跌）
- `level > 0`: 远离订单（买入接盘，卖出获利）

**等价写法（entry/exit + order_levels）**：

```yaml
# conf/executor/market_making/multi_layer_v2.yaml
class_name: market_making

requires:
  - ticker

vars:
  - name: base_spread
    value: '0.0002 * mid_price'

# 入场订单（趋近）
entry_order_levels: 3
entry_order:
  spread: 'base_spread * abs(level)'
  order_usd: '50 * abs(level)'
  refresh_tolerance: 0.8
  timeout: 1h

# 出场订单（远离）
exit_order_levels: 3
exit_order:
  spread: 'base_spread * abs(level)'
  order_usd: '50 * abs(level)'
  refresh_tolerance: 0.8
  timeout: 1h
```

> **注意**：使用 entry/exit 写法时，不需要根据 level 判断 reverse，语义更清晰。

---

## 4. PCAExecutor - 价格成本平均执行器

用于金字塔式加仓/减仓策略。

### 4.1 基础 PCA

```yaml
# conf/executor/pca/basic.yaml
class_name: pca

requires:
  - ticker
  - rsi

vars:
  - name: delta_position_usd
    value: 'current_position_usd - position_usd'

conditional_vars:
  center_price:
    value: mid_price
    on: 'rsi[-1] < 30 or rsi[-1] > 70'
    default: null

reset: 'abs(delta_position_usd) < 50'

# 入场订单
entry_order_levels: 10
entry_order:
  vars:
    - name: direction
      value: '1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else null)'
    - name: spread
      value: '0.0002 * mid_price * (entry_level ** 2 + entry_level)'

  condition: 'direction is not null'
  price: 'center_price - direction * spread'
  order_amount: '0.01 * (entry_level ** 2 + entry_level)'
  refresh_tolerance: 1.0
  timeout: 7d

# 出场订单
exit_order_levels: 1
exit_order:
  vars:
    - name: direction
      value: '-1 if delta_position_usd > 0 else 1'

  condition: 'abs(delta_position_usd) > 50'
  price: '(1 - 0.01 * direction) * average_entry_price'
  order_amount: '-delta_position_amount'
  refresh_tolerance: 0.5
  timeout: 1h
```

**PCA 内置变量**：
- `entry_level`: 当前入场档位（0-based）
- `total_entry_amount`: 累计入场数量
- `total_entry_usd`: 累计入场金额
- `average_entry_price`: 平均入场价格
- `delta_position_amount`: 当前仓位数量偏差

### 4.2 动态档位间距

```yaml
# conf/executor/pca/dynamic_spacing.yaml
class_name: pca

requires:
  - ticker
  - volatility

vars:
  - name: vol_multiplier
    value: 'clip(volatility / 0.01, 0.5, 3.0)'  # 波动率归一化

entry_order_levels: 10
entry_order:
  vars:
    - name: spacing
      value: '0.0002 * vol_multiplier * (entry_level + 1)'

  price: 'center_price - spacing * mid_price'
  order_amount: '0.01 * (entry_level + 1)'
  timeout: 7d
```

**说明**：档位间距根据波动率动态调整。

---

## 5. SmartExecutor - 智能路由执行器

根据条件智能选择执行方式。

### 5.1 基于流动性路由

```yaml
# conf/executor/smart/liquidity_based.yaml
class_name: smart

requires:
  - ticker

vars:
  - name: spread_pct
    value: '(best_ask - best_bid) / mid_price'
  - name: is_liquid
    value: 'spread_pct < 0.0005'

routes:
  # 高流动性：市价单
  - condition: 'is_liquid and abs(delta_usd) < 5000'
    executor: market/basic

  # 中等流动性：限价单
  - condition: 'spread_pct < 0.002'
    executor: limit/fixed_spread

  # 低流动性：做市策略
  - condition: 'spread_pct >= 0.002'
    executor: market_making/symmetric
```

### 5.2 基于紧急度路由

```yaml
# conf/executor/smart/urgency_based.yaml
class_name: smart

requires:
  - ticker

vars:
  - name: urgency
    value: 'speed'  # 来自 Strategy

routes:
  # 极高紧急度：市价单
  - condition: 'urgency > 0.8'
    executor: market/basic

  # 高紧急度：小 spread 限价单
  - condition: 'urgency > 0.5'
    executor: limit/small_spread

  # 中等紧急度：正常限价单
  - condition: 'urgency > 0.2'
    executor: limit/fixed_spread

  # 低紧急度：做市策略
  - condition: 'urgency <= 0.2'
    executor: market_making/multi_layer
```

### 5.3 基于仓位大小路由

```yaml
# conf/executor/smart/size_based.yaml
class_name: smart

requires:
  - ticker

vars:
  - name: delta_abs
    value: 'abs(delta_usd)'

routes:
  # 小单：市价单
  - condition: 'delta_abs < 100'
    executor: market/basic

  # 中单：限价单
  - condition: 'delta_abs < 1000'
    executor: limit/fixed_spread

  # 大单：分批 + 做市
  - condition: 'delta_abs >= 1000'
    executor: market_making/multi_layer
```

---

## 6. 高级技巧

### 6.1 动态订单金额

```yaml
vars:
  - name: position_ratio
    value: 'abs(delta_usd) / max_position_usd'
  - name: dynamic_size
    value: '100 + position_ratio * 400'  # 100-500 USD

order:
  order_usd: 'dynamic_size'
```

### 6.2 时间衰减

```yaml
conditional_vars:
  order_start_time:
    value: 'current_timestamp'
    on: 'abs(delta_usd) > 10'
    default: 'current_timestamp'

vars:
  - name: elapsed
    value: 'current_timestamp - order_start_time'
  - name: urgency_factor
    value: 'min(elapsed / 60, 1.0)'  # 1 分钟后完全紧急
  - name: adaptive_spread
    value: 'base_spread * (1 - urgency_factor * 0.5)'
```

### 6.3 订单簿不平衡调整

```yaml
requires:
  - orderbook

vars:
  - name: imbalance
    value: '(bid_volume - ask_volume) / (bid_volume + ask_volume)'
  - name: imbalance_adjustment
    value: 'imbalance * base_spread * 0.3'
  - name: adjusted_bid_spread
    value: 'base_spread - imbalance_adjustment'
  - name: adjusted_ask_spread
    value: 'base_spread + imbalance_adjustment'
```

### 6.4 波动率过滤

```yaml
requires:
  - volatility

vars:
  - name: vol_percentile
    value: 'volatility / historical_avg_volatility'
  - name: is_volatile
    value: 'vol_percentile > 2.0'

order:
  condition: 'not is_volatile or abs(delta_usd) > 1000'
  order_usd: 'min(abs(delta_usd), 500) if is_volatile else abs(delta_usd)'
```

---

## 7. 配置模式对比

| Executor | 配置复杂度 | 适用场景 | 成交确定性 |
|----------|----------|---------|----------|
| MarketExecutor | 低 | 紧急调仓 | 高 |
| LimitExecutor | 中 | 一般调仓 | 中 |
| MarketMakingExecutor | 中 | 做市策略 | 中 |
| PCAExecutor | 高 | 金字塔加仓 | 低 |
| SmartExecutor | 高 | 复杂路由 | 动态 |

---

## 8. 相关文档

- [Feature 0010: Executor vars 系统](../features/0010-executor-vars-system.md)
- [Feature 0008: Strategy 数据驱动](../features/0008-strategy-data-driven.md)
- [docs/executor.md](../docs/executor.md)
- [Example 001: 稳定币做市](./001-stablecoin-market-making.md)
