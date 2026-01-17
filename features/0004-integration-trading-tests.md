# Feature: 集成测试（真实下单 + 历史记录校验）

> **状态**：全部通过

## 背景

现有单元测试覆盖了路由/表达式/缓存/并发等逻辑，但无法验证真实交易链路（交易所配置、权限、下单参数、成交/未成交预期、以及历史记录落地）。需要一套可控、可重复、默认安全的集成测试。

## 目标

基于 `conf/*/demo` 的 demo 配置（测试网、密码统一为 `null`），构建最小运行环境，完成以下集成验证：

1. **交易所 API 下单链路**：OKX / Binance demo 配置可下单（现货 + 合约）。
2. **执行器链路**：通过少量 demo executor + demo app/strategy 组合，验证实际 tick 能触发条件并创建订单。
3. **历史记录正确性**：至少验证“订单创建/撤单/成交”在可查询的历史中可见（ccxt `fetch_my_trades` / `fetch_orders` / 本地落地记录）。

## 非目标

- 不做笛卡尔积（n executors * m apps * k exchanges）全覆盖；只做少量组合覆盖关键路径。
- 不要求所有 limit 单必定成交；重点校验“价格/方向/数量”合理，以及未成交的订单可被正确发现和撤销。

## 安全与开关

- **唯一开关**：集成测试是否执行完全由 `INTEGRATION_TEST_ALLOW_LISTS` 环境变量控制
- 环境变量（未定义时使用默认值）：
  - `INTEGRATION_TEST_ALLOW_LISTS=""`（默认空：全部跳过，不会真实下单）
  - `INTEGRATION_TEST_DELAY_TIMEOUT=30`（每个分组运行时长）
  - `INTEGRATION_TEST_ALLOW_APP_LISTS="*"`（默认：允许所有 app；若设为具体列表则只验证指定 app）
- 分组 allow-list 机制：
  - `INTEGRATION_TEST_ALLOW_LISTS="0,1,2"`：仅允许执行指定分组（逗号/空格分隔均可）
  - `INTEGRATION_TEST_ALLOW_LISTS="*"`：允许执行全部分组
  - 未设置/空字符串：全部跳过（并输出清晰跳过原因）
- pytest-integration 插件（可选，用于按标记过滤测试）：
  - 安装方式：`pip install pytest-integration`
  - `@pytest.mark.integration_test`：标记为快速集成测试（分组 0、1）
  - `@pytest.mark.slow_integration_test`：标记为慢速集成测试（分组 2，App tick 级）
- 运行示例：
  - 跑分组 0、1：`INTEGRATION_TEST_ALLOW_LISTS="0,1" pytest tests/test_integration_trading.py -v -s`
  - 跑全部分组：`INTEGRATION_TEST_ALLOW_LISTS="*" pytest tests/test_integration_trading.py -v -s`
  - 仅跑快速集成测试（需安装插件）：`INTEGRATION_TEST_ALLOW_LISTS="*" pytest --integration -v -s`
  - 包含慢速集成测试（需安装插件）：`INTEGRATION_TEST_ALLOW_LISTS="*" pytest --integration-cover -v -s`
  - 默认不跑集成：直接 `pytest -q`（环境变量未设置时全部跳过）
- 强制要求 `conf/exchange/demo/*` 的 `test: true` 为真；否则跳过并报清晰原因
- 单笔订单规模：现货固定 1 SOL，合约约 100 USD
- **测试开始前与结束后**：必须保证 SOL 现货余额≈0、ETH 合约仓位≈0，并取消相关挂单
- 交易等待：每次下单后等待 `5–30s`（实现时可固定 10s + 随机抖动）以让成交/状态可见

## 集成测试分组（草案）

- 0：交易所 API 级（spot+swap 市价开平 + `fetch_my_trades` 可见性）
- 1：限价单校验（far/near 下单参数合理性 + 可查询/可撤单）
- 2：App tick 级（少量 demo app/executor/strategy 组合跑 30s，验证能触发下单并能收尾清理）

## 目录与配置规划（demo）

### 1) Exchange demo（已存在）

- `conf/exchange/demo/okx.yaml`
- `conf/exchange/demo/binance.yaml`

密码：`null`

