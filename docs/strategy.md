# Strategy 模块文档

## 概述

Strategy（策略）负责计算目标仓位，不负责执行。执行由 Executor 统一处理。

### 核心职责

- 计算目标仓位（position_usd, position_amount 等）
- 定义执行紧急度（speed）
- 监控仓位是否达标

### 数据驱动设计（Feature 0008 & 0012）

Strategy 支持数据驱动能力：

1. **requires 依赖声明**：声明依赖的 Indicator，自动收集变量
2. **Scope 系统**：多层级变量定义和继承（见 [Scope 文档](scope.md)）
3. **通用字典输出**：可输出任意字段，不限于 position_usd
4. **targets 表达式**：目标字段支持表达式求值
5. **多 Exchange 匹配**：支持匹配多个交易所

```
┌─────────────────────────────────────────────────────────────┐
│                    Strategy 数据流                           │
├─────────────────────────────────────────────────────────────┤
│  IndicatorGroup                                              │
│  ├── equation (MedalEquationDataSource)                     │
│  ├── ticker (TickerDataSource)                              │
│  └── rsi (RSIIndicator)                                     │
│           │                                                  │
│           ▼ requires: [equation, rsi]                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Context Variables                                   │    │
│  │  {equation_usd, mid_price, rsi, ...}                │    │
│  └─────────────────────────────────────────────────────┘    │
│           │                                                  │
│           ▼ Scope vars 计算（多层级）                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Scope System                                        │    │
│  │  - GlobalScope vars                                  │    │
│  │  - ExchangeScope vars                                │    │
│  │  - TradingPairScope vars                             │    │
│  └─────────────────────────────────────────────────────┘    │
│           │                                                  │
│           ▼ targets 表达式求值                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  StrategyOutput                                      │    │
│  │  {(exchange_id, symbol): {"position_usd": ..., ...}}│   │
│  └─────────────────────────────────────────────────────┘    │
│           │                                                  │
│           ▼ 聚合到 Executor                                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  strategies namespace                                │    │
│  │  strategies["position_usd"] = [1000]                │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## 类层次

```
BaseStrategy (抽象基类)
├── StaticPositionsStrategy       # 静态目标仓位策略
├── MarketNeutralPositionsStrategy # 市场中性对冲策略（Feature 0013）
└── (其他自定义策略)
```

---

## 输出格式

### 旧格式（向后兼容）

```python
TargetPositions = dict[tuple[str, str], tuple[float, float]]
# {(exchange_id, symbol): (position_usd, speed)}
```

示例：
```python
return {
    ("okx/main", "BTC/USDT:USDT"): (5000.0, 0.5),
    ("okx/main", "ETH/USDT:USDT"): (-2000.0, 0.8),
}
```

### 新格式（Feature 0008 推荐）

```python
StrategyOutput = dict[tuple[str, str], dict[str, Any]]
# {(exchange_id, symbol): {"position_usd": ..., "speed": ..., "任意字段": ...}}
```

示例：
```python
return {
    ("okx/main", "BTC/USDT:USDT"): {
        "position_usd": 5000.0,
        "position_amount": 0.1,
        "max_position_usd": 10000.0,
        "speed": 0.5,
    },
}
```

新格式的所有字段都会传递给 Executor，聚合到 `strategies` namespace。

---

## 配置格式

### 基础配置（BaseStrategyConfig）

```yaml
class_name: static_positions
name: my_strategy
interval: 1.0  # 主循环间隔（秒）
debug: false   # 调试模式（不下真单）

# 交易对过滤
trading_pairs:
  - '*'              # 所有交易对
  - 'BTC/USDT:USDT'  # 指定交易对
  - '!ETH/USDT'      # 排除某交易对
max_trading_pairs: 12

# 数据驱动（Feature 0008 & 0012）
requires:
  - equation
  - rsi

# Feature 0011: 全局 condition（可选）
# - 默认为 null（等价 True）
# - 在每个 (exchange_id, symbol) 上求值；若为 False 或求值异常：忽略该 scope（等价于为每个 target 追加 AND 条件）
condition: null

# Scope 系统（Feature 0012）
# 注意：Scope 节点只允许在 conf/app/*.yaml 的 scopes 字段里声明；
# Strategy 配置里只引用 links，不允许出现 scopes 字段。
links:
  - id: link_main
    value: [g, exchange_class, exchange, trading_pair]
