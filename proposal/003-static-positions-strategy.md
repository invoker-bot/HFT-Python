# Proposal 003: StaticPositions 策略

## 1. 背景与动机

### 1.1 当前问题

旧的 `KeepPositionsStrategy` 存在以下问题：
- 配置格式不统一（旧格式 vs 新格式）
- 缺乏数据驱动能力
- 不支持多交易所匹配
- 表达式求值能力有限

### 1.2 设计目标

实现 **StaticPositions** 策略（原 `keep_positions`），这是一个静态目标仓位策略，用于保持固定的目标仓位。

**核心目标**：
- 支持静态仓位配置
- 支持数据驱动的动态仓位计算
- 基于 Scope 系统实现灵活的变量管理
- 支持多交易所/多交易对配置

**策略特性**：
1. **静态仓位**：直接配置目标仓位（USD 或数量）
2. **动态仓位**：基于表达式计算目标仓位
3. **多交易所支持**：支持 `*` 通配符匹配多个交易所
4. **灵活的退出机制**：支持达到目标后自动退出

---

## 2. 使用场景

### 2.1 场景 1：简单持仓

```yaml
# 保持固定的 BTC 多仓
positions_usd:
  BTC/USDT:USDT: 1000
```

**用途**：
- 长期持有某个币种
- 简单的定投策略

### 2.2 场景 2：对冲持仓

```yaml
# OKX 做多，Binance 做空
targets:
  - exchange_id: okx/main
    symbol: BTC/USDT:USDT
    position_usd: 1000

  - exchange_id: binance/main
    symbol: BTC/USDT:USDT
    position_usd: -1000
```

**用途**：
- 跨交易所对冲
- 降低单边风险

### 2.3 场景 3：动态仓位（基于账户权益）

```yaml
# 仓位 = 账户权益 × 风险比例
targets:
  - exchange_id: "*"
    symbol: BTC/USDT:USDT
    position_usd: "equation_usd * risk_ratio"
```

**用途**：
- 根据账户权益动态调整仓位
- 风险管理

### 2.4 场景 4：基于指标的动态仓位

```yaml
# 根据 RSI 指标调整方向
targets:
  - exchange_id: "*"
    symbol: BTC/USDT:USDT
    position_usd: "max_position_usd * direction"
    # direction 在 Scope vars 中计算：
    # direction = 1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else 0)
```

**用途**：
- 基于技术指标的策略
- 自动化交易

---

## 3. 核心概念

### 3.1 Position（仓位）

**定义**：目标仓位，可以用 USD 或数量表示。

**类型**：
- `position_usd`：以 USD 计价的仓位（推荐）
- `position_amount`：以合约数量计价的仓位

**符号**：
- 正数：做多
- 负数：做空
- 0：平仓

### 3.2 Speed（执行速度）

**定义**：执行紧急度，范围 `[0, 1]`。

**含义**：
- `0.0`：不紧急，慢慢执行
- `0.5`：中等紧急度
- `1.0`：非常紧急，尽快执行

**用途**：
- 控制订单执行速度
- 平衡成交速度和滑点

### 3.3 Tolerance（容忍度）

**定义**：当前仓位与目标仓位的偏差容忍度，范围 `[0, 1]`。

**含义**：
- `0.05`：偏差 > 5% 时才调整
- `0.1`：偏差 > 10% 时才调整

**用途**：
- 避免频繁调仓
- 降低交易成本

### 3.4 Exit on Target（达标退出）

**定义**：当所有交易对的仓位都达到目标时，策略自动退出。

**配置**：
```yaml
exit_on_target: true  # 达标后退出
exit_on_target: false # 持续运行（默认）
```

**用途**：
- 一次性建仓任务
- 自动化脚本

---

## 4. 配置格式

### 4.1 旧格式（向后兼容）

```yaml
# conf/strategy/static_positions/<name>.yaml
class_name: static_positions

exchange_path: okx/main
exit_on_target: true
tolerance: 0.05
speed: 0.8

positions_usd:
  BTC/USDT:USDT: 1000
  ETH/USDT:USDT: -500
```

**特点**：
- 简单直观
- 只支持单个交易所
- 静态配置

### 4.2 新格式（推荐）

