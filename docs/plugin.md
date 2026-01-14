# Plugin 插件系统

## 概述

插件系统基于 [pluggy](https://pluggy.readthedocs.io/) 实现，允许用户在不修改核心代码的情况下扩展 HFT 框架功能。

## Hook 实现状态

| Hook | 状态 | 调用位置 |
|------|------|---------|
| `on_app_start` | ✅ | `AppCore.on_start()` |
| `on_app_stop` | ✅ | `AppCore.on_stop()` |
| `on_app_tick` | ✅ | `AppCore.on_tick()` |
| `on_listener_start` | ✅ | `Listener.on_start()` |
| `on_listener_stop` | ✅ | `Listener.on_stop()` |
| `on_order_creating` | ✅ | `BaseExchange.create_order()` 前 |
| `on_order_created` | ✅ | `BaseExchange.create_order()` 成功后 |
| `on_order_cancelled` | ✅ | `BaseExchange.cancel_order()` 后 |
| `on_order_error` | ✅ | `BaseExchange.create_order()` 失败时 |
| `on_order_filled` | ✅ | `ExchangeOrderBillListener._emit_order_filled_hook()` |
| `on_strategy_targets_calculated` | ✅ | `StrategyGroup.get_aggregated_targets()` |
| `on_targets_aggregated` | ✅ | `StrategyGroup.get_aggregated_targets()` |
| `on_execution_start` | ✅ | `BaseExecutor.on_tick()` 执行前 |
| `on_execution_complete` | ✅ | `BaseExecutor.on_tick()` 执行后 |
| `on_balance_update` | ✅ | `BaseExchange.medal_cache_balance()` |
| `on_position_update` | ✅ | `BaseExchange.medal_cache_positions()` |
| `on_ticker_update` | ✅ | `TickerDataSource._emit_plugin_hook()` |
| `on_funding_rate_update` | ✅ | `GlobalFundingRateFetcher._fetch_and_distribute()` |
| `on_notify` | ✅ | `NotifyService.send()` |
| `on_health_check_failed` | ✅ | `Listener.health_check()` 失败时 |

## 设计原则

| 原则 | 说明 |
|------|------|
| 非侵入性 | 插件不修改核心代码，通过钩子注入 |
| 可组合 | 多个插件可以同时工作，互不干扰 |
| 配置驱动 | 插件通过 YAML 配置启用和参数化 |
| 生命周期感知 | 插件与 Listener 生命周期集成 |

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                      AppCore                             │
│  ┌─────────────────────────────────────────────────┐    │
│  │              PluginManager                       │    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐           │    │
│  │  │Plugin A │ │Plugin B │ │Plugin C │  ...       │    │
│  │  └────┬────┘ └────┬────┘ └────┬────┘           │    │
│  │       │           │           │                 │    │
│  │       └───────────┼───────────┘                 │    │
│  │                   v                             │    │
│  │              HookSpec                           │    │
│  │    on_app_start, on_order_created, ...         │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                                │
│    ┌────────────────────┼────────────────────┐          │
│    v                    v                    v          │
│ Exchange           Strategy              Executor       │
└─────────────────────────────────────────────────────────┘
```

## Hook 分类

### 1. 生命周期 Hooks

与 Listener 状态机同步的钩子：

```python
@hookspec
def on_app_start(app: "AppCore"):
    """应用启动时调用"""

@hookspec
def on_app_stop(app: "AppCore"):
    """应用停止时调用"""

@hookspec
def on_app_tick(app: "AppCore"):
    """每个 tick 循环调用"""

@hookspec
def on_listener_start(listener: "Listener"):
    """任何 Listener 启动时调用"""

@hookspec
def on_listener_stop(listener: "Listener"):
    """任何 Listener 停止时调用"""
```

### 2. 交易 Hooks

交易相关事件的钩子：

```python
@hookspec
def on_order_creating(exchange: "BaseExchange", symbol: str, side: str, amount: float, price: float) -> bool:
    """
    订单创建前调用

    Returns:
        True 允许创建，False 阻止创建
    """

@hookspec
def on_order_created(exchange: "BaseExchange", order: dict):
    """订单创建成功后调用"""

@hookspec
def on_order_filled(exchange: "BaseExchange", order: dict):
    """订单成交后调用"""

@hookspec
def on_order_cancelled(exchange: "BaseExchange", order: dict):
    """订单取消后调用"""

@hookspec
def on_order_error(exchange: "BaseExchange", error: Exception, order_params: dict):
    """订单创建失败时调用"""
```

### 3. 策略 Hooks

策略执行相关的钩子：

```python
@hookspec
def on_strategy_targets_calculated(strategy: "BaseStrategy", targets: "TargetPositions"):
    """策略计算出目标仓位后调用"""

@hookspec
def on_targets_aggregated(strategy_group: "StrategyGroup", targets: "AggregatedTargets"):
    """策略组聚合目标后调用"""

@hookspec
def on_execution_start(executor: "BaseExecutor", targets: "AggregatedTargets"):
    """执行器开始执行前调用"""

@hookspec
def on_execution_complete(executor: "BaseExecutor", results: list):
    """执行器执行完成后调用"""
```

### 4. 数据 Hooks

市场数据相关的钩子：

```python
@hookspec
def on_ticker_update(exchange: "BaseExchange", symbol: str, ticker: dict):
    """Ticker 更新时调用"""

@hookspec
def on_balance_update(exchange: "BaseExchange", balance: dict):
    """余额更新时调用"""

@hookspec
def on_position_update(exchange: "BaseExchange", positions: dict):
    """持仓更新时调用"""

@hookspec
def on_funding_rate_update(exchange: "BaseExchange", symbol: str, funding_rate: dict):
    """资金费率更新时调用"""
```

### 5. 通知 Hooks

通知和告警相关的钩子：

```python
@hookspec
def on_notify(level: str, title: str, message: str):
    """
    发送通知时调用

    Args:
        level: "info", "warning", "error"
        title: 通知标题
        message: 通知内容
    """

@hookspec
def on_health_check_failed(listener: "Listener", error: Exception):
    """健康检查失败时调用"""
```

## 插件实现

### 基础插件类

```python
from hft.plugin import PluginBase, hookimpl

class MyPlugin(PluginBase):
    """自定义插件"""

    name = "my_plugin"  # 插件名称，用于配置引用

    def __init__(self, config: dict = None):
        self.config = config or {}

    @hookimpl
    def on_app_start(self, app):
        print(f"App started: {app.name}")

    @hookimpl
    def on_order_created(self, exchange, order):
        print(f"Order created on {exchange.name}: {order}")
```

### 插件配置

```yaml
# conf/app/main.yaml
plugins:
  - name: telegram_notifier
    config:
      bot_token: "xxx"
      chat_id: "xxx"

  - name: trade_logger
    config:
      log_file: "trades.log"
      format: "json"

  - name: risk_limiter
    config:
      max_daily_loss_usd: 1000
      max_position_usd: 10000
```

### 内置插件示例

#### 1. TelegramNotifier - Telegram 通知插件

```python
class TelegramNotifierPlugin(PluginBase):
    """Telegram 通知插件"""

    name = "telegram_notifier"

    def __init__(self, config: dict):
        self.bot_token = config["bot_token"]
        self.chat_id = config["chat_id"]

    @hookimpl
    def on_notify(self, level, title, message):
        emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}
        text = f"{emoji.get(level, '')} {title}\n{message}"
        # 发送 Telegram 消息
        ...

    @hookimpl
    def on_order_filled(self, exchange, order):
        self._send(f"订单成交: {order['symbol']} {order['side']} {order['amount']}")
