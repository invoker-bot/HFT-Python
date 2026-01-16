# Issue 0007: Feature 0005 计算类 Indicator 长期 not ready

## 问题描述

Feature 0005 期望计算类 Indicator（如 MidPrice/MedalEdge/Volume）能够通过 `requires` 参与：
- requires ready gate（未 ready 则跳过执行）
- 条件表达式变量注入（`calculate_vars(direction)`）

但当前实现存在一个关键问题：`MidPriceIndicator / MedalEdgeIndicator / VolumeIndicator` 在 `query_indicator()` 语义下会长期处于 not ready，导致它们基本不可用于 requires/变量注入（配置写了也一直跳过）。

## 证据与定位

- `hft/indicator/base.py`：`BaseIndicator.ready_internal()` 默认要求 `len(self._data) > 0`
- `hft/indicator/computed/mid_price_indicator.py`：未维护自身 `_data`，未覆盖 `ready_internal()`
- `hft/indicator/computed/medal_edge_indicator.py`：未维护自身 `_data`，未覆盖 `ready_internal()`
- `hft/indicator/computed/volume_indicator.py`：未维护自身 `_data`，未覆盖 `ready_internal()`，且 `calculate_vars()` 输出 `notional` 变量与 Executor 内置 `notional=abs(delta_usd)` 冲突风险很高（见 `issue/0005-executor-context-var-collisions.md`）
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

- [ ] MidPriceIndicator：实现/覆盖 `ready_internal()` + requires 模式维护 `_data`（待实现）
- [ ] MedalEdgeIndicator：实现/覆盖 `ready_internal()` + requires 模式维护 `_data`（待实现）
- [ ] VolumeIndicator：实现/覆盖 `ready_internal()` + requires 模式维护 `_data`（待实现）
- [ ] VolumeIndicator：移除/重命名 `notional` 输出，避免覆盖内置 `notional`（或引入命名空间/保留字校验）（待实现）
- [ ] 单元测试：新增用例覆盖以上 3 个 indicator 的 ready 行为与 requires gate（待实现）

