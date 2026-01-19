# App 配置规范

## 概述

App 配置是应用的入口配置文件，定义了运行时需要的所有组件。

## 配置根目录：`HFT_ROOT_PATH`

App/Exchange/Strategy/Executor 的配置文件统一从 `HFT_ROOT_PATH`（默认 `.`）下加载：

- `conf/app/`
- `conf/exchange/`
- `conf/strategy/`
- `conf/executor/`

详见 [config-path.md](config-path.md)。

## 配置结构

```yaml
# conf/app/<app_name>.yaml
class_name: app

# 运行参数
interval: 1.0
health_check_interval: 60.0
log_interval: 120.0

# 组件引用（配置路径类型字段，见 config-path.md）
exchanges:               # ExchangeConfigPathGroup（list[str] 选择器）
  - <selector>
strategy: <strategy_id>  # StrategyConfigPath（单条）
executor: <executor_id>  # ExecutorConfigPath（单条）

# 内联定义（运行时创建）
indicators:
  <indicator_id>:
    class: <IndicatorClassName>
    params:
      <param>: <value>
    ready_condition: "<expression>"
```

## 配置路径 vs 内联定义

### 配置路径（引用模式）

**适用于**：exchanges, strategy, executor

这些组件有独立的配置文件，App 配置只需引用路径：

```yaml
exchanges:
  - okx/main        # → $HFT_ROOT_PATH/conf/exchange/okx/main.yaml
  - binance/spot    # → $HFT_ROOT_PATH/conf/exchange/binance/spot.yaml

strategy: keep_positions/btc   # → $HFT_ROOT_PATH/conf/strategy/keep_positions/btc.yaml

executor: smart/default         # → $HFT_ROOT_PATH/conf/executor/smart/default.yaml
```

**原因**：
1. 配置复杂，需要独立文件管理
2. 可复用，多个 App 可引用同一配置
3. 有明确的类型和参数结构

### exchanges 选择器（selector）

`exchanges` 字段是 `ExchangeConfigPathGroup`（输入为 `list[str]`），支持选择器语义：

```yaml
exchanges:
  - "*"           # 默认包含全部 exchange 配置
  - "!okx/test"   # 排除
  - "okx/main"    # 也可显式包含单个
  - "binance/*"   # 支持通配
```

若只写排除规则（全部以 `!` 开头），语义等价于“先 `*` 再排除”。

selector 的严格语义与缓存建议见 [config-path.md](config-path.md)。

### 内联定义（声明模式）

**适用于**：indicators

Indicator 在 App 配置中内联定义：

```yaml
indicators:
  ticker:
    class: TickerDataSource
    params:
      window: 60.0
    ready_condition: "timeout < 5"

  trades:
    class: TradesDataSource
    params:
      window: 300.0
    ready_condition: "timeout < 60 and cv < 0.8"

  rsi:
    class: RSIIndicator
    params:
      ohlcv: ohlcv
      period: 14
```

**原因**：
1. 配置简单，通常只有几个参数
2. 与交易对绑定，由 IndicatorGroup 动态创建
3. 不同 App 的 Indicator 配置通常不同

## 完整示例

```yaml
# conf/app/stablecoin/grid.yaml
class_name: app

interval: 1.0
health_check_interval: 60.0
log_interval: 120.0

# 引用配置路径
exchanges:
  - okx/spot_main
  - okx/spot_backup

strategy: stablecoin/grid_positions

executor: stablecoin/grid_executor

# 内联定义 Indicator
indicators:
  ticker:
    class: TickerDataSource
    params:
      window: 60.0
    ready_condition: "timeout < 5"

  order_book:
    class: OrderBookDataSource
    params:
      window: 60.0
      depth: 20
    ready_condition: "timeout < 10"
```

## ready_condition 表达式

`ready_condition` 用于判断 Indicator 数据是否就绪，支持以下变量：

| 变量 | 类型 | 说明 |
|------|------|------|
| `timeout` | float | 距离最后一次数据更新的秒数 |
| `cv` | float | 采样间隔变异系数（0-1，越小越稳定） |
| `range` | float | 实际覆盖时间 / 期望窗口时间 |

**示例**：
```yaml
ready_condition: "timeout < 60 and cv < 0.8 and range > 0.6"
```

## 相关文档

- [architecture.md](architecture.md) - 整体架构
- [config-path.md](config-path.md) - ConfigPath 类型、ExchangeConfigPathGroup 选择器与缓存
- [indicator.md](indicator.md) - Indicator 模块
- [executor.md](executor.md) - Executor 配置

## 兼容性与迁移提示

`strategies:`（列表）已被 `strategy:`（单条）取代；如需从旧配置迁移：

- 将 `strategies: [a/b]` 改为 `strategy: a/b`
- 若旧配置包含多条策略，需先合并/收敛为单条策略入口（保持 Executor 的 `strategies` namespace 仍使用列表聚合语义）
