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

# Scope 系统（仅 AppConfig 支持；Strategy/Executor 配置里不允许出现 scopes 字段）
scopes:
  <scope_class_id>:       # 用户命名（例如 g / exchange / trading_pair 等）
    class: <ScopeClass>   # 例如 GlobalScope / ExchangeScope / TradingPairScope
    vars:                 # scope 创建时初值（以及可选的持久状态初值）
      ...

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

strategy: static_positions/main   # → $HFT_ROOT_PATH/conf/strategy/static_positions/main.yaml

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
      window: 1m  # 支持 duration 字符串：60s, 1m, 5m, 1h, 1d, 500ms
      debug: false  # 可选：开启调试模式，记录每次 calculate_vars 的结果
      debug_log_interval: 60s  # 可选：debug 日志输出间隔（支持 duration 字符串）
    ready_condition: "timeout < 5"

  trades:
    class: TradesDataSource
    params:
      window: 5m  # 等价于 300.0 或 "300s"
      debug: true  # 开启调试模式
      debug_log_interval: 30s  # 每 30 秒输出一次 debug 日志
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
# conf/app/app.yaml（简化示例）
class_name: app

health_check_interval: 60.0
log_interval: 30.0

# 引用配置路径
exchanges:
  - "demo/*"  # selector: 匹配所有 demo 下的 exchange
strategy: static_positions/main
executor: market/default

debug: true
```

## ready_condition 表达式

`ready_condition` 用于判断 Indicator 数据是否就绪，支持以下变量：

| 变量 | 类型 | 说明 |
|------|------|------|
| `timeout` | float | 距离最后一次数据更新的秒数 |
| `cv` | float | 采样间隔变异系数（0-1，越小越稳定） |
| `range` | float | 实际覆盖时间 / 期望窗口时间 |

**限制**：
- `ready_condition` 禁用函数调用（如 `len/sum/min/max`），仅支持比较/逻辑/基本算术等操作符
- `window <= 0` 或 `window: null` 时：`cv = 0.0`，`range = 1.0`

**示例**：
```yaml
ready_condition: "timeout < 60 and cv < 0.8 and range > 0.6"
```

## window 参数格式（Issue 0015）

`window` 参数用于指定数据窗口大小，支持以下格式：

| 格式 | 说明 | 示例 | 等价秒数 |
|------|------|------|----------|
| `int/float` | 数值（单位秒） | `60`, `300.0` | 60, 300 |
| `str` | duration 字符串 | `"60s"`, `"1m"`, `"5m"` | 60, 60, 300 |
| `null` | 无窗口（仅保留最新点） | `null` | 0 |

**支持的 duration 单位**：
- `ms`: 毫秒（0.001秒）
- `s`: 秒
- `m`: 分钟（60秒）
- `h`: 小时（3600秒）
- `d`: 天（86400秒）

**推荐写法**：优先使用 duration 字符串（更直观、更不易出错）

```yaml
# 推荐：duration 字符串
window: 1m    # 1分钟
window: 5m    # 5分钟
window: 1h    # 1小时

# 也支持：数值秒
window: 60.0  # 1分钟
window: 300   # 5分钟

# 无窗口
window: null  # 等价于 0
```

## debug 参数（调试模式）

`debug` 参数用于开启 Indicator 的调试模式，记录每次 `calculate_vars()` 的计算结果。

**用途**：
- 调试 Indicator 的变量输出
- 排查 Executor 条件表达式问题
- 观察 Indicator 的实时计算结果

**配置参数**：
- `debug`: 是否开启调试模式（`true`/`false`，默认 `false`）
- `debug_log_interval`: 日志输出间隔（可选，支持 duration 字符串）
  - 未设置时：每次 `calculate_vars()` 都输出日志
  - 设置后：按指定间隔输出日志（避免日志过多）

**配置示例**：

```yaml
indicators:
  # 示例 1: 每次都输出日志
  ticker:
    class: TickerDataSource
    params:
      window: 1m
      debug: true  # 开启调试模式，每次都输出
    ready_condition: "timeout < 5"

  # 示例 2: 每 60 秒输出一次日志
  trades:
    class: TradesDataSource
    params:
      window: 5m
      debug: true
      debug_log_interval: 60s  # 每 60 秒输出一次
    ready_condition: "timeout < 60 and cv < 0.8"

  # 示例 3: 每 1 分钟输出一次日志
  rsi:
    class: RSIIndicator
    params:
      ohlcv: ohlcv
      period: 14
      debug: true
      debug_log_interval: 1m  # 等价于 60s
```

**日志输出示例**：

```
[INFO] [DEBUG] Indicator ticker calculate_vars(direction=1): {'last': 50000.0, 'bid': 49999.5, 'ask': 50000.5, 'mid': 50000.0, 'spread': 0.00002}
```

**注意事项**：
- debug 模式会产生大量日志，仅在开发/调试时使用
- 生产环境建议关闭（`debug: false` 或省略该参数）
- 使用 `debug_log_interval` 可以控制日志频率，避免日志过多
- `debug_log_interval` 支持 duration 字符串格式（如 `60s`, `1m`, `5m`）
- 日志级别为 INFO，需要确保日志配置允许 INFO 级别输出

## 相关文档

- [architecture.md](architecture.md) - 整体架构
- [config-path.md](config-path.md) - ConfigPath 类型、ExchangeConfigPathGroup 选择器与缓存
- [indicator.md](indicator.md) - Indicator 模块
- [executor.md](executor.md) - Executor 配置
- [scope.md](scope.md) - Scope 系统（links/ChainMap/target 输出）

## 兼容性与迁移提示

`strategies:`（列表）已被 `strategy:`（单条）取代；如需从旧配置迁移：

- 将 `strategies: [a/b]` 改为 `strategy: a/b`
- 若旧配置包含多条策略，需先合并/收敛为单条策略入口（保持 Executor 的 `strategies` namespace 仍使用列表聚合语义）