```

#### 2. TradeLogger - 交易日志插件

```python
class TradeLoggerPlugin(PluginBase):
    """交易日志插件 - 记录所有交易到文件"""

    name = "trade_logger"

    def __init__(self, config: dict):
        self.log_file = config.get("log_file", "trades.log")
        self.format = config.get("format", "json")

    @hookimpl
    def on_order_created(self, exchange, order):
        self._log("created", exchange, order)

    @hookimpl
    def on_order_filled(self, exchange, order):
        self._log("filled", exchange, order)

    def _log(self, event, exchange, order):
        record = {
            "timestamp": time.time(),
            "event": event,
            "exchange": exchange.name,
            "order": order,
        }
        with open(self.log_file, "a") as f:
            if self.format == "json":
                f.write(json.dumps(record) + "\n")
```

#### 3. RiskLimiter - 风控插件

```python
class RiskLimiterPlugin(PluginBase):
    """风控插件 - 限制交易风险"""

    name = "risk_limiter"

    def __init__(self, config: dict):
        self.max_daily_loss = config.get("max_daily_loss_usd", 1000)
        self.max_position = config.get("max_position_usd", 10000)
        self._daily_pnl = 0.0

    @hookimpl
    def on_order_creating(self, exchange, symbol, side, amount, price) -> bool:
        """订单创建前检查风控"""
        order_value = amount * price

        # 检查单笔订单大小
        if order_value > self.max_position:
            logger.warning("Order rejected: exceeds max position")
            return False

        # 检查日亏损
        if self._daily_pnl < -self.max_daily_loss:
            logger.warning("Order rejected: daily loss limit reached")
            return False

        return True

    @hookimpl
    def on_order_filled(self, exchange, order):
        """更新 PnL 统计"""
        # 简化版：实际需要更复杂的 PnL 计算
        ...
