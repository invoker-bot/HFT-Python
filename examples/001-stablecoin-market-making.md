# 稳定币交易对做市策略

本文档介绍如何使用数据驱动配置实现稳定币交易对的做市策略。

## 场景描述

**交易对**：OKX 上的 USDG/USDT 现货
**目标**：维持账户中 USDG 占 60%，USDT 占 40% 的平衡状态

## 策略配置概览

```
┌─────────────────────────────────────────────────────────────┐
│                    App 配置结构                              │
├─────────────────────────────────────────────────────────────┤
│  conf/app/stablecoin/main.yaml                              │
│  ├── exchanges:                                             │
│  │   └── okx (现货账户)                                      │
│  ├── strategies:                                            │
│  │   └── keep_positions (维持目标仓位)                       │
│  ├── executors:                                             │
│  │   ├── grid (网格交易)                                     │
│  │   ├── rebalance (再平衡)                                  │
│  │   └── pca (金字塔加仓)                                    │
│  └── indicators:                                            │
│      └── ticker (价格数据)                                   │
└─────────────────────────────────────────────────────────────┘
```

## 方案一：网格交易

### 策略说明

在当前价格上下各挂 3 个限价单，形成网格：
- 买单：价格 -0.02%, -0.04%, -0.06%
- 卖单：价格 +0.02%, +0.04%, +0.06%
- 订单过期时间 7 天
- `refresh_tolerance=1` 表示价格偏离不触发撤单

### 配置文件

#### App 配置

```yaml
# conf/app/stablecoin/grid.yaml
exchanges:
  - okx/spot_a
  - okx/spot_b

strategies:
  - stablecoin/grid_positions

executor: stablecoin/grid_executor

indicators:
  ticker:
    class: TickerDataSource
    params:
      window: 60 # (或者写60s)
    ready_condition: "timeout < 5"
  equation:
    class: MedalEquationDataSource
    params:
      window: null  
    ready_condition: "timeout < 15"
```

#### Strategy 配置

```yaml
# conf/strategy/stablecoin/grid_positions.yaml
class_name: keep_positions
requires:
  - equation
# 目标仓位：USDG 占 60%
targets:
  - exchange: okx/a  # 这是可选选项，指的是path的实例, 默认为 *，表示app下的所有
    exchange_class: 'okx'  # 只有class_name匹配为okx的实例，默认为 *，表示app下的所有
    symbol: USDG/USDT
    position_usd: '0.6 * equation_usd'  # 60% 现货仓位（支持expr），equation_usd为注入的变量之一 
    max_position_usd: '0.8 * equation_usd'  # position_usd 是中性仓位，max_position_usd是最大仓位
    # 这里有一些未完成的特性：
    # 有一些是 GlobalIndicator
    # 有一些可能是 ExchangeClassLevelIndicator
    # 有一些可能是 ExchangePathLevelIndicator
    # 有一些可能是 PairLevelIndicator
    # 这是一个MedalEquationDataSource(BaseIndicator)，这很显然是一个ExchangePathLevelIndicator
    # 目前可以用 equation_usd = medal_fetch_total_balance_usd()，这是一个注入值
    # 这里需要说明的是不同平台计算的方式可能不一样
    # 对于okx账户，现货和合约是一体的 equation_usd
    # 对于binance账户，为现货和合约价值之和
    speed: 0.1         # 调仓速度（支持expr）

```

#### Executor 配置

有两种配置方式：

**方式一：使用 orders 显式列表**

```yaml
# conf/executor/stablecoin/grid_executor.yaml
class_name: limit

requires:
  - ticker

vars:
  - name: q
    value: 'clip((current_position_usd - position_usd) / max_position_usd, -1, 1)'

# 使用 orders 数组模拟网格（每侧 3 档）
# entry_orders: 趋近目标仓位
# exit_orders: 偏离目标仓位（做市）
entry_orders:
  - spread: '0.0002 * mid_price'
    order_usd: '100 - q * 50'
    timeout: 7d
    refresh_tolerance: 1.0
  - spread: '0.0004 * mid_price'
    order_usd: '100 - q * 50'
    timeout: 7d
    refresh_tolerance: 1.0
  - spread: '0.0006 * mid_price'
    order_usd: '100 - q * 50'
    timeout: 7d
    refresh_tolerance: 1.0

exit_orders:
  - spread: '0.0002 * mid_price'
    order_usd: '100 + q * 50'
    timeout: 7d
    refresh_tolerance: 1.0
  - spread: '0.0004 * mid_price'
    order_usd: '100 + q * 50'
    timeout: 7d
    refresh_tolerance: 1.0
  - spread: '0.0006 * mid_price'
    order_usd: '100 + q * 50'
    timeout: 7d
    refresh_tolerance: 1.0
```