说明：
- 如果需要覆盖 spot 子用例，demo exchange 配置应包含：`support_types: [spot, swap]`
- 若 demo exchange 未开启 spot 支持，则 spot 子用例应跳过（只跑 swap 子用例），并在测试输出中提示原因

### 2) Executor demo（新增）

在 `conf/executor/demo/` 新增少量配置文件（示例命名）：

- `market_eth.yaml`：MarketExecutor，用于保证市价单必成交路径
- `limit_far_eth.yaml`：LimitExecutor，偏离当前价 5%（默认不成交，用于挂单验证）
- `limit_near_eth.yaml`：LimitExecutor，偏离当前价 1%（不保证成交，但用于"价格/方向/数量"合理性校验）
- `smart_eth.yaml`：SmartExecutor，routes 覆盖 `speed` / `edge/notional`（触发条件用 market，否则用 limit）

### 3) Strategy demo（新增）

在 `conf/strategy/demo/` 新增用于触发交易的策略配置/入口：

- `keep_positions_eth.yaml`：基于 `keep_positions`，针对 ETH 的目标仓位可在测试中切换（0 ↔ +100USD ↔ 0）
- 需要最小化依赖，便于在 app/demo 中引用

### 4) App demo（新增）

在 `conf/app/demo/` 新增少量 app 配置，随机配对覆盖：

- `okx_market_keep_positions.yaml`（OKX + market + keep_positions）
- `binance_limit_keep_positions.yaml`（Binance + limit + keep_positions）
- `okx_smart_keep_positions.yaml`（OKX + smart + keep_positions）

说明：
- 只需要 3–5 个组合即可。
- `exchanges:` 必须引用 demo 交易所配置路径，例如：`demo/okx`、`demo/binance`（对应 `conf/exchange/demo/*.yaml`）。
- 运行 app 的筛选由 `INTEGRATION_TEST_ALLOW_APP_LISTS` 控制：
  - `INTEGRATION_TEST_ALLOW_APP_LISTS="*"`：跑全部 demo app
  - `INTEGRATION_TEST_ALLOW_APP_LISTS="okx_market_keep_positions,okx_smart_keep_positions"`：只跑指定 app（建议使用不带 `.yaml` 的 basename）

## 测试设计（核心用例）

### A. 交易所直接下单（API 级）

对每个 exchange demo，执行：

1) **环境清理**
- 取消 `SOL/USDT`（spot）与 `ETH/USDT:USDT`（swap）所有挂单
- 平掉 swap 仓位（reduceOnly）
- 将 spot SOL 余额卖出至≈0

> **注意**：现货测试使用 SOL 而非 ETH，因为 OKX Demo Trading 存在 ETH 现货相关的 bug。

2) **Market order（必须成交）**
- Spot：使用 SOL/USDT，固定数量 1 SOL，市价买入 → 等待 → 市价卖出同数量
- Swap：按当前价计算 ~100USD 的 ETH 基础币数量，市价开仓 → 等待 → 市价 `reduceOnly` 平仓

3) **Limit order（远离，默认不成交）**
- 获取当前价 `p`
- Buy limit 价 `p * 0.95`（偏离 5%）
- 等待 5–30s，验证订单状态应为 open（或未完全成交）
- 校验价格偏移不应超过预期（允许微小价格格式化误差）
- 取消该订单并确认撤单成功

4) **Limit order（靠近，仅做合理性校验）**
- Buy limit 价应 **不高于** 当前价（例如 `p * 0.99`）
- Sell limit 价应 **不低于** 当前价（例如 `p * 1.01`）
- 不强制成交，但必须验证：价格方向正确、数量非异常、订单能被查询/取消

### B. 执行器 + 策略（tick 级）

选取少量 app/demo 组合（3–5 个），对每个组合：

1) 启动最小 Listener 树（exchange/datasource/strategy/executor/app core），运行有限 duration（由INTEGRATION_TEST_DELAY_TIMEOUT决定）。
2) 通过策略目标变化触发交易（例如 keep_positions 目标从 0 → +100USD → 0）。
3) 验证：
- 触发条件时选择的执行器符合预期（market/smart/limit）
- 订单被创建（市价单必须成交；限价单检查价格/方向合理）
- 最终回到仓位≈0 且无残留挂单

## 历史记录验证（最小可行）

至少满足 ccxt 侧：

