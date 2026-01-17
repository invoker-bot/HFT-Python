# App 配置规范

## 概述

App 配置是应用的入口配置文件，定义了运行时需要的所有组件。

## 配置结构

```yaml
# conf/app/<app_name>.yaml
class_name: app

# 运行参数
interval: 1.0
health_check_interval: 60.0
log_interval: 120.0

# 组件引用（配置路径）
exchanges:
  - <exchange_config_path>
strategies:
  - <strategy_config_path>
executor: <executor_config_path>

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

**适用于**：exchanges, strategies, executor

这些组件有独立的配置文件，App 配置只需引用路径：

```yaml
exchanges:
  - okx/main        # → conf/exchange/okx/main.yaml
  - binance/spot    # → conf/exchange/binance/spot.yaml

strategies:
  - keep_positions/btc   # → conf/strategy/keep_positions/btc.yaml

executor: smart/default  # → conf/executor/smart/default.yaml
```

**原因**：
1. 配置复杂，需要独立文件管理
2. 可复用，多个 App 可引用同一配置
3. 有明确的类型和参数结构

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

strategies:
  - stablecoin/grid_positions

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
- [indicator.md](indicator.md) - Indicator 模块
- [executor.md](executor.md) - Executor 配置
