# Issue: `hft run test exchange` 因缺少 `fetch_balance` 崩溃

> **状态**: ✅ 已完成，审核通过
> **发现命令**: `hft -p null run test exchange demo/okx`
> **现象**: 直接崩溃，无法进入后续 API 测试

## 复现

```bash
hft -p null run test exchange demo/okx
```

报错（节选）：

```
Error: 'OKXExchange' object has no attribute 'fetch_balance'
```

## 根因分析

- `hft/test/exchange.py` 在 REST API Tests 阶段会调用 `exchange.fetch_balance`（作为可调用对象传给 `_test_api`）。
- `BaseExchange` 已封装了 `fetch_ticker/fetch_order_book/fetch_trades/fetch_ohlcv/fetch_positions/...`，但缺少 `fetch_balance()` 方法。
- 因为属性不存在，异常发生在 `_test_api` 调用之前，导致整段测试流程中断，而不是记录为 FAIL 并继续。

## 修复方案（建议）

1) 在 `hft/exchange/base.py` 增加 `async def fetch_balance(self) -> dict`：
- 默认使用 `self.config.ccxt_instance.fetch_balance()`；
- 可选：调用 `medal_cache_balance()` 走统一缓存与 hook（若能确定 ccxt_instance_key）。

2) 补充单测，确保 `BaseExchange.fetch_balance` 存在且为 async method（避免回归）。

3) 验证命令可继续执行（即使因 demo key/权限导致 API 返回失败，也应在报告中显示 FAIL 而不是崩溃）。

## TODO

- [x] 增加 `BaseExchange.fetch_balance()` 兼容封装（审核完成，hft/exchange/base.py）
- [x] 增加单测覆盖 `fetch_balance` 方法存在性（审核完成，tests/test_exchange_fetch_balance.py）
- [x] 验证 `hft -p null run test exchange demo/okx` 可完整出报告（审核完成，用户已验证）
