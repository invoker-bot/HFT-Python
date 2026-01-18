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
│  │  {(exchange_path, symbol): {"position_usd": ..., ...}}│   │
│  └─────────────────────────────────────────────────────┘    │
│           │                                                  │
│           ▼ 聚合到 Executor                                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  strategies namespace                                │    │
│  │  strategies["position_usd"] = [1000, 2000]          │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## 类层次

```
BaseStrategy (抽象基类)
├── KeepPositionsStrategy  # 保持目标仓位
└── (其他自定义策略)
```

---

## 输出格式

### 旧格式（向后兼容）

```python
TargetPositions = dict[tuple[str, str], tuple[float, float]]
# {(exchange_path, symbol): (position_usd, speed)}
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
# {(exchange_path, symbol): {"position_usd": ..., "speed": ..., "任意字段": ...}}
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
class_name: keep_positions
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

# Scope 系统（Feature 0012）
scopes:
  global:
    class_name: GlobalScope
    vars:
      - max_position_ratio=0.8

  trading_pair:
    class_name: TradingPairScope
    vars:
      - name: center_price
        value: mid_price
        on: rsi[-1] < 30 or rsi[-1] > 70
        initial_value: mid_price
```

**注意**：Strategy 中的 vars 定义在 `scopes` 配置中，不支持顶级 `vars` 字段。详见 [vars 文档](vars.md) 和 [Scope 文档](scope.md)。

### KeepPositionsStrategy 配置

支持两种配置方式：

#### 旧格式（向后兼容）

```yaml
class_name: keep_positions
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
class_name: keep_positions
requires:
  - equation

vars:
  - name: target_ratio
    value: '0.6'

targets:
  - exchange: '*'           # 匹配所有 exchange
    exchange_class: okx     # 但只匹配 okx 类型
    symbol: BTC/USDT:USDT
    position_usd: 'target_ratio * equation_usd'
    max_position_usd: '0.8 * equation_usd'
    speed: 0.5

  - exchange: okx/spot_a    # 精确匹配 exchange path
    symbol: USDG/USDT
    position_amount: 'base_amount + delta'
    custom_field: 'some_expression'  # 任意自定义字段

exit_on_target: false
tolerance: 0.05
```

---

## targets 匹配规则

### exchange 匹配

- `'*'`：匹配所有 exchange
- `'okx/main'`：精确匹配 exchange path
- `'okx/*'`：模式匹配（支持 fnmatch 语法）

### exchange_class 匹配

- `'*'`：匹配所有 exchange class
- `'okx'`：精确匹配 exchange class_name
- `'ok*'`：模式匹配

### 匹配示例

```yaml
targets:
  # 匹配所有 okx 交易所的 BTC 交易对
  - exchange: '*'
    exchange_class: okx
    symbol: BTC/USDT:USDT
    position_usd: 1000

  # 只匹配特定的 exchange
  - exchange: okx/spot_a
    exchange_class: '*'
    symbol: USDG/USDT
    position_usd: 500
```

---

## 多策略聚合

多个 Strategy 的输出会聚合到 Executor 的 `strategies` namespace：

```python
# Strategy A 输出
{("okx/main", "BTC/USDT"): {"position_amount": 0.01}}

# Strategy B 输出
{("okx/main", "BTC/USDT"): {"position_amount": 0.02}}

# Executor 收到的 strategies namespace
strategies["position_amount"] = [0.01, 0.02]  # 列表形式
```

在 Executor 中使用：

```yaml
# conf/executor/xxx.yaml
vars:
  - name: position_amount
    value: sum(strategies["position_amount"])
  - name: position_usd
    value: sum(strategies["position_usd"]) if "position_usd" in strategies else 0
```

---

## 退出机制

当策略的 `on_tick()` 返回 `True` 时，触发退出流程：

1. Strategy.on_tick() 返回 True → 策略从 StrategyGroup 中移除
2. StrategyGroup.is_finished 变为 True → StrategyGroup.on_tick() 返回 True
3. AppCore.on_tick() 检测到策略组完成 → 返回 True → 程序正常退出

---

## 示例

### 简单的保持仓位策略

```yaml
# conf/strategy/simple_hold.yaml
class_name: keep_positions
exchange_path: okx/main
positions_usd:
  BTC/USDT:USDT: 1000
exit_on_target: true
tolerance: 0.05
speed: 0.8
```

### 数据驱动的动态仓位策略

```yaml
# conf/strategy/dynamic_hold.yaml
class_name: keep_positions

requires:
  - equation  # 账户权益数据源
  - rsi       # RSI 指标

scopes:
  global:
    class_name: GlobalScope
    vars:
      - risk_ratio=0.6

  trading_pair:
    class_name: TradingPairScope
    vars:
      - name: direction
        value: 1 if rsi[-1] < 30 else (-1 if rsi[-1] > 70 else 0)
        on: rsi[-1] < 30 or rsi[-1] > 70
        initial_value: 0

targets:
  - exchange: '*'
    exchange_class: okx
    symbol: BTC/USDT:USDT
    position_usd: 'risk_ratio * equation_usd * direction'
    speed: 0.5

exit_on_target: false
```

### 多交易所对冲策略

```yaml
# conf/strategy/hedge.yaml
class_name: keep_positions

requires:
  - equation

targets:
  # OKX 做多
  - exchange: okx/main
    symbol: BTC/USDT:USDT
    position_usd: '0.3 * equation_usd'
    speed: 0.3

  # Binance 做空
  - exchange: binance/main
    symbol: BTC/USDT:USDT
    position_usd: '-0.3 * equation_usd'
    speed: 0.3

exit_on_target: false
```