```yaml
# conf/strategy/static_positions/<name>.yaml
class_name: static_positions

requires:
  - equation  # 账户权益数据源

links:
  - id: main
    value: [global, exchange_class, exchange, trading_pair]

targets:
  - exchange_id: "*"
    exchange_class: okx
    symbol: BTC/USDT:USDT
    position_usd: "equation_usd * 0.6"
    speed: 0.5

  - exchange_id: okx/spot_a
    symbol: USDG/USDT
    position_amount: "base_amount + delta"
    custom_field: "some_expression"

exit_on_target: false
tolerance: 0.05
```

**特点**：
- 支持表达式
- 支持多交易所
- 支持自定义字段
- 数据驱动

### 4.3 展开式写法（简化配置）

```yaml
# conf/strategy/static_positions/<name>.yaml
class_name: static_positions

target_pairs:
  - BTC/USDT:USDT
  - ETH/USDT:USDT

target:
  exchange_class: okx
  position_usd: "1000"
  speed: 0.5
```

**说明**：
- `target_pairs + target` 会在配置加载时展开为 `targets` 列表
- 减少重复配置
- 适合批量配置相同参数的交易对

---
## 5. targets 匹配规则

### 5.1 exchange_id 匹配

- `'*'`：匹配所有 exchange
- `'okx/main'`：精确匹配 exchange path
- `'okx/*'`：模式匹配（支持 fnmatch 语法）

**兼容字段**：`exchange` 等价于 `exchange_id`（不推荐继续使用）

### 5.2 exchange_class 匹配

- `'*'`：匹配所有 exchange class
- `'okx'`：精确匹配 exchange class_name
- `'ok*'`：模式匹配

### 5.3 symbol 匹配

- `'*'`：匹配所有 symbol
- `'BTC/USDT:USDT'`：精确匹配
- `'BTC/*'`：模式匹配

### 5.4 匹配示例

```yaml
targets:
  # 匹配所有 okx 交易所的 BTC 交易对
  - exchange_id: '*'
    exchange_class: okx
    symbol: BTC/USDT:USDT
    position_usd: 1000

  # 只匹配特定的 exchange
  - exchange_id: okx/spot_a
    exchange_class: '*'
    symbol: USDG/USDT
    position_usd: 500

  # 匹配所有 BTC 交易对
  - exchange_id: '*'
    symbol: 'BTC/*'
    position_usd: 2000
```

---

## 6. App 配置（Scope 定义）

### 6.1 简单配置

```yaml
# conf/app/<app>.yaml
class_name: app

exchanges:
  - okx/main
  - binance/spot

strategy: static_positions/btc_hold

scopes:
  global:
    class: GlobalScope
    vars:
      - max_position_usd=10000
      - risk_ratio=0.6

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope

  trading_pair:
    class: TradingPairScope
```

### 6.2 带条件变量的配置

```yaml
# conf/app/<app>.yaml
scopes:
  global:
    class: GlobalScope
    vars:
      - max_position_usd=10000

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope

  trading_pair:
    class: TradingPairScope
    vars:
      - name: direction
        value: 1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else 0)
        on: rsi[-1] < 30 or rsi[-1] > 70
        initial_value: 0
      - position_usd=max_position_usd * direction
```

---

## 7. 计算流程

### 7.1 整体流程

```
1. 加载配置
   ↓
2. 构建 Scope 树
   ↓
3. Indicator/DataSource 注入
   ↓
4. 计算 Scope vars
   ↓
5. 匹配 targets
   ↓
6. 计算 target vars
   ↓
7. 检查 target condition
   ↓
8. 输出给 Executor
```

### 7.2 详细步骤

#### 步骤 1：加载配置

- 加载 App 配置（scopes 定义）
- 加载 Strategy 配置（targets 定义）
- 验证配置有效性

#### 步骤 2：构建 Scope 树

根据 `links` 声明，构建 Scope 实例树：

```
global
  └─ exchange_class (okx)
      └─ exchange (okx/main)
          └─ trading_pair (okx/main, BTC/USDT:USDT)
```

#### 步骤 3：Indicator/DataSource 注入

根据 `requires` 声明，注入变量：

```yaml
requires:
  - equation  # 注入 equation_usd 到 exchange scope
  - rsi       # 注入 rsi 到 trading_pair scope
```

