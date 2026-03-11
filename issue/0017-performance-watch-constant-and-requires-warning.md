# Issue 0017: 性能回归 - per-symbol requires 警告与 watch 常数复杂度

**状态**: 待实现（Open）

## 背景

性能回归测试要求：
- 大规模交易对 N (50/200/1000/5000) 下，watch 数量应为常数复杂度，不随 N 线性增长。
- 当 Strategy 的 requires 依赖按 symbol 扩展的数据源 (如 ticker/orderbook) 时，需要输出全局 warning（仅一次）。

当前实现中，Strategy.requires 会为每个 symbol 创建对应 DataSource，导致 watch 任务数量与 N 线性增长，且无警告提示。

## 复现

1. 配置 Strategy：`include_symbols: ["*"]`，`requires: ["ticker"]`，交易对数量 N 增大。
2. 运行性能回归测试或调用 Strategy.get_target_positions_usd()。
3. 观察到每个 symbol 都创建并启动 watch 任务，watch 数量随 N 增长；无任何 warning。

## 影响

- 资源消耗不可控：WebSocket 连接数/任务数随 N 增长。
- 无明显提示：使用者难以察觉 requires 配置引发的复杂度上升。
- 与性能测试结论不一致：watch 数量无法维持常数复杂度。

## 期望行为

1. **watch 常数复杂度**：同一时间 active watch 数量应与 exchange_class 数量相关，而非与 symbol 数量相关。
2. **全局 warning**：Strategy.requires 依赖按 symbol 扩展的数据源时输出 warning（全局仅一次）。

## 方案建议

1. **DataSource 聚合订阅**
   - 引入 exchange 级订阅聚合器（支持 `watch_tickers` 或批量 fetch_tickers）。
   - DataSource 仅注册 symbol，实际 watch 由聚合器统一拉取并分发。
   - active watch 数量与 exchange_class 数量相关，实现 O(1)。
2. **per-symbol requires warning**
   - 在 Strategy 注入 requires 时调用 `indicator.set_requires_flag(True)`。
   - 对 per-symbol DataSource (Ticker/OrderBook/Trades/OHLCV) 加标记，并在首次被 Strategy.requires 触发时输出 warning。
   - 使用模块级 guard，确保仅输出一次。

## 验收标准

- `pytest -q tests/test_complexity_scaling.py` 通过（watch 数量不随 N 增长，warning 仅一次）。
- 在 `include_symbols: ["*"]` + `requires: ["ticker"]` 的配置下，active watch 数量与 N 脱钩。
- warning 只输出一次，不重复刷屏。

## TODO

- [ ] 明确聚合订阅接口（watch_tickers / fetch_tickers）及回退策略（待实现）
- [ ] Strategy.requires 触发 per-symbol DataSource warning（仅一次）（待实现）
- [ ] DataSource 订阅聚合器实现与单元测试（待实现）