```

#### 4. MetricsCollector - 指标收集插件

```python
class MetricsCollectorPlugin(PluginBase):
    """指标收集插件 - 导出 Prometheus 指标"""

    name = "metrics_collector"

    def __init__(self, config: dict):
        self.port = config.get("port", 9090)
        # 初始化 Prometheus metrics
        self.orders_total = Counter("orders_total", "Total orders", ["exchange", "side"])
        self.position_gauge = Gauge("position_usd", "Position in USD", ["exchange", "symbol"])

    @hookimpl
    def on_order_filled(self, exchange, order):
        self.orders_total.labels(exchange=exchange.name, side=order["side"]).inc()

    @hookimpl
    def on_position_update(self, exchange, positions):
        for symbol, amount in positions.items():
            # 需要价格计算 USD 价值
            ...
```

## 插件注册

### 方式一：setuptools entry_points

```toml
# pyproject.toml
[project.entry-points."hft"]
telegram_notifier = "my_plugins.telegram:TelegramNotifierPlugin"
trade_logger = "my_plugins.logger:TradeLoggerPlugin"
```

### 方式二：显式注册

```python
from hft.plugin import pm

# 注册插件实例
pm.register(TelegramNotifierPlugin(config))

# 或注册插件类（框架自动实例化）
pm.register(TelegramNotifierPlugin)
```

### 方式三：配置文件自动加载

```yaml
# conf/app/main.yaml
plugins:
  - name: telegram_notifier  # 从 entry_points 查找
    config: {...}

  - module: my_plugins.custom:CustomPlugin  # 直接指定模块路径
    config: {...}
```

## Hook 调用时机

### 核心代码集成点

```python
# hft/exchange/base.py
class BaseExchange(Listener):
    async def create_order(self, symbol, side, amount, price=None):
        # 调用 hook: 允许插件阻止订单
        results = pm.hook.on_order_creating(
            exchange=self, symbol=symbol, side=side, amount=amount, price=price
        )
        if False in results:
            raise OrderRejectedException("Order rejected by plugin")

        try:
            order = await self._create_order_impl(symbol, side, amount, price)
            # 调用 hook: 通知订单创建成功
            pm.hook.on_order_created(exchange=self, order=order)
            return order
        except Exception as e:
            pm.hook.on_order_error(exchange=self, error=e, order_params={...})
            raise
```

```python
# hft/core/app/base.py
class AppCore(Listener):
    async def on_start(self):
        await super().on_start()
        pm.hook.on_app_start(app=self)

    async def on_stop(self):
        pm.hook.on_app_stop(app=self)
        await super().on_stop()
```

## Hook 执行顺序

pluggy 支持通过装饰器控制 hook 执行顺序：

```python
class EarlyPlugin(PluginBase):
    @hookimpl(tryfirst=True)  # 最先执行
    def on_order_creating(self, ...):
        ...