#### 步骤 4：计算 Scope vars

沿 link 从前到后计算每一层的 `vars`：

```yaml
global:
  vars:
    - max_position_usd=10000

trading_pair:
  vars:
    - direction=1 if rsi[-1] < 30 else 0
    - position_usd=max_position_usd * direction
```

#### 步骤 5：匹配 targets

对每个 `trading_pair` scope，检查是否匹配任何 target：

```python
for target in targets:
    if match(exchange_id, target.exchange_id) and \
       match(exchange_class, target.exchange_class) and \
       match(symbol, target.symbol):
        # 匹配成功
```

#### 步骤 6：计算 target vars

在匹配的 `trading_pair` scope 中，计算 target 的 vars：

```yaml
target:
  vars:
    - position_usd=max_position_usd * risk_ratio
```

#### 步骤 7：检查 target condition

```python
if eval(target.condition):
    # 输出该 target
```

#### 步骤 8：输出给 Executor

```python
output[(exchange_id, symbol)] = {
    "position_usd": position_usd,
    "speed": speed,
    # ... 其他字段
}
```

---

## 8. 输出格式

### 8.1 旧格式（向后兼容）

```python
TargetPositions = dict[tuple[str, str], tuple[float, float]]
# {(exchange_id, symbol): (position_usd, speed)}
```

**示例**：
```python
{
    ("okx/main", "BTC/USDT:USDT"): (5000.0, 0.5),
    ("okx/main", "ETH/USDT:USDT"): (-2000.0, 0.8),
}
```

### 8.2 新格式（推荐）

```python
StrategyOutput = dict[tuple[str, str], dict[str, Any]]
# {(exchange_id, symbol): {"position_usd": ..., "speed": ..., ...}}
```

**示例**：
```python
{
    ("okx/main", "BTC/USDT:USDT"): {
        "position_usd": 5000.0,
        "position_amount": 0.1,
        "max_position_usd": 10000.0,
        "speed": 0.5,
    },
}
```

**优势**：
- 支持任意自定义字段
- 所有字段都会传递给 Executor
- 聚合到 `strategies` namespace

---

## 9. strategies namespace（单策略口径）

Executor 会接收到一个 `strategies` namespace（list 口径），用于在表达式里统一处理"策略输出字段"：

- 当前 App 仅支持单策略，因此每个字段的列表长度为 `1`
- 仍使用 list：避免把表达式与"是否多策略"绑定；未来如恢复多策略，该口径可自然扩展

**示例**：
```python
# Strategy 输出（单策略）
{("okx/main", "BTC/USDT"): {"position_amount": 0.01}}

# Executor 接收到的 strategies namespace（仍为列表）
strategies["position_amount"] = [0.01]
```

**在 Executor 中使用**：
```yaml
# conf/executor/xxx.yaml
vars:
  - name: position_amount
    value: sum(strategies["position_amount"])
  - name: position_usd
    value: sum(strategies["position_usd"]) if "position_usd" in strategies else 0
```

---

## 10. 退出机制

当策略的 `on_tick()` 返回 `True` 时，触发退出流程：

```
1. Strategy.on_tick() 返回 True
   ↓
2. StrategyGroup.is_finished 变为 True
   ↓
3. StrategyGroup.on_tick() 返回 True
   ↓
4. AppCore.on_tick() 检测到策略完成
   ↓
5. 返回 True → 程序正常退出
```

**触发条件**：
- `exit_on_target = true`
- 所有交易对的仓位都达到目标（在 tolerance 范围内）

---
## 11. 配置示例

### 11.1 简单持仓策略

```yaml
# conf/strategy/static_positions/btc_hold.yaml
class_name: static_positions
exchange_path: okx/main
positions_usd:
  BTC/USDT:USDT: 1000
exit_on_target: true
tolerance: 0.05
speed: 0.8
```

### 11.2 多交易所对冲策略

```yaml
# conf/strategy/static_positions/hedge.yaml
class_name: static_positions

requires:
  - equation

targets:
  # OKX 做多
  - exchange_id: okx/main
    symbol: BTC/USDT:USDT
    position_usd: '0.3 * equation_usd'
    speed: 0.3

  # Binance 做空
  - exchange_id: binance/main
    symbol: BTC/USDT:USDT
    position_usd: '-0.3 * equation_usd'
    speed: 0.3

exit_on_target: false
```