```

**过滤字段说明**：
- `trading_pairs/max_trading_pairs`：旧字段（不使用 Scope 系统时常用）。
- Scope 系统下（配置了 `links`）建议使用 `include_symbols/exclude_symbols`（见 [scope.md](scope.md)）；exchange 选择由 AppConfig 的 `exchanges` 选择器控制。

**注意**：Scope 的配置边界、实例发现与 ChainMap 继承规则详见 [Scope 文档](scope.md)。Strategy 顶级 `vars` 仍用于“策略本地变量”（与 Scope vars 不同）。

**vars 格式**：`vars` 支持标准格式 / dict 简化格式 / list[str] 简化格式，详见 [vars.md](vars.md)。

### StaticPositionsStrategy 配置

支持三种配置方式：

#### 旧格式（向后兼容）

```yaml
class_name: static_positions
exchange_path: okx/main
exit_on_target: true
tolerance: 0.05
speed: 0.8
positions_usd:
  BTC/USDT:USDT: 1000
  ETH/USDT:USDT: -500
```

#### 新格式（Feature 0008）

```yaml
class_name: static_positions
requires:
  - equation

vars:
  - name: target_ratio
    value: '0.6'

targets:
  - exchange_id: '*'        # 匹配所有 exchange（注：兼容字段 exchange 等价 exchange_id）
    exchange_class: okx     # 但只匹配 okx 类型
    symbol: BTC/USDT:USDT
    position_usd: 'target_ratio * equation_usd'
    max_position_usd: '0.8 * equation_usd'
    speed: 0.5

  - exchange_id: okx/spot_a  # 精确匹配 exchange path
    symbol: USDG/USDT
    position_amount: 'base_amount + delta'
    custom_field: 'some_expression'  # 任意自定义字段

exit_on_target: false
tolerance: 0.05
```

#### 展开式写法（Feature 0011 推荐）

```yaml
class_name: static_positions

target_pairs:
  - BTC/USDT:USDT
  - ETH/USDT:USDT

target:
  exchange_class: okx
  position_usd: "1000"
  speed: 0.5
```

说明：`target_pairs + target` 会在配置加载时展开为 `targets` 列表（减少重复配置）。

---

## targets 匹配规则

### exchange_id 匹配

- `'*'`：匹配所有 exchange
- `'okx/main'`：精确匹配 exchange path
- `'okx/*'`：模式匹配（支持通配符语法，使用 younotyou 包）

兼容字段：`exchange` 等价于 `exchange_id`（不推荐继续使用）。

### exchange_class 匹配

- `'*'`：匹配所有 exchange class
- `'okx'`：精确匹配 exchange class_name
- `'ok*'`：模式匹配

### 匹配示例

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
```

---

## strategies namespace（单策略标量化）

Executor 会接收到一个 `strategies` namespace，用于在表达式里统一处理"策略输出字段"：

- 当前 App 仅支持单策略，因此每个字段是标量值（Issue 0013）
- 直接访问：`strategies["field"]` 即可，无需 sum/avg 聚合

```python
# Strategy 输出（单策略）
{("okx/main", "BTC/USDT"): {"position_amount": 0.01}}

# Executor 接收到的 strategies namespace（Issue 0013: 单策略标量化）
strategies["position_amount"] = 0.01
```

在 Executor 中使用：

```yaml
# conf/executor/xxx.yaml
vars:
  - name: position_amount
    value: strategies["position_amount"]
  - name: position_usd
    value: strategies["position_usd"] if "position_usd" in strategies else 0
```

---

## 退出机制

当策略的 `on_tick()` 返回 `True` 时，触发退出流程：

1. Strategy.on_tick() 返回 True → 唯一策略完成
2. StrategyGroup.is_finished 变为 True → StrategyGroup.on_tick() 返回 True
3. AppCore.on_tick() 检测到策略完成 → 返回 True → 程序正常退出

---

## 示例

### 简单的保持仓位策略

```yaml
# conf/strategy/simple_hold.yaml
class_name: static_positions
exchange_path: okx/main
positions_usd:
  BTC/USDT:USDT: 1000
exit_on_target: true
tolerance: 0.05
speed: 0.8
```

### 数据驱动的动态仓位策略

```yaml
# conf/app/<app>.yaml（片段：scopes 字段只允许出现在 app 配置里）
scopes:
  g:
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
# conf/strategy/dynamic_hold.yaml
class_name: static_positions

requires:
  - equation  # 账户权益数据源
  - rsi       # RSI 指标

links:
  - id: link_main
    value: [g, exchange_class, exchange, trading_pair]

targets:
  - exchange_id: '*'
    exchange_class: okx
    symbol: BTC/USDT:USDT
    position_usd: 'risk_ratio * equation_usd * direction'
    speed: 0.5

exit_on_target: false
```