**方式二：使用 order_levels 扩展**

```yaml
# conf/executor/stablecoin/grid_executor_v2.yaml
class_name: limit

requires:
  - ticker

vars:
  - name: q
    value: 'clip((current_position_usd - position_usd) / max_position_usd, -1, 1)'

# 入场订单（趋近目标）
entry_order_levels: 3
entry_order:
  spread: '0.0002 * mid_price * abs(level)'
  order_usd: '100 - q * 50'
  timeout: 7d
  refresh_tolerance: 1.0

# 出场订单（偏离目标）
exit_order_levels: 3
exit_order:
  spread: '0.0002 * mid_price * abs(level)'
  order_usd: '100 + q * 50'
  timeout: 7d
  refresh_tolerance: 1.0
```

### 配置说明

| 字段 | 说明 |
|------|------|
| `entry_orders` | 趋近目标仓位的订单（当前 < 目标时买入，当前 > 目标时卖出） |
| `exit_orders` | 偏离目标仓位的订单（做市单，提供流动性） |
| `order_levels: 3` | 生成 level ∈ {-3, -2, -1, 1, 2, 3} |
| `spread` | 价差（绝对值或表达式） |
| `order_usd` | 订单金额 |
| `refresh_tolerance` | 刷新容忍度（价差偏离 > tolerance * spread 时刷新） |

### 需要的 Feature

> 参考：`features/0008-strategy-data-driven.md`（vars、变量注入）
> 参考：`features/0010-executor-vars-system.md`（统一 order 配置）

---

## 方案二：屯币再平衡

### 策略说明

维持 USDG 占仓位 60% 的目标比例：
- 仅在偏离目标时挂单
- 挂在买一/卖一价（最优价格）
- 成交后自动调整，保持平衡

### 配置文件

App 配置与方案一类似，主要区别在 Strategy 和 Executor 配置。

#### Strategy 配置

```yaml
# conf/strategy/stablecoin/rebalance_positions.yaml
class_name: keep_positions
requires:
  - equation

targets:
  - exchange: '*'
    exchange_class: okx
    symbol: USDG/USDT
    position_usd: '0.6 * equation_usd'
    max_position_usd: 'equation_usd'
    speed: 0.05
```

#### Executor 配置

```yaml
# conf/executor/stablecoin/rebalance_executor.yaml
class_name: limit

requires:
  - ticker

# 仅在偏离超过 1% 时执行
condition: 'abs(current_position_usd - position_usd) / position_usd > 0.01'

order_levels: 1
order:
  spread: 0                          # 挂在买一/卖一价
  order_usd: 'abs(current_position_usd - position_usd)'
  timeout: 1h
  refresh_tolerance: 0.5
```

---

## 方案三：PCA 金字塔加仓

### 策略说明

Price Cost Averaging（价格成本平均）策略：
- 基于 RSI 信号触发入场
- 金字塔式加仓：每档数量递增
- 价格回升时止盈出场

有两种实现方式，各有优劣。

---

### 方式一：keep_positions + PCAExecutor

Strategy 始终返回目标仓位 0，由 PCAExecutor 自主管理入场/出场逻辑。

#### App 配置

```yaml
# conf/app/stablecoin/pca.yaml
exchanges:
  - okx/spot_main

strategies:
  - stablecoin/pca_zero_position

executor: stablecoin/pca_executor

indicators:
  ticker:
    class: TickerDataSource
    params:
      window: 60.0
    ready_condition: "timeout < 5"
  rsi:
    class: RSIIndicator
    params:
      period: 14
    ready_condition: "len(data) >= 14"
```

#### Strategy 配置

```yaml
# conf/strategy/stablecoin/pca_zero_position.yaml
class_name: keep_positions

targets:
  - symbol: USDG/USDT
    position_usd: 0  # 始终返回 0，由 Executor 管理
    speed: 0.1
```

#### Executor 配置

