# Scope 系统使用指南

本文档介绍如何使用 Scope 系统实现层级化的变量管理和数据流。

## 什么是 Scope 系统

Scope 系统是一个分层的变量作用域机制，支持：
- **层级化变量**：不同层级的 Scope 管理不同范围的变量
- **自动继承**：子 Scope 自动继承父 Scope 的变量
- **灵活链路**：通过配置定义 Scope 链路
- **聚合计算**：支持跨层级的聚合操作

## Scope 层级体系

| Scope 类型 | 说明 | Instance ID 示例 |
|-----------|------|-----------------|
| GlobalScope | 全局作用域 | `"global"` |
| ExchangeClassScope | 交易所类型 | `"okx"`, `"binance"` |
| ExchangeScope | 交易所实例 | `"okx/main"`, `"binance/spot"` |
| TradingPairClassScope | 交易对类型 | `"okx-BTC/USDT"` |
| TradingPairScope | 交易对实例 | `"okx/main-BTC/USDT"` |

## 基本使用

### 1. 在 App 配置中声明 Scope

```yaml
# conf/app/my_app.yaml
scopes:
  global:
    class: GlobalScope
    vars:
      - name: max_position_ratio
        value: 0.8
      - name: risk_factor
        value: 1.0

  exchange:
    class: ExchangeScope
    vars:
      - name: available_usd
        value: equation_usd * max_position_ratio
```

### 2. 在 Strategy 中使用 Scope

```yaml
# conf/strategy/my_strategy.yaml
class_name: static_positions

requires:
  - ticker
  - equation

links:
  - [global, exchange, trading_pair]

targets:
  - exchange_id: '*'
    symbol: BTC/USDT
    position_usd: 'available_usd * 0.5'
    speed: 0.5
```

## 示例：多交易所资金分配

### 场景描述

在多个交易所上交易 BTC/USDT，根据各交易所的账户余额动态分配仓位。

### App 配置

```yaml
# conf/app/multi_exchange.yaml
exchanges:
  - okx/main
  - binance/spot

strategy: multi_exchange/btc_strategy
executor: multi_exchange/market

scopes:
  global:
    class: GlobalScope
    vars:
      - name: total_target_usd
        value: 10000
      - name: max_leverage
        value: 3.0

  exchange:
    class: ExchangeScope
    vars:
      - name: weight
        value: equation_usd / sum([child["equation_usd"] for child in parent.children.values()])
      - name: allocated_usd
        value: total_target_usd * weight
```

### Strategy 配置

```yaml
# conf/strategy/multi_exchange/btc_strategy.yaml
class_name: static_positions

requires:
  - ticker
  - equation

links:
  - [global, exchange, trading_pair]

targets:
  - exchange_id: '*'
    symbol: BTC/USDT
    position_usd: allocated_usd
    speed: 0.5
```

### 执行流程

1. **Global Scope**：定义总目标仓位 `total_target_usd = 10000`
2. **Exchange Scope**：计算每个交易所的权重和分配金额
   - OKX: `weight = okx_equation / (okx_equation + binance_equation)`
   - Binance: `weight = binance_equation / (okx_equation + binance_equation)`
3. **TradingPair Scope**：使用分配的金额设置目标仓位

## 相关文档

- [docs/scope.md](../docs/scope.md) - Scope 系统架构
- [docs/vars.md](../docs/vars.md) - 变量系统设计
- [docs/scope-execution-flow.md](../docs/scope-execution-flow.md) - 执行流程