### 11.3 基于 RSI 的动态仓位策略

```yaml
# conf/app/<app>.yaml
scopes:
  global:
    class: GlobalScope
    vars:
      - risk_ratio=0.6

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope

  trading_pair:
    class: TradingPairScope
    vars:
      - name: direction
        value: 1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else 0)
        on: rsi[-1] < 30 or rsi[-1] > 70
        initial_value: 0
```

```yaml
# conf/strategy/static_positions/rsi_strategy.yaml
class_name: static_positions

requires:
  - equation
  - rsi

links:
  - id: main
    value: [global, exchange_class, exchange, trading_pair]

targets:
  - exchange_id: '*'
    exchange_class: okx
    symbol: BTC/USDT:USDT
    position_usd: 'risk_ratio * equation_usd * direction'
    speed: 0.5

exit_on_target: false
```

---
## 12. 相关文档

- [Proposal 001: Scope/VirtualMachine 数据驱动系统](./001-scope-vm-data-driven-system.md)
- [Feature 0008: Strategy 数据驱动](../features/0008-strategy-data-driven.md)
- [Feature 0011: Strategy Target 展开式与去特殊化](../features/0011-strategy-target-expansion.md)
- [docs/strategy.md](../docs/strategy.md)
- [Example 003: StaticPositions 配置详解](../examples/003-static-positions-strategy.md)

---

## 13. 与 MarketNeutralPositions 的对比

| 特性 | StaticPositions | MarketNeutralPositions |
|------|-----------------|------------------------|
| **用途** | 静态/动态持仓 | 市场中性套利 |
| **分组** | 不需要 | 按 group_id 分组 |
| **Ratio 平衡** | 不需要 | 自动平衡（总和为 0） |
| **Direction 计算** | 不需要 | 自动计算 |
| **配置复杂度** | 简单 | 复杂 |
| **适用场景** | 单边持仓、对冲 | 跨平台套利 |

---

## 14. FAQ

### Q1: 旧格式和新格式可以混用吗？

**A**: 不可以。必须选择其中一种格式。

- 旧格式：使用 `positions_usd` + `exchange_path`
- 新格式：使用 `targets` + `requires` + `links`

### Q2: 如何在多个交易所保持相同的仓位？

**A**: 使用 `exchange_id: "*"` 通配符：

```yaml
targets:
  - exchange_id: "*"
    symbol: BTC/USDT:USDT
    position_usd: 1000
```

### Q3: 如何根据账户权益动态调整仓位？

**A**: 使用 `equation` 数据源：

```yaml
requires:
  - equation

targets:
  - exchange_id: "*"
    symbol: BTC/USDT:USDT
    position_usd: "equation_usd * 0.6"
```

### Q4: tolerance 如何计算？

**A**: 
```python
current_position_usd = 当前仓位（USD）
target_position_usd = 目标仓位（USD）
deviation = abs(current_position_usd - target_position_usd) / abs(target_position_usd)

if deviation > tolerance:
    # 需要调整仓位
```

### Q5: exit_on_target 何时触发？

**A**: 当所有交易对的仓位都达到目标（在 tolerance 范围内）时触发。

---

## 15. 最佳实践

### 15.1 使用新格式

推荐使用新格式（targets + requires + links），因为：
- 支持表达式
- 支持多交易所
- 支持自定义字段
- 更灵活

### 15.2 合理设置 tolerance

- 太小：频繁调仓，交易成本高
- 太大：仓位偏差大，风险控制差

**推荐值**：
- 高频策略：0.02 - 0.05
- 低频策略：0.05 - 0.1

### 15.3 使用条件变量

对于基于指标的策略，使用条件变量避免频繁更新：

```yaml
vars:
  - name: direction
    value: 1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else 0)
    on: rsi[-1] < 30 or rsi[-1] > 70
    initial_value: 0
```

### 15.4 使用展开式写法

对于批量配置相同参数的交易对，使用展开式写法：

```yaml
target_pairs:
  - BTC/USDT:USDT
  - ETH/USDT:USDT
  - SOL/USDT:USDT

target:
  exchange_class: okx
  position_usd: "1000"
  speed: 0.5
```