### 多交易所对冲策略

```yaml
# conf/strategy/hedge.yaml
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

---

## MarketNeutralPositionsStrategy（市场中性对冲策略）

### 概述

MarketNeutralPositionsStrategy 是一个市场中性对冲策略，通过在不同交易所/交易对之间建立对冲仓位来捕获价差套利机会。

**核心特性**：
- **市场中性**：确保组内 `ratio` 总和为 0
- **自动分组**：按 `group_id` 分组交易对
- **公平价格**：通过 `FairPriceIndicator` 计算标准价格
- **智能方向**：自动计算开仓/平仓/持仓方向
- **Ratio 平衡**：自动调整仓位比例，满足对冲条件

### 配置示例

```yaml
# conf/strategy/market_neutral_positions/eth_arbitrage.yaml
class_name: market_neutral_positions

# 交易对过滤
include_symbols: ['ETH/USDT', 'WBETH/USDT', 'BTC/USDT']
exclude_symbols: []

# 依赖的 Indicator
requires:
  - medal_amount  # 账户余额
  - ticker        # 行情数据
  - fair_price    # 公平价格

# Scope 链路
links:
  - id: main
    value: [g, exchange_class, exchange, trading_pair_class_group, trading_pair_class, trading_pair]

# 分组配置
default_trading_pair_group: symbol.split('/')[0]
trading_pair_group:
  WBETH/USDT: ETH
  STETH/USDT: ETH

# 阈值配置
max_trading_pair_groups: 10
max_position_usd: 2000.0
entry_price_threshold: 0.001
exit_price_threshold: 0.0005
score_threshold: 0.001

# 目标配置
targets:
  - exchange_id: "*"
    symbol: "*"
    condition: "ratio != 0"
    vars:
      - position_usd=ratio * max_position_usd
```

### 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_trading_pair_groups` | int | 10 | 最大交易对分组数量 |
| `max_position_usd` | float | 2000.0 | 每个分组的最大仓位（USD） |
| `entry_price_threshold` | float | 0.001 | 开仓价差阈值（0.1%） |
| `exit_price_threshold` | float | 0.0005 | 平仓价差阈值（0.05%） |
| `score_threshold` | float | 0.001 | 最小 score 阈值 |
| `default_trading_pair_group` | str | `symbol.split('/')[0]` | 默认分组表达式 |
| `trading_pair_group` | dict | {} | 自定义分组映射 |

### 工作原理

#### 1. Trading Pair 分组

策略将交易对按 `group_id` 分组（如 ETH/USDT、WBETH/USDT → ETH 组）。

#### 2. Fair Price 计算

通过 `FairPriceIndicator` 获取每个交易对的公平价格（mid_price）。

#### 3. Direction 计算

根据价差计算每个交易对的方向：
- `-1`: Entry Short（建议开空仓）
- `0`: Exit（建议平仓）
- `1`: Entry Long（建议开多仓）
- `null`: Hold（建议持仓不动）

#### 4. Ratio 计算与平衡

策略自动调整仓位比例，确保：
- 组内所有 `ratio` 总和为 0（市场中性）
- `ratio(Price_min) - ratio(Price_max) = 2`（对冲条件）

### 套利场景

**场景 1：跨平台现货套利**
```
低价平台买入现货 → 链上转账 → 高价平台卖出现货
同时：高价平台买入等值空合约（对冲）
```

**场景 2：资费率套利**
```
资费率为正：做空合约 + 买入现货（收取资费）
资费率为负：做多合约 + 卖出现货（收取资费）
```

**场景 3：合约间套利**
```
不同交易所的合约价差套利
```

### 示例输出

```python
# 策略输出
{
    ("okx/main", "WBETH/USDT"): {
        "position_usd": 2000.0,   # 做多 WBETH（最低价）
        "ratio": 1.0,
    },
    ("okx/main", "ETH/USDT"): {
        "position_usd": 0.0,      # 不持仓（中间价）
        "ratio": 0.0,
    },
    ("binance/spot", "ETH/USDT"): {
        "position_usd": -2000.0,  # 做空 ETH（最高价）
        "ratio": -1.0,
    },
}
```

### 相关文档

- [Feature 0013: MarketNeutralPositions 策略](../features/0013-market-neutral-positions-strategy.md)
- [Example 004: MarketNeutralPositions 配置详解](../examples/004-market-neutral-positions-strategy.md)
- [Scope 系统文档](scope.md)
