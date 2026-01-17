# Issue 0003: Feature 0006 实现缺陷

> **状态**：全部通过

## 概述

Feature 0006 (Indicator & DataSource 统一架构) 实现中存在多个缺陷，需要修复。

## 任务列表

### P0 - 严重
	
- [x] 修复 IndicatorGroup 的 GlobalIndicators 被 tick 移除问题（已通过）
  - 位置：`hft/indicator/group.py:388-405`
  - 修复：`sync_children_params()` 添加 GlobalIndicators，`create_dynamic_child()` 处理静态子节点
  - P2 新增项已完成，阻塞条件已解除

- [x] 修复 TickerData.from_ccxt() 对 timestamp=None 不健壮问题（已通过）
  - 位置：`hft/indicator/datasource/ticker_datasource.py:26-44`
  - 修复：使用 `data.get("timestamp") or 0`，无值时用 `time.time()`

### P1 - 中等

- [x] 修复 BaseDataSource.on_stop() 异常处理不完整问题（已通过）
  - 位置：`hft/indicator/base.py:429-441`
  - 修复：添加 `except Exception` 捕获其他异常

- [x] 修复文档 window 格式与实现不一致问题（已通过）
  - 位置：`features/0006-indicator-datasource-unification.md:356-364`
  - 修复：将 `1d`/`1h` 改为数值秒 `86400`/`3600`

### P2 - 低

- [x] 修复 HealthyDataArray.timeout 负值问题（已通过）
  - 位置：`hft/core/healthy_data.py:339-344`
  - 修复：添加 `max(0.0, ...)` 防止交易所时间超前本机导致负值

### P2 - 低（新增）
	
- [x] IndicatorGroup.on_start() 避免重复 add_child(GlobalIndicators) 导致 class index 重复登记风险（已通过）
  - 位置：`hft/indicator/group.py:233-237`
  - 修复：已移除 `on_start()` 中的 `add_child()` 调用，只保留注释说明

### P2 - 低（新增：回归测试）
	
- [x] 添加回归测试：IndicatorGroup.tick 后 GlobalIndicators 不会被移除（已通过）
  - 位置：`tests/test_indicator_group.py::TestRegressionIssue0003`
- [x] 添加回归测试：TickerData.from_ccxt timestamp 缺失/None 时回退到 time.time() 且为 float 秒（已通过）
  - 位置：`tests/test_ticker_datasource.py::TestRegressionIssue0003`
- [x] 添加回归测试：HealthyDataArray.timeout 在 latest_timestamp 超前本机时间时不为负（已通过）
  - 位置：`tests/test_healthy_data_array.py::TestRegressionIssue0003`
- [x] 添加回归测试：BaseDataSource.on_stop 在 watch_task 异常退出时 stop 链路不抛异常（已通过）
  - 位置：`tests/test_indicator_base.py::TestRegressionIssue0003`

## 关联

- Feature: `features/0006-indicator-datasource-unification.md`