class LatePlugin(PluginBase):
    @hookimpl(trylast=True)  # 最后执行
    def on_order_creating(self, ...):
        ...

class WrapperPlugin(PluginBase):
    @hookimpl(hookwrapper=True)  # 包装其他 hook
    def on_order_creating(self, ...):
        # 前置逻辑
        outcome = yield  # 执行其他 hooks
        # 后置逻辑
        result = outcome.get_result()
```

## 异步 Hook 支持

对于需要异步操作的 hook，使用 `async_hookimpl`：

```python
from hft.plugin import async_hookimpl

class AsyncPlugin(PluginBase):
    @async_hookimpl
    async def on_order_created(self, exchange, order):
        # 异步操作
        await self.send_notification(order)
```

调用时使用 `await pm.ahook.on_order_created(...)`。

## 最佳实践

### 1. 保持插件独立

```python
# Good: 插件自包含
class GoodPlugin(PluginBase):
    def __init__(self, config):
        self.db = self._init_db(config["db_url"])

# Bad: 依赖全局状态
class BadPlugin(PluginBase):
    def __init__(self):
        from hft.some_module import global_db
        self.db = global_db
```

### 2. 处理异常

```python
class SafePlugin(PluginBase):
    @hookimpl
    def on_order_created(self, exchange, order):
        try:
            self._process_order(order)
        except Exception as e:
            # 记录错误但不影响其他插件
            logger.error("Plugin error: %s", e)
```

### 3. 资源清理

```python
class ResourcePlugin(PluginBase):
    @hookimpl
    def on_app_start(self, app):
        self.connection = self._connect()

    @hookimpl
    def on_app_stop(self, app):
        if self.connection:
            self.connection.close()
```

### 4. 配置验证

```python
class ConfiguredPlugin(PluginBase):
    def __init__(self, config: dict):
        required = ["api_key", "endpoint"]
        for key in required:
            if key not in config:
                raise ValueError(f"Missing required config: {key}")
        self.config = config
```

## 调试插件

### 查看已注册插件

```python
from hft.plugin import pm

# 列出所有插件
for plugin in pm.get_plugins():
    print(f"Plugin: {plugin.name}")

# 检查 hook 实现
for impl in pm.hook.on_order_created.get_hookimpls():
    print(f"  - {impl.plugin_name}")
```

### 启用 Hook 调用追踪

```python
# 设置环境变量
export HFT_PLUGIN_TRACE=1

# 或在代码中
pm.trace.root.setwriter(print)
pm.enable_tracing()
```

## 目录结构

```
hft/
└── plugin/
    ├── __init__.py      # 导出 pm, hookspec, hookimpl
    ├── base.py          # PluginBase, HookSpec 定义
    ├── manager.py       # PluginManager 扩展
    └── builtin/         # 内置插件
        ├── __init__.py
        ├── logger.py        # TradeLoggerPlugin
        ├── notifier.py      # TelegramNotifierPlugin
        ├── risk.py          # RiskLimiterPlugin
        └── metrics.py       # MetricsCollectorPlugin
```

## 与现有系统集成

插件系统与现有 Listener 架构互补：

| 场景 | 使用 Listener | 使用 Plugin |
|------|--------------|-------------|
| 需要持续运行 | ✓ | |
| 需要状态管理 | ✓ | |
| 响应事件/钩子 | | ✓ |
| 横切关注点 | | ✓ |
| 可选功能扩展 | | ✓ |
| 第三方集成 | | ✓ |

示例：日志记录
- **Listener 方式**：创建 LogListener，每个 tick 主动拉取数据
- **Plugin 方式**：通过 hook 被动接收事件，更轻量

## 迁移指南

将现有 Listener 改造为 Plugin：

```python
# Before: Listener 方式
class NotifyListener(Listener):
    async def on_tick(self):
        # 轮询检查...
        if should_notify:
            self._send_notification()

# After: Plugin 方式
class NotifyPlugin(PluginBase):
    @hookimpl
    def on_order_filled(self, exchange, order):
        self._send_notification(order)
```
