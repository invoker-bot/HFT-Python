# 交易所模块文档

## 概述

交易所模块封装了与交易所 API 的交互，支持多账户管理。

## 类层次

```
BaseExchange (抽象基类)
├── OKXExchange         # OKX 交易所
└── BinanceExchange     # Binance 交易所

ExchangeGroup           # 多账户分组管理
```

## BaseExchange

### 核心功能

| 功能 | 方法 |
|------|------|
| 下单 | `create_order()`, `create_limit_order()`, `create_market_order()` |
| 撤单 | `cancel_order()`, `cancel_all_orders()` |
| 持仓 | `fetch_positions()`, `medal_fetch_positions()` |
| 余额 | `fetch_balance()`, `medal_fetch_balance()` |
| 订单 | `fetch_order()`, `fetch_open_orders()` |

### 配置

```yaml
# conf/exchange/okx/demo.yaml
class_name: okx

ccxt_instances:
  main:
    api_key: "encrypted..."
    api_secret: "encrypted..."
    passphrase: "encrypted..."

trading_config:
  leverage: 10
  margin_mode: cross
```

### medal_* 方法

带缓存和健康检查的包装方法：

```python
async def medal_fetch_positions(self) -> dict[str, float]:
    """获取持仓（带缓存）"""
    if self._positions.is_healthy:
        return self._positions.get_unchecked()
    return await self._positions.get_or_fetch()
```

## ExchangeGroup

### 功能

- 管理多个交易所账户
- 按名称获取交易所
- 聚合所有账户的仓位/余额

### 使用

```python
# 获取交易所
exchange = exchange_group.get_exchange("okx_main")

# 遍历所有交易所
for exchange in exchange_group:
    await exchange.fetch_positions()
```

## 子监听器

### ExchangeBalanceListener

监控账户余额变化。

```python
class ExchangeBalanceListener(GroupListener):
    """
    动态创建子节点：
    - watch-{key}: WebSocket 监听余额
    - fetch-{key}: REST 轮询余额
    """
```

### ExchangePositionListener

监控持仓变化。

```python
class ExchangePositionListener(GroupListener):
    """
    动态创建子节点：
    - watch: WebSocket 监听持仓
    """
```

### ExchangeOrderBillListener

监控订单状态。

```python
class ExchangeOrderBillListener(GroupListener, DataListener):
    """
    动态创建子节点：
    - watch-{key}: WebSocket 监听订单
    - fetch: REST 轮询订单状态
    """
```

## 交易所树形结构

```
BaseExchange
├── ExchangeBalanceListener
│   ├── watch-main (ExchangeBalanceWatchListener)
│   └── fetch-main (ExchangeBalanceFetchListener)
├── ExchangePositionListener
│   └── watch (ExchangePositionWatchListener)
└── ExchangeOrderBillListener
    ├── watch-main (ExchangeOrderBillWatchListener)
    └── fetch (ExchangeOrderBillFetchListener)
```

## 合约规格

```python
# 获取合约大小
contract_size = exchange.get_contract_size(symbol)

# 计算下单数量
base_amount = usd / price
contracts = base_amount / contract_size

# 计算仓位数量
base_amount = contracts * contract_size
```

## 错误处理

```python
try:
    order = await exchange.create_order(...)
except ccxt.InsufficientFunds:
    logger.error("Insufficient funds")
except ccxt.InvalidOrder:
    logger.error("Invalid order parameters")
except Exception as e:
    logger.exception("Order failed: %s", e)
```
