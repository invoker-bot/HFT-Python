# Issue 0010: Indicator `window: null` 语义与实现不一致（需要 normalize None -> 0）

> **状态**：全部通过

## 问题描述

Feature 0006 已明确：`window: null` 语义等价 `window: 0`（不保留历史窗口，仅保留最新点；ready_condition 中 `cv/range` 走默认值）。

但当前实现中：
- `BaseIndicator` 将 `window` 直接传给 `HealthyDataArray(max_seconds=window)`（`hft/indicator/base.py:65`），未对 `None` 做归一化
- `BaseIndicator.is_ready()` 会比较 `self._window > 0`（`hft/indicator/base.py:263`）

因此一旦 `window` 来自 YAML 的 `null`（即 Python `None`），会在运行期触发 `TypeError`。

## 复现

1. 任意 indicator 配置写 `window: null`（YAML 会解析为 `None`）
2. 触发该 indicator 的 `is_ready()`（在已有数据点时）或 `_data.append()` 写入/清理逻辑

常见报错（示例）：
- `TypeError: '>' not supported between instances of 'NoneType' and 'int'`（来自 `self._window > 0`）
- `TypeError: unsupported operand type(s) for -: 'float' and 'NoneType'`（来自 `latest_ts - self._max_seconds`）

## 影响

- 任何使用 `window: null` 的 indicator 都可能在运行期崩溃（尤其是 ready 逻辑、append shrink 逻辑）
- 阻塞“单值 indicator”的通用配置表达（例如 fair_price 这类只关心最新值的指标）
- Feature 0013 的 FairPriceIndicator/MarketNeutralPositions 相关链路会被放大影响

## 期望行为（与 Feature 0006 一致）

- `window: null` 与 `window: 0` 等价
- 存储语义：仅保留最新点（或最新 timestamp 的点集合），行为类似 `HealthyData` 单值缓存
- ready_condition 语义：`cv=0.0`、`range=1.0`，仅由 `timeout` 与 `ready_internal()` 决定

## 修复建议

- `BaseIndicator.__init__` 接受 `window: Optional[float]`
- 构造阶段归一化：`window = 0.0 if window is None else float(window)`
- 保证 `_data = HealthyDataArray(max_seconds=window)` 的 `max_seconds` 始终为数值
- 补单测覆盖：`window=None` 与 `window=0` 的等价性（is_ready 分支、append/shrink 行为）

## TODO

- [x] 修复：`BaseIndicator` 支持 `window: null`（None -> 0 归一化）（已通过）
- [x] 测试：新增单元测试覆盖 `window=None` 与 `window=0` 的等价性（已通过）
- [x] 回归：确认 `FairPriceIndicator` 等"单值 indicator"不再因 window 触发 TypeError（已通过）

