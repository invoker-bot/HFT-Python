# Feature 0008: Strategy 数据驱动增强

## 概述

增强 Strategy 的数据驱动能力，使其支持：
1. `requires` 依赖声明（类似 Executor）
2. `vars` 变量系统 变量计算
3. **通用字典输出**（重大变更）
4. Indicator 层级体系
5. 多 Exchange 目标匹配

## 重大变更：Strategy 返回通用字典

### 旧设计

```python
# Strategy 返回固定格式
TargetPositions = dict[tuple[str, str], tuple[float, float]]  # (position_usd, speed)
```

### 新设计

```python
# Strategy 返回通用字典，可包含任意字段
StrategyOutput = dict[tuple[str, str], dict[str, Any]]
# {(exchange_path, symbol): {"position_usd": ..., "speed": ..., "任意字段": ...}}
```

### Executor 接收聚合后的列表

多个 Strategy 的输出聚合到 `strategies` namespace：

```python
# 假设有两个 Strategy 都输出了 position_amount
# Strategy A: {("okx/main", "BTC/USDT"): {"position_amount": 0.01}}
# Strategy B: {("okx/main", "BTC/USDT"): {"position_amount": 0.02}}

# Executor 收到：
strategies["position_amount"] = [0.01, 0.02]  # 列表形式
```

### Executor 中聚合 Strategy 输出

```yaml
# conf/executor/xxx.yaml
vars:
  - name: position_amount
    value: sum(strategies["position_amount"])
  - name: position_usd
    value: sum(strategies["position_usd"]) if "position_usd" in strategies else null
```

## 当前实现问题

### KeepPositionsStrategy 配置

**当前**：
```yaml
class_name: keep_positions
exchange_path: okx/main  # 只能指定单个 exchange
positions_usd:
  BTC/USDT:USDT: 1000    # 静态数值
```

**目标**：
```yaml
class_name: keep_positions
requires:
  - equation
  - rsi

# vars 定义在 scopes 中（Feature 0012）
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
      - name: base_amount
        value: current_position_amount
        on: rsi[-1] < 30 or rsi[-1] > 70
        initial_value: 0

targets:
  - exchange: okx/a
    exchange_class: okx
    symbol: USDG/USDT
    position_usd: '0.6 * equation_usd'
    position_amount: 'base_amount + target_delta'  # 可同时输出多个字段
    max_position_usd: '0.8 * equation_usd'
    speed: 0.1
```

## 设计

### 1. Strategy requires 机制

Strategy 也支持 `requires` 声明，用于获取 Indicator 变量：

```python
class BaseStrategy(Listener):
    def __init__(self, config: BaseStrategyConfig):
        self._requires: list[str] = config.requires or []

    def collect_context_vars(self, exchange_path: str, symbol: str) -> dict:
        """收集 requires 中 Indicator 的变量"""
        vars = {}
        for indicator_id in self._requires:
            indicator = self._get_indicator(indicator_id, exchange_path, symbol)
            if indicator and indicator.is_ready():
                vars.update(indicator.calculate_vars(direction=0))
        return vars
```

### 2. Indicator 层级体系

不同 Indicator 有不同的作用域：

| 层级 | 说明 | 示例 |
|------|------|------|
| Global | 全局唯一 | GlobalFundingRateIndicator |
| ExchangeClass | 按交易所类型 | - |
| ExchangePath | 按交易所实例 | MedalEquationDataSource |
| Pair | 按交易对 | TickerDataSource, RSIIndicator |

```python
class IndicatorGroup:
    def query_indicator(
        self,
        indicator_id: str,
        exchange_class: str | None = None,
        symbol: str | None = None,
        exchange_path: str | None = None,  # 新增
    ) -> BaseIndicator | None:
        """
        按层级查询 Indicator

        查询顺序：
        1. Pair 级别: (exchange_class, symbol)
        2. ExchangePath 级别: (exchange_path,)
        3. ExchangeClass 级别: (exchange_class,)
        4. Global 级别: ()
        """
```

### 3. MedalEquationDataSource

账户权益数据源，ExchangePath 级别：

```python
class MedalEquationDataSource(BaseIndicator[float]):
    """
    账户权益数据源

    提供变量：
    - equation_usd: 账户总权益（USD）
    - available_usd: 可用余额（USD）
    """

    def __init__(self, exchange_path: str, **kwargs):
        self._exchange_path = exchange_path

    async def on_tick(self) -> bool:
        exchange = self._get_exchange()
        balance = await exchange.medal_fetch_total_balance_usd()
        self._data.append(balance, timestamp=time.time())
        return False

    def calculate_vars(self, direction: int) -> dict:
        return {
            "equation_usd": self._data.latest,
        }
```

### 4. Strategy vars / conditional_vars

Strategy 也支持 `vars` 和 `conditional_vars`，与 Executor 机制相同：

```yaml
vars:
  - name: current_amount
    value: current_position_amount
  - name: price_ratio
    value: mid_price / center_price
  - name: center_price
    value: mid_price
    on: rsi[-1] < 30 or rsi[-1] > 70
    initial_value: mid_price
```