```yaml
# conf/executor/stablecoin/pca_executor.yaml
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
    on: 'rsi[-1] < 30 or rsi[-1] > 70 or duration > 7 * 24 * 3600'
    default: null

reset: 'abs(delta_position_usd) < 50'

# === 入场订单 ===
entry_order_levels: 10
entry_order:
  vars:
    - name: direction
      value: '1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else null)'
    - name: spread_value
      value: '0.0002 * mid_price * (entry_level ** 2 + entry_level)'
  condition: 'direction is not null'
  spread: 'spread_value'
  price: 'center_price - direction * spread_value'
  order_amount: '0.01 * (entry_level ** 2 + entry_level) * direction'
  refresh_tolerance: 1.0
  timeout: 7d

# === 出场订单 ===
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

#### PCAExecutor 特殊行为

| 特性 | 说明 |
|------|------|
| `entry_level` 追踪 | 记住当前档位，避免重复吃单 |
| 订单状态追踪 | 成交/取消后 level + 1，直到 max levels |
| 统计变量 | `total_entry_amount`, `total_entry_usd`, `average_entry_price` |
| `reset` 条件 | 满足时重置所有统计，entry_level 归零 |

---

### 方式二：keep_positions + LimitExecutor

由 Strategy 利用 `vars` / `conditional_vars` 计算动态目标仓位，Executor 只负责执行。

**优点**：Strategy 和 Executor 职责分离，复用现有 LimitExecutor
**缺点**：Strategy 需要维护状态（中心价格、基准仓位）

#### Strategy 配置

```yaml
# conf/strategy/stablecoin/pca_dynamic.yaml
class_name: keep_positions

requires:
  - ticker
  - rsi

vars:
  - name: current_amount
    value: current_position_amount
  - name: price_drop_levels
    value: int(max(0, (center_price - mid_price) / (center_price * 0.0002)))
  - name: target_delta
    value: sum([0.01 * (i ** 2 + i) for i in range(1, price_drop_levels + 1)])

conditional_vars:
  center_price:
    value: mid_price
    on: rsi[-1] < 30 or rsi[-1] > 70
    default: mid_price
  base_amount:
    value: current_amount
    on: rsi[-1] < 30 or rsi[-1] > 70
    default: 0
  direction:
    value: 1 if rsi[-1] < 30 else -1
    on: rsi[-1] < 30 or rsi[-1] > 70
    default: 0

targets:
  - symbol: USDG/USDT
    position_amount: base_amount + direction * target_delta
    speed: 0.1
```

#### Executor 配置

```yaml
# conf/executor/stablecoin/pca_limit.yaml
class_name: limit

requires:
  - ticker

vars:
  - name: position_amount
    value: 'sum(strategies["position_amount"])'
  - name: delta_amount
    value: 'position_amount - current_position_amount'

condition: 'abs(delta_amount) > 0.001'

order_levels: 1
order:
  spread: 0
  order_amount: 'delta_amount'
  timeout: 1h
  refresh_tolerance: 0.5
```

---

### 两种方式对比

| 特性 | 方式一 (PCAExecutor) | 方式二 (keep_positions) |
|------|---------------------|------------------------|
| 状态管理 | Executor 内部 | Strategy conditional_vars |
| 复杂度 | 高（专用 Executor） | 低（复用 LimitExecutor） |
| 灵活性 | 高（entry/exit 分离） | 中（统一 position_amount） |
| 适用场景 | 复杂 PCA 逻辑 | 简单 PCA 逻辑 |

---

### 需要的 Feature

> 参考：`features/0010-executor-vars-system.md`（vars、conditional_vars、order 展开机制）
> 参考：`features/0008-strategy-data-driven.md`（Strategy vars/conditional_vars、通用字典输出）

---

## 总结：三种方案对比

| 方案 | 适用场景 | 交易频率 | 风险 |
|------|----------|----------|------|
| 网格交易 | 震荡行情 | 高 | 中（单边行情亏损） |
| 屯币再平衡 | 长期持有 | 低 | 低（被动调仓） |
| PCA 金字塔 | 抄底策略 | 中 | 高（需要资金支撑） |

## Feature 依赖汇总

本示例需要以下 Feature 支持：

| Feature | 文件 | 说明 |
|---------|------|------|
| Strategy 数据驱动 | `features/0008-strategy-data-driven.md` | requires、position_usd 表达式、Indicator 层级 |
| Executor vars 系统 | `features/0010-executor-vars-system.md` | vars、conditional_vars、统一 order 配置 |
| 现货模式支持 | 部分实现 | Exchange `mode: spot` 配置 |

## Indicator 层级说明

| 层级 | 作用域 | 示例 |
|------|--------|------|
| Global | 全局唯一 | GlobalFundingRateIndicator |
| ExchangePath | 按交易所实例 | MedalEquationDataSource |
| Pair | 按交易对 | TickerDataSource, RSIIndicator |

## 相关文档

- [docs/executor.md](../docs/executor.md) - Executor 设计文档
- [docs/indicator.md](../docs/indicator.md) - Indicator 统一架构
- [docs/datasource.md](../docs/datasource.md) - DataSource 数据源
