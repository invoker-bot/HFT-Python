# Issue 0004: SmartExecutor routes 的 notional 语义与实现不一致（导致路由失效）

> **状态**：全部通过

## 概述

当前 `SmartExecutor` 的 routes 条件表达式中，变量 `notional` 的语义在「设计/测试期望」与「实际实现」之间不一致，导致路由规则无法按预期命中，进而出现执行器选择错误。

本 issue 以 `pytest` 失败用例为证据，要求明确 `notional` 的定义，并统一：代码（路由上下文）、文档（features/docs/config 注释）、测试（期望）。

## 复现

执行：

```bash
pytest -q
```

当前失败用例（至少）：
- `tests/test_smart_executor_phase3.py::test_route_matching_with_notional_condition`
- `tests/test_smart_executor_phase3.py::test_route_matching_with_complex_condition`
- `tests/test_smart_executor_phase3.py::test_failsafe_edge_and_notional_default_values`
- `tests/test_smart_executor_phase3.py::test_notional_calculation_sell_side`
- `tests/test_smart_executor_phase4.py::TestEdgeCases::test_negative_delta_usd_sell_direction`

## 期望 vs 实际

**期望（从测试与路由表达式语义推断）**：
- routes 中的 `notional` 表示“该方向 trades 的成交额（USD）”，例如 `sum(trade.cost for trade in trades if side matches)`。
- 因此当 buy/sell 方向 trades_notional 很大时（例如 > 10000），应命中 `notional > 10000` 并选择 `market`。

**实际（当前实现）**：
- 路由上下文中的 `notional` 来自 `BaseExecutor.collect_context_vars(... notional=abs(delta_usd))`，即“目标仓位差额的 USD 绝对值”，与 trades 成交额无关。
- `SmartExecutor` 虽然额外计算了 `trades_notional`，但 routes 里默认使用的是 `notional`，导致条件无法命中，最终走默认执行器分支，测试失败。

## 根因分析（待确认但高度可疑）

- `SmartExecutor._get_route_context()` 构建上下文时将 `notional` 绑定为 `abs(delta_usd)`（目标仓位差额）。
- 同时又单独计算 `trades_notional`（方向成交额）并放入上下文，但 routes/测试使用的是 `notional`。
- 这形成了“同名变量两套语义”的冲突：一套来自 BaseExecutor（目标 notional），一套来自 SmartExecutor 设计（成交额 notional）。

## 修复方向（需二选一或提供兼容期）

建议明确两类 notional，并避免同名歧义：

1. **推荐（清晰）**：在 SmartExecutor route context 中提供两个变量并在文档中强制区分
   - `target_notional`: `abs(delta_usd)`（目标差额 USD）
   - `trades_notional`: 方向成交额（USD）
   - routes 条件若要用成交额，使用 `trades_notional`；不要再用 `notional` 表示成交额

2. **兼容旧写法**：将 routes 的 `notional` 定义为 `trades_notional`（成交额），并把 BaseExecutor 的 `notional` 改名为 `target_notional`（或在 SmartExecutor 上下文中覆盖 `notional`）
   - 风险：可能影响其他 executor/condition 的既有语义；需要评估范围与迁移成本

无论选择哪种方案，都需要同步更新：
- 文档（features/docs/config 注释）
- 配置验证/可用变量列表（避免误导）
- 测试用例（作为回归锁）

## 任务列表

### P0 - 严重

- [x] 明确 SmartExecutor routes 的 `notional` 定义（成交额 vs 目标差额），并给出最终裁决（已通过）
  - 裁决：采用方案 2（兼容旧写法），routes 中 `notional` = 成交额（trades_notional），新增 `target_notional` = 目标差额
- [x] 统一实现：调整 SmartExecutor 路由上下文变量命名与内容（已通过）
  - 位置：`hft/executor/smart_executor/executor.py:500-512`
  - 修复：`_get_route_context()` 中将 `notional` 覆盖为 `trades_notional`，保留 `target_notional`
- [x] 统一测试：修复/更新上述失败用例，使其与最终语义一致（已通过）
  - 测试结果：`pytest -q tests/test_smart_executor_phase3.py tests/test_smart_executor_phase4.py` 33 passed
- [x] 统一文档：更新相关 feature 文档与配置注释，避免用户误用（已通过）
  - 位置：`hft/executor/smart_executor/config.py:34-38`
  - 修复：RouteConfig docstring 中明确 `notional` 和 `target_notional` 的区别

### P1 - 中等

- [x] 补充/调整路由配置校验：对 `notional`/`trades_notional`/`target_notional` 的可用变量与错误信息做一致化（已通过）
  - 位置：`hft/executor/smart_executor/executor.py:274-291`
  - 修复：`_validate_routes()` 中的 `available_vars` 已包含所有三个变量
- [x] 增加迁移说明：旧配置如使用 `notional` 的含义变更时，给出替换建议（已通过）
  - 说明：旧配置中 `notional` 原本指目标差额，现改为成交额；如需目标差额请使用 `target_notional`
