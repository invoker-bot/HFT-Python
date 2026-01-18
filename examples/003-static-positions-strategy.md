# StaticPositionsStrategy 配置详解

本文档详细说明 `StaticPositionsStrategy`（静态仓位策略）的配置方式。

**静态仓位策略**的核心特点是：目标仓位是**预先配置好的固定值**，而不是根据市场数据动态计算的。

---

## 1. 基本概念

`StaticPositionsStrategy` 用于维持指定的目标仓位：

```yaml
class_name: static_positions

targets:
  - symbol: BTC/USDT
    position_usd: 1000    # 目标仓位：固定 1000 USD
    speed: 0.5            # 调仓速度
```

### 核心字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | string | 交易对（必填） |
| `position_usd` | number | 目标仓位金额（USD） |
| `speed` | number | 调仓速度（0-1），默认 0.5 |
| `exchange` | string | 交易所实例路径，默认 `"*"`（匹配所有） |
| `exchange_class` | string | 交易所类型，默认 `"*"`（匹配所有） |
| `condition` | string | 条件表达式，默认 `null`（视为 True） |

---

## 2. 配置方式

### 2.1 targets 列表写法

直接列出每个目标：

```yaml
class_name: static_positions
name: my_static_strategy

targets:
  - symbol: BTC/USDT
    position_usd: 1000
    speed: 0.5

  - symbol: ETH/USDT
    position_usd: 500
    speed: 0.3
```

### 2.2 target_pairs 展开式写法（推荐）

当多个交易对共享相同配置时，使用展开式写法更简洁：

```yaml
class_name: static_positions
name: multi_coin_strategy

target_pairs:
  - BTC/USDT
  - ETH/USDT
  - SOL/USDT

target:
  position_usd: 1000
  speed: 0.5
```

等价于：

```yaml
targets:
  - symbol: BTC/USDT
    position_usd: 1000
    speed: 0.5
  - symbol: ETH/USDT
    position_usd: 1000
    speed: 0.5
  - symbol: SOL/USDT
    position_usd: 1000
    speed: 0.5
```

### 2.3 target_pairs 格式详解

```yaml
target_pairs:
  # 格式 1：字符串简写（最常用）
  - BTC/USDT

  # 格式 2：指定交易所类型
  - symbol: ETH/USDT
    exchange_class: okx

  # 格式 3：指定交易所实例
  - symbol: SOL/USDT
    exchange: okx/main

  # 格式 4：单独覆盖某个字段
  - symbol: DOGE/USDT
    position_usd: 200    # 覆盖 target 模板中的值
```

### 2.4 混合写法

`target_pairs` 和 `targets` 可以同时使用：

```yaml
class_name: static_positions

# 展开式：这些交易对使用相同配置
target_pairs:
  - BTC/USDT
  - ETH/USDT

target:
  position_usd: 1000
  speed: 0.5

# 额外的独立配置
targets:
  - symbol: SOL/USDT
    position_usd: 300
    speed: 0.8
```

---

## 3. 交易所过滤

### 3.1 匹配所有交易所（默认）

```yaml
targets:
  - symbol: BTC/USDT
    exchange: '*'           # 匹配所有交易所实例
    exchange_class: '*'     # 匹配所有交易所类型
    position_usd: 1000
```

### 3.2 按交易所类型过滤

```yaml
targets:
  # 仅在 OKX 交易所
  - symbol: BTC/USDT
    exchange_class: okx
    position_usd: 1000

  # 仅在 Binance 交易所
  - symbol: ETH/USDT
    exchange_class: binance
    position_usd: 500
```

### 3.3 按交易所实例过滤

```yaml
targets:
  # 指定交易所实例
  - symbol: BTC/USDT
    exchange: okx/main
    position_usd: 1000

  - symbol: BTC/USDT
    exchange: okx/sub
    position_usd: 500
```

---

## 4. 调仓速度（speed）

`speed` 控制每次调仓的幅度，取值 0-1：

