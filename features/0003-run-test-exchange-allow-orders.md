# Feature: `hft run test exchange` 支持 `--allow-orders`

> **状态**：全部通过

## 背景

`hft run test exchange <path>` 仅做连通性/延迟测试，无法验证下单链路（create_order/cancel/平仓参数等）。需要一个显式开关来允许在测试网执行小额市价单，以便端到端验证交易所配置与权限。

## 目标

新增参数 `--allow-orders`（默认关闭）。开启时在测试流程中追加订单测试：

- **ETH 现货**：市价买入 `0.01` ETH → 市价卖出 `0.01` ETH
- **ETH 合约（swap）**：市价开仓买入 `0.01` ETH → 市价卖出 `0.01` ETH（`reduceOnly` 平仓）

## 关键约束

- 安全默认：不开启 `--allow-orders` 时不会产生任何订单。
- 使用测试模式：以 exchange config 的 `test: true` 为准（实现侧会将其映射到 ccxt 的 `sandbox`/demo 交易模式）。
- 合约数量口径：订单测试里的 `0.01` 表示 **基础货币数量**（与 `contractSize` 无关），在下单前会换算为 ccxt 需要的合约张数：`contracts = base_amount / contractSize`。
- Spot 市场可用性：若当前 exchange config 未启用 spot instance（仅 swap），则在测试中临时创建 spot ccxt 实例完成现货下单测试。

## 实现

- CLI：`hft/bin/run.py` 为 `hft run test exchange` 增加 `--allow-orders`
- 测试流程：`hft/test/exchange.py` 在 REST tests 后追加 Order Tests（仅在 allow_orders=True 时运行）

## TODO

- [x] `--allow-orders` 参数与下单测试逻辑（已通过，hft/bin/run.py / hft/test/exchange.py）
- [x] 现货/合约下单 amount 口径与 reduceOnly 行为复核（已通过）
- [x] 手工验证：`hft -p <pwd> run test exchange <path> --allow-orders`（已通过，用户已验证）
