# Issue 0007: Feature 0005 计算类 Indicator 长期 not ready

> **状态**：全部通过

## 问题描述

Feature 0005 期望计算类 Indicator（如 MidPrice/MedalEdge/Volume）能够通过 `requires` 参与：
- requires ready gate（未 ready 则跳过执行）
- 条件表达式变量注入（`calculate_vars(direction)`）

该问题已修复：`MidPriceIndicator / MedalEdgeIndicator / VolumeIndicator` 已实现 requires 模式 `_data` 维护与 `ready_internal()`，并提供 lazy 缓存，能够在 `query_indicator()` 语义下正常参与 requires/变量注入。

## 证据与定位（历史问题）

- `hft/indicator/base.py`：`BaseIndicator.ready_internal()` 默认要求 `len(self._data) > 0`
- `hft/indicator/computed/mid_price_indicator.py`：当时未维护自身 `_data`，未覆盖 `ready_internal()`
- `hft/indicator/computed/medal_edge_indicator.py`：当时未维护自身 `_data`，未覆盖 `ready_internal()`
- `hft/indicator/computed/volume_indicator.py`：当时未维护自身 `_data`，未覆盖 `ready_internal()`，且输出 `notional` 与 Executor 内置 `notional=abs(delta_usd)` 有冲突风险（见 `issue/0005-executor-context-var-collisions.md`）
- `hft/indicator/group.py`：`query_indicator()` 仅在 `indicator.is_ready()` 为 True 时返回实例，否则返回 None

结果：Executor 的 `requires` 在这些计算类 indicator 上会被 ready gate 持续拦住（或变量永远注入不到 context）。

## 影响

- `conf/executor/**` 一旦写 `requires: [mid_price/medal_edge/volume]`，通常会导致该执行器对该 symbol 永远不执行（ready gate 永远 False）
- 文档/示例容易误导：看起来变量可用，但实际表达式里变量永远缺失（fail-safe 为 False/None）
- `VolumeIndicator.notional` 覆盖内置 `notional` 时，会造成 SmartExecutor `target_notional` 语义被污染（详见 `issue/0005-executor-context-var-collisions.md`）

## 期望行为（与 Feature 0006 口径一致）

- 计算类 indicator 必须明确 ready 语义：
  - 至少覆盖 `ready_internal()`，使其在依赖数据源 ready 时可 ready（即使自身 `_data` 还未写入）
  - 在被 requires 依赖时，应维护自身 `_data`（用于 `timeout/cv/range` 与健康口径）
- 计算类 indicator 的输出变量名需避免覆盖内置保留名（尤其是 `notional/target_notional/trades_notional`）

## TODO

- [x] MidPriceIndicator：实现/覆盖 `ready_internal()` + requires 模式维护 `_data`（已通过）
- [x] MedalEdgeIndicator：实现/覆盖 `ready_internal()` + requires 模式维护 `_data`（已通过）
- [x] VolumeIndicator：实现/覆盖 `ready_internal()` + requires 模式维护 `_data`（已通过）
- [x] VolumeIndicator：移除/重命名 `notional` 输出，避免覆盖内置 `notional`（或引入命名空间/保留字校验）（已通过）
- [x] 单元测试：新增用例覆盖以上 3 个 indicator 的 ready 行为与 requires gate（已通过）

## 审核结论

结论：审核通过。

验收依据：
- `hft/indicator/computed/mid_price_indicator.py`、`hft/indicator/computed/medal_edge_indicator.py`、`hft/indicator/computed/volume_indicator.py`：已实现 requires 模式 `_data` 更新 + `ready_internal()` + lazy cache
- `tests/test_executor_dynamic_conditions.py`：`TestComputedIndicatorsReadyState` 覆盖 ready_internal、输出变量名、direction 语义与 lazy cache 的基本行为

## 实现说明

### 1. MidPriceIndicator

- 添加 `MidPriceData` 数据类，包含 `mid_price`、`best_bid`、`best_ask`、`spread`、`timestamp`
- 实现 `on_tick()` 方法，被 requires 时定期计算并缓存到 `_data`
- 实现 `ready_internal()` 方法，被 requires 时检查 `_data` 是否有数据
- 实现 lazy 缓存机制，未被 requires 时按需计算

### 2. MedalEdgeIndicator

- 添加 `MedalEdgeData` 数据类，包含 `medal_edge`、`buy_edge`、`sell_edge`、`timestamp`
- 实现 `on_tick()` 方法，被 requires 时定期计算并缓存到 `_data`
- 实现 `ready_internal()` 方法，被 requires 时检查 `_data` 是否有数据
- 实现 lazy 缓存机制，未被 requires 时按需计算
- `calculate_vars()` 根据 direction 选择 buy_edge 或 sell_edge

### 3. VolumeIndicator

- 添加 `VolumeData` 数据类，包含 `volume`、`buy_volume`、`sell_volume`、`volume_notional`、`buy_volume_notional`、`sell_volume_notional`、`timestamp`
- 实现 `on_tick()` 方法，被 requires 时定期计算并缓存到 `_data`
- 实现 `ready_internal()` 方法，被 requires 时检查 `_data` 是否有数据
- 实现 lazy 缓存机制，未被 requires 时按需计算
- **变量重命名**：`notional` → `volume_notional`，避免与 Executor 内置 `notional` 冲突（Issue 0005）

### 4. 测试用例

新增 `TestComputedIndicatorsReadyState` 测试类，包含：
- `test_volume_indicator_ready_internal_not_required`
- `test_volume_indicator_ready_internal_when_required`
- `test_volume_indicator_calculate_vars_output`
- `test_medal_edge_indicator_ready_internal_when_required`
- `test_medal_edge_indicator_calculate_vars_direction`
- `test_mid_price_indicator_ready_internal_when_required`
- `test_mid_price_indicator_calculate_vars_output`
- `test_computed_indicator_lazy_cache`
- `test_is_required_property`