**计算顺序**：
1. 收集 requires 中 Indicator 的变量
2. 计算 vars（按列表顺序，包括条件变量）
3. 求值 targets 中的表达式

### 5. targets 通用字段输出

targets 中的字段不再限于 `position_usd` 和 `speed`，可输出任意字段：

```yaml
targets:
  - symbol: USDG/USDT
    position_usd: '0.6 * equation_usd'
    position_amount: 'base_amount + delta'  # 任意字段
    max_position_usd: '0.8 * equation_usd'
    speed: 0.1
    custom_field: 'some_expression'         # 自定义字段
```

所有字段都会传递给 Executor，聚合到 `strategies` namespace。

### 6. Executor 聚合 Strategy 输出

Executor 通过 `strategies` namespace 接收聚合后的 Strategy 输出：

```yaml
# conf/executor/xxx.yaml
vars:
  - name: position_amount
    value: sum(strategies["position_amount"])
  - name: position_usd
    value: sum(strategies["position_usd"]) if "position_usd" in strategies else null
```

> **注意**：原有的 `current_position_usd`、`position_usd` 等直接注入变量仍然保留，
> 作为 `strategies` 聚合的快捷方式。

### 7. 多 Exchange 目标匹配

```yaml
targets:
  - exchange: okx/a        # 精确匹配 path
    symbol: USDG/USDT
    position_usd: 1000

  - exchange: '*'          # 匹配所有 exchange
    exchange_class: okx    # 但只匹配 okx 类型
    symbol: BTC/USDT:USDT
    position_usd: 500
```

匹配规则：
- `exchange`: 匹配 exchange path，`*` 表示所有
- `exchange_class`: 匹配 exchange class_name，`*` 表示所有

## 任务列表

### Phase 1: Indicator 层级（P0）

- [x] IndicatorGroup 支持 exchange_path 级别查询（已通过）
- [x] 新增 MedalEquationDataSource（已通过）
- [x] IndicatorFactory 注册 MedalEquationDataSource（已通过）

### Phase 2: Strategy 通用字典输出（P0）

- [x] 修改 StrategyOutput 类型为通用字典（已通过）
- [x] 实现多 Strategy 输出聚合到 strategies namespace（已通过）
- [x] Executor 支持 strategies["field"] 访问（已通过）

### Phase 3: Strategy vars / conditional_vars（P1）

- [x] BaseStrategyConfig 添加 requires 字段（已通过）
- [x] BaseStrategyConfig 添加 vars / conditional_vars 字段（已通过）
- [x] BaseStrategy 添加 collect_context_vars() 方法（已通过）
- [x] 实现 vars 列表语义计算（已通过）
- [x] 实现 conditional_vars 条件触发更新（已通过）

### Phase 4: targets 通用字段（P1）

- [x] KeepPositionsStrategyConfig.targets 支持任意字段（已通过）
- [x] targets 字段表达式求值（已通过）
- [x] 多 Exchange 目标匹配逻辑（已通过）

### Phase 5: 文档和测试（P2）

- [x] 更新 docs/strategy.md（已通过）
- [x] 添加单元测试（已通过）

### Phase 6: vars 简化格式支持（P2）

- [ ] BaseStrategyConfig 支持 vars 的三种格式（待审核）
- [ ] BaseExecutorConfig 支持 vars 的三种格式（待审核）
- [ ] ScopeConfig 支持 vars 的三种格式（待审核）
- [ ] 更新 docs/strategy.md 说明 vars 格式（待审核）
- [ ] 更新 docs/executor.md 说明 vars 格式（待审核）
- [ ] 更新 docs/scope.md 说明 vars 格式（待审核）
- [ ] 添加 vars 简化格式的单元测试（待实现）

## 与现有 Feature 的关系

| Feature | 关系 |
|---------|------|
| Feature 0005 | 复用 Executor 的 requires 机制 |
| Feature 0006 | 扩展 Indicator 层级体系 |
| Feature 0007 | GlobalIndicator 已实现 |
| Feature 0010 | 共享 vars / conditional_vars 机制 |

## 示例配置

### App 配置

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
      window: 60
    ready_condition: "timeout < 5"
  equation:
    class: MedalEquationDataSource
    params:
      window: null
    ready_condition: "timeout < 15"
```

### Strategy 配置

```yaml
# conf/strategy/stablecoin/grid_positions.yaml
class_name: keep_positions
requires:
  - equation

targets:
  - exchange: '*'
    exchange_class: okx
    symbol: USDG/USDT
    position_usd: '0.6 * equation_usd'
    speed: 0.1
```

## 备注

### 不同平台的 equation_usd 计算

| 平台 | 计算方式 |
|------|----------|
| OKX | 现货和合约一体，直接返回 totalEquity |
| Binance | 现货 + 合约账户价值之和 |

这个差异由 `BaseExchange.medal_fetch_total_balance_usd()` 内部处理。