- **ccxt 侧**：market 成交后，`fetch_my_trades` 能查询到新的成交记录。
  - 允许延迟：轮询至多 `30s`（建议与 `INTEGRATION_TEST_DELAY_TIMEOUT` 对齐，取 `min(30s, INTEGRATION_TEST_DELAY_TIMEOUT)`）
  - 若超时仍不可见：默认判定失败，并输出“可能是交易所延迟/权限/market type 不匹配”的排查建议（是否改为 skip/xfail 需另行决策并审核）
- 本地侧：通过现有 Listener/Hook（如 `on_order_created/on_order_cancelled` 或订单 listener）能观测到本次订单的创建/撤销事件，并能关联到 symbol/exchange。
- 若 ClickHouse/DB 可用：验证订单/成交记录被写入（可作为 `@pytest.mark.integration_db` 扩展，不作为阻塞）。

## 验收标准

- 默认不会下单；只有当 `INTEGRATION_TEST_ALLOW_LISTS` 包含对应分组时才会下单。
- 每个 exchange demo 的 market spot + market swap 都能完成“开→平/买→卖”，最终 SOL spot ≈ 0、ETH swap ≈ 0 且无挂单。
- far limit 单：应保持 open 并可取消；价格偏移符合 5% 预期。
- near limit 单：不出现明显不合理的价格方向/数量；可查询/可取消。
- 历史记录验证至少满足 **ccxt 侧**：market 成交后 `fetch_my_trades` 在轮询超时内可见新增成交。

## TODO

- [x] 新增 demo executor/app/strategy 配置（已通过）
- [x] 集成测试分组开关：`INTEGRATION_TEST_ALLOW_LISTS`（已通过）
- [x] 分组运行时长控制：`INTEGRATION_TEST_DELAY_TIMEOUT`（已通过）
- [x] 增加 `tests/test_integration_trading.py`（基于环境变量的分组 allow-list）（已通过）
- [x] 实现"开测前/结束后"清仓与撤单（SOL spot + ETH swap）工具函数（已通过）
- [x] 增加历史记录验证（ccxt trades 或本地事件）（已通过）
- [x] Spot 支持处理：demo exchange 未开启 spot 时跳过 spot 子用例（已通过）
- [x] 文档：如何安全运行、如何跳过、如何排查失败（已通过）

## 运行指南

### 安全运行

1. **确认使用 demo 配置**：集成测试仅使用 `conf/*/demo/` 下的配置，这些配置连接测试网/Demo Trading 环境
2. **检查 `test: true`**：所有 demo 交易所配置必须设置 `test: true`，否则测试会跳过
3. **密码为 `null`**：demo 配置的加密密码统一为 `null`

### 如何跳过集成测试

- **默认跳过**：不设置 `INTEGRATION_TEST_ALLOW_LISTS` 环境变量时，所有集成测试自动跳过
- **跳过特定分组**：只在 `INTEGRATION_TEST_ALLOW_LISTS` 中列出要运行的分组
- **跳过特定 app**：设置 `INTEGRATION_TEST_ALLOW_APP_LISTS` 为特定 app 名称列表

### 排查失败

常见问题及解决方案：

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| `Exchange does not have test: true` | demo 配置缺少 test 字段 | 检查 `conf/exchange/demo/*.yaml` 中的 `test: true` |
| `InsufficientFunds` | 测试账户余额不足 | 登录交易所 Demo Trading 页面充值 |
| `fetch_my_trades` 返回空 | 交易所延迟 | 增加 `INTEGRATION_TEST_DELAY_TIMEOUT` 值 |
| 限价单价格格式错误 | 交易所精度要求 | 检查 ccxt market 的 precision 配置 |
| 现货测试失败（OKX） | OKX Demo Trading ETH 现货 bug | 已改用 SOL 现货测试 |
| 连接超时 | 网络问题 | 检查网络连接，必要时使用代理 |

### 调试技巧

```bash
# 只跑单个分组，详细输出
INTEGRATION_TEST_ALLOW_LISTS="0" pytest tests/test_integration_trading.py -v -s

# 只跑特定交易所
INTEGRATION_TEST_ALLOW_LISTS="0" pytest tests/test_integration_trading.py -v -s -k "okx"

# 增加超时时间
INTEGRATION_TEST_DELAY_TIMEOUT=60 INTEGRATION_TEST_ALLOW_LISTS="0" pytest tests/test_integration_trading.py -v -s
```
