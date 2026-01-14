# Feature: SmartExecutor（智能执行器路由 / 多执行器编排）

## 背景与目标
当前已存在多种执行器：`MarketExecutor`（市价）、`LimitExecutor`（普通限价/网格）、`AvellanedaStoikovExecutor`（AS 做市）、`PCAExecutor`（分批/加仓）。  
本 Feature 提议新增一个“路由型执行器”`SmartExecutor`：自身作为唯一挂到 Strategy 下的 Executor，对每个 `(exchange, symbol)` 的 `delta_usd/speed` **动态选择**子执行器，既支持手工指定，也支持基于市场公共成交数据的自动决策。

## 配置约定
### SmartExecutorConfig（executor 层）
`children` 采用“配置路径引用”（即 `BaseConfig.path` 格式，路径相对 `conf/executor/`，不含 `.yaml`）：

```yaml
# conf/executor/smart/default.yaml
class_name: smart
interval: 1.0
speed_threshold: 0.9
trades_window_seconds: 300      # 5min
min_trades: 50                 # 低于则回退默认
default_executor: as           # 未命中任何规则时使用
children:
  market: market/default
  limit: limit/maker
  as: avellaneda_stoikov/default
  pca: pca/default
```

### ExchangeConfig（exchange 层）
允许在交易所配置中按交易对强制指定执行器（优先级最高）：

```yaml
# conf/exchange/okx/main.yaml
executor_map:
  "BTC/USDT:USDT": pca
  "ETH/USDT:USDT": limit
```

> 说明：`executor_map` 的 value 必须是 `SmartExecutorConfig.children` 的 key（如 `pca/limit/as/market`）。

## 路由规则（从高到低）
对每个 `(exchange, symbol, delta_usd, speed, current_price)`：
1. **显式路由**：若 `exchange.config.executor_map[symbol]` 存在且在 `children` 中 → 直接使用该子 executor。
2. **速度阈值**：若 `speed > speed_threshold` → 使用 `market`（优先保证成交速度与确定性）。
3. **自动选择（market vs AS）**：基于最近 `trades_window_seconds` 的公共 trades + 费率，判断“taker（市价）是否仍有优势”。
4. **默认回退**：数据不足（`min_trades`）或计算失败 → `default_executor`（建议默认 `as`）。

## “taker 优势”计算（基于公共 TradesDataSource）
数据来源：`hft/datasource/trades_datasource.py:TradeData`（`side/price/amount/cost/timestamp`），取窗口内 trades。

记：
- `p_final = current_price`（优先使用 executor 传入的当前价；也可退化为窗口内最后一笔成交价）
- `B = {t | t.side == "buy"}`，`S = {t | t.side == "sell"}`
- `buy_qty = Σ t.amount (t∈B)`，`buy_notional = Σ t.cost (t∈B)`
- `sell_qty = Σ t.amount (t∈S)`，`sell_notional = Σ t.cost (t∈S)`
- `taker_fee = exchange.config.swap_taker_fee`（或根据 trade_type 选择 spot/swap）

方向相关（用于当前这次 `delta_usd` 的 side）：
- 若本次需要 **买入**（`delta_usd > 0`）：
  - `vwap_buy = buy_notional / buy_qty`
  - `edge_buy_usd = buy_qty * (p_final - vwap_buy) - taker_fee * buy_notional`
- 若本次需要 **卖出**（`delta_usd < 0`）：
  - `vwap_sell = sell_notional / sell_qty`
  - `edge_sell_usd = sell_qty * (vwap_sell - p_final) - taker_fee * sell_notional`

判定（v1）：若 `edge_side_usd > 0` → 认为 taker 在该方向“近期能覆盖成本/手续费”，选择 `market`；否则选择 `as`（更偏 maker/挂单）。

> 可选增强（v1.1）：若结合 `order_book` 估计点差/滑点，可将“maker 的潜在价格改进 + fee 差异”作为额外门槛，例如比较 `edge_side_ratio` 与 `(spread/mid + (taker_fee-maker_fee))`。

## 集成与实现要点（供落地时参考）
- `SmartExecutor` 自身继承 `BaseExecutor`，只实现 `execute_delta()`，内部把调用委托给选中的 child executor 的 `execute_delta()`。
- 子 executor 作为 Listener children **但必须避免独立 tick**（建议：设置 `lazy_start=True` 并保持 `STOPPED`，或直接 `enabled=False`），否则会出现多个 executor 同时消费 targets 的冲突。
- 订单生命周期：各子 executor 自己维护 `_active_orders`；`SmartExecutor.on_stop()` 需汇总调用各 child 的 `cancel_all_orders()`。
- 可观测性：每次路由打印一行结构化日志（symbol、speed、命中规则、edge 值、trades_count、选用 executor）。

