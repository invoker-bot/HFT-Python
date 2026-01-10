# HFT-Python

基于 Listener 架构的全异步高频交易策略框架。

## 架构概览

```
AppCore (根节点)
├── CacheListener              # 状态持久化（异步写入）
├── StateLogListener           # 状态日志输出
├── UnhealthyRestartListener   # 不健康自动重启
│
├── ExchangeGroup             # 交易所分组管理
│   ├── okx: [OKX_1, OKX_2, ...]
│   ├── binance: [Binance_1, ...]
│   └── ...
│
├── DataSourceGroup            # 数据源管理
│   └── [各类数据源...]
│
├── StrategyGroup              # 策略组
│   └── [各策略实例...]
│
└── MarketExecutor             # 交易执行器
```

## 核心组件

| 组件 | 职责 | 详见 |
|------|------|------|
| **AppCore** | 应用根节点，管理生命周期和退出流程 | `hft/core/app/base.py` |
| **ExchangeGroup** | 按类型分组管理多账户，数据去重 | `hft/exchange/group.py` |
| **StrategyGroup** | 管理策略，聚合并转发交易信号 | `hft/strategy/group.py` |
| **TradeSignal** | 策略与执行器的通信协议 | `hft/strategy/signal.py` |
| **MarketExecutor** | 监听信号，执行市价单 | `hft/executor/market.py` |
| **BaseExchange** | 交易所统一封装 | `hft/exchange/base.py` |

## 数据流

```
ExchangeGroup ──watch/fetch──> DataSourceGroup ──query──> Strategy
                                                              │
                                                         emit_signal()
                                                              │
                                                              ▼
ExchangeGroup <──create_order── MarketExecutor <── StrategyGroup.event
```

## 信号机制

策略通过 `TradeSignal` 表达交易意图：

```python
TradeSignal(
    exchange_class="okx",       # 交易所类型
    symbol="BTC/USDT:USDT",     # 交易对
    value=0.5,                  # 目标仓位 [-1.0, 1.0]
    speed=0.8,                  # 执行紧急度 [0.0, 1.0]
)
```

- `value`: 目标仓位比例，+1.0=全仓做多，-1.0=全仓做空，0=平仓
- `speed`: 紧急度，>=0.8 使用市价单，<0.5 可用限价单

## 退出流程

级联退出机制：

1. `Strategy.on_tick()` 返回 `True` → 策略完成，从 StrategyGroup 移除
2. `StrategyGroup.is_finished` 变为 `True` → `StrategyGroup.on_tick()` 返回 `True`
3. `AppCore.on_tick()` 检测到策略组完成 → 返回 `True` → 程序退出

## 特性

| 特性 | 说明 |
|------|------|
| **全异步** | 基于 asyncio，非阻塞 I/O |
| **可持续运行** | 状态持久化 + 断点恢复 + 异常自愈 |
| **资源高效** | 自动订阅管理，数据去重 |
| **灵活扩展** | Listener 树形结构，易于添加组件 |
| **多账户** | 同类交易所多账户同步执行 |
| **事件驱动** | Strategy → Signal → Executor 事件链 |

## 配置示例

```yaml
# conf/app/app.yaml
class_name: app
health_check_interval: 60.0
log_interval: 30.0
database_url: clickhouse://user:pass@localhost:8123/hft

exchanges:
  - okx_main
  - okx_sub1

strategies:
  - funding_rate_arbitrage
```

## 快速开始

```bash
# 运行应用
hft run main app

# 调试模式（不下单）
hft -p null run main app
```