| speed | 含义 | 适用场景 |
|-------|------|---------|
| 0.1 | 每次调整差值的 10% | 长期持仓，平滑调整 |
| 0.5 | 每次调整差值的 50% | 一般策略 |
| 0.8 | 每次调整差值的 80% | 快速响应 |
| 1.0 | 立即调整到位 | 紧急调仓 |

**示例**：目标 1000 USD，当前 600 USD，差值 400 USD
- `speed: 0.5` → 本次调整 200 USD
- `speed: 1.0` → 本次调整 400 USD

```yaml
targets:
  # 保守策略：缓慢调仓
  - symbol: BTC/USDT
    position_usd: 10000
    speed: 0.1

  # 激进策略：快速调仓
  - symbol: ETH/USDT
    position_usd: 5000
    speed: 0.8
```

---

## 5. 完整配置示例

### 5.1 单币种固定仓位

```yaml
# conf/strategy/static_positions/btc_only.yaml
class_name: static_positions
name: btc_fixed_position
interval: 5.0
exit_on_target: false
tolerance: 0.05

targets:
  - symbol: BTC/USDT:USDT
    position_usd: 1000
    speed: 0.5
```

### 5.2 多币种固定仓位（展开式）

```yaml
# conf/strategy/static_positions/multi_coin.yaml
class_name: static_positions
name: multi_coin_fixed
interval: 5.0
exit_on_target: false
tolerance: 0.05

target_pairs:
  - BTC/USDT:USDT
  - ETH/USDT:USDT
  - SOL/USDT:USDT

target:
  position_usd: 1000
  speed: 0.5
```

### 5.3 不同仓位的多币种配置

```yaml
# conf/strategy/static_positions/weighted_portfolio.yaml
class_name: static_positions
name: weighted_portfolio
interval: 5.0
exit_on_target: false
tolerance: 0.05

targets:
  - symbol: BTC/USDT:USDT
    position_usd: 5000    # BTC 占大头
    speed: 0.3

  - symbol: ETH/USDT:USDT
    position_usd: 3000
    speed: 0.3

  - symbol: SOL/USDT:USDT
    position_usd: 1000
    speed: 0.5

  - symbol: DOGE/USDT:USDT
    position_usd: 500
    speed: 0.8
```

### 5.4 跨交易所配置

```yaml
# conf/strategy/static_positions/cross_exchange.yaml
class_name: static_positions
name: cross_exchange_btc
interval: 5.0

targets:
  # OKX 交易所
  - symbol: BTC/USDT:USDT
    exchange_class: okx
    position_usd: 2000
    speed: 0.5

  # Binance 交易所
  - symbol: BTC/USDT
    exchange_class: binance
    position_usd: 1000
    speed: 0.5
```

### 5.5 中性仓位（不持仓）

```yaml
# conf/strategy/static_positions/neutral.yaml
class_name: static_positions
name: neutral_btc
interval: 5.0
exit_on_target: false

target_pairs:
  - BTC/USDT:USDT

target:
  position_usd: 0    # 目标仓位为 0，即不持仓
  speed: 0.5
```

---

## 6. Condition 门控

Strategy 支持 `condition` 表达式门控，控制 target 是否生效。

### 6.1 全局 condition

定义在 Strategy 顶层，对所有 targets 生效：

```yaml
class_name: static_positions
name: guarded_strategy
interval: 5.0

# 全局条件：仅当账户权益 > 1000 USD 时才执行
condition: equity_usd > 1000

target_pairs:
  - BTC/USDT:USDT

target:
  position_usd: 1000
  speed: 0.5
```

### 6.2 Target 级 condition

定义在 `target` 或 `targets[*]` 内，仅对该 target 生效：

```yaml
class_name: static_positions
name: conditional_targets
interval: 5.0

targets:
  # 仅当 RSI < 30 时持有 BTC
  - symbol: BTC/USDT:USDT
    condition: rsi[-1] < 30
    position_usd: 1000
    speed: 0.5

  # 仅当 RSI > 70 时持有 ETH
  - symbol: ETH/USDT:USDT
    condition: rsi[-1] > 70
    position_usd: 500
    speed: 0.3
```

### 6.3 condition 求值规则

| 情况 | 结果 |
|------|------|
| 未定义 / `null` | 视为 True（target 生效） |
| 求值为 `True` | target 生效 |
| 求值为 `False` | target 被忽略（不输出） |
| 求值异常 / `None` | 视为 False（fail-safe，target 被忽略） |

**组合规则**：全局 condition 和 target condition 做 **AND** 门控：
- 两者都为 True → target 生效
- 任一为 False → target 被忽略

### 6.4 展开式中使用 condition

`target_pairs` + `target` 展开式中，`condition` 也可作为模板字段：

```yaml
class_name: static_positions

target_pairs:
  - BTC/USDT:USDT
  - ETH/USDT:USDT
  # 覆盖模板中的 condition
  - symbol: SOL/USDT:USDT
    condition: volume_24h > 1000000

target:
  condition: equity_usd > 500    # 默认 condition（可被覆盖）
  position_usd: 1000
  speed: 0.5
```

---

## 7. 策略级配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `class_name` | string | - | 必须为 `static_positions` |
| `name` | string | - | 策略名称 |
| `interval` | float | 5.0 | tick 间隔（秒） |
| `exit_on_target` | bool | true | 达到目标后是否退出策略 |
| `tolerance` | float | 0.05 | 仓位容忍度（5% 内视为达标） |
| `condition` | string | null | 全局条件表达式（null 视为 True） |

---

## 8. 多 Strategy 聚合

当 App 配置多个 Strategy 时，StrategyGroup 会聚合它们的输出：

```yaml
# conf/app/multi_strategy.yaml
strategies:
  - static_positions/strategy_a    # position_usd: 1000
  - static_positions/strategy_b    # position_usd: 500
```

聚合结果（供 Executor 使用）：

```python
{
    ("okx/main", "BTC/USDT"): {
        "position_usd": [1000, 500],  # 列表形式
        "speed": [0.5, 0.3]
    }
}
```

Executor 的默认聚合行为：
- `target_usd = sum(position_usd)` → 1500
- `speed = weighted_avg(speed, position_usd)` → 加权平均

---

## 9. Scope 系统集成（可选）

从 Feature 0012 开始，Strategy 支持 Scope 系统，可以实现更强大的多层级变量计算。

### 9.1 基本 Scope 配置

```yaml
class_name: static_positions

# Scope 链路定义
links:
  - ["global", "exchange_class", "exchange", "trading_pair"]

# Scope 变量配置
scopes:
  global:
    vars:
      max_total_position_usd: 10000
      default_speed: 0.5
```

### 9.2 使用 Scope 变量

```yaml
class_name: static_positions

links:
  - ["global", "exchange_class", "exchange", "trading_pair"]

scopes:
  global:
    vars:
      max_position_usd: 2000
      speed_multiplier: 1.0

  trading_pair:
    vars:
      position_usd: max_position_usd * 0.5
      speed: default_speed * speed_multiplier

target_pairs:
  - BTC/USDT
  - ETH/USDT

target:
  position_usd: position_usd  # 使用 Scope 变量
  speed: speed                # 使用 Scope 变量
```

### 9.3 条件变量（conditional_vars）

```yaml
scopes:
  global:
    vars:
      base_position: 1000

  trading_pair:
    vars:
      - name: position_usd
        value: >
          base_position * 2 if symbol == "BTC/USDT" else
          base_position * 1.5 if symbol == "ETH/USDT" else
          base_position
```

---

## 10. 相关文档

- [Feature 0011: Strategy Target 展开式与去特殊化](../features/0011-strategy-target-expansion.md)
- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
- [docs/strategy.md](../docs/strategy.md)
- [Example 002: Executor 配置详解](./002-executor-configurations.md)
- [Example 004: MarketNeutralPositions 配置详解](./004-market-neutral-positions-strategy.md)

