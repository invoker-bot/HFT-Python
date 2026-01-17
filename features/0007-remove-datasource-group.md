# Feature: 移除 DataSourceGroup

> **状态**：全部通过

## 背景

Feature 0006 实现了 `IndicatorGroup` 统一架构，但旧的 `DataSourceGroup` 仍在使用。本 Feature 完成最终清理工作。

## 目标

将旧的 `DataSourceGroup` 从主链路彻底移除：AppCore/Executor/Indicator 不再依赖，也不再通过 `hft/datasource/__init__.py` 导出；旧模块本体以 `DEPRECATED` 形式短期保留作为兼容层（后续可再做 breaking 删除）。

## 当前依赖分析

### 1. AppCore

~~已移除，AppCore 不再使用 datasource_group~~

### 2. GlobalFundingRateFetcher

~~已迁移到 `hft/indicator/datasource/funding_rate_datasource.py`~~

### 3. 回退路径

~~已移除，LazyIndicator 和 avellaneda_stoikov_executor 只使用 IndicatorGroup~~

## TODO

> Phase 1：迁移 GlobalFundingRateFetcher

- [x] 创建 `GlobalFundingRateIndicator`（已通过）
- [x] 创建 `FundingRateIndicator`（已通过）
- [x] 注册 factory 到 `IndicatorGroup`（已通过）
- [x] 迁移 `FundingRatePersistListener`（已通过）
- [x] 单元测试（已通过）

> Phase 2：移除 AppCore.datasource_group

- [x] 修改 `AppCore.__init__()`（已通过）
- [x] 更新相关文档（已通过）

> Phase 3：移除回退路径

- [x] 修改 `LazyIndicator.get_datasource()`（已通过）
- [x] 修改 `avellaneda_stoikov_executor._get_datasource()`（已通过）
- [x] 移除 `DataType` 导入（已通过）

> Phase 4：删除旧模块

- [x] 在 `hft/datasource/group.py` 添加废弃标记（已通过）
- [x] 更新 `__init__.py` 导出（已通过）
- [x] 删除旧测试文件（已通过）
  - 无旧测试文件需要删除
- [x] 更新文档（已通过）
  - `docs/datasource.md` 已更新为 Indicator 统一架构

## 实现说明

### Phase 1: GlobalFundingRateFetcher 迁移

已迁移到 `hft/indicator/datasource/funding_rate_datasource.py`：

1. **GlobalFundingRateIndicator** - 全局资金费率指标
   - 继承 `GlobalIndicator[dict[str, FundingRate]]`
   - 定时调用 `medal_fetch_funding_rates()` 获取所有交易对资金费率
   - 通过 update 事件分发给 FundingRateIndicator
   - 支持持久化到 ClickHouse

2. **FundingRateIndicator** - 交易对级资金费率指标
   - 继承 `BaseIndicator[FundingRate]`
   - 事件驱动（interval=None），监听 GlobalFundingRateIndicator 的 update 事件
   - 提供 `calculate_vars()` 返回 `funding_rate`、`daily_funding_rate` 等变量

### Phase 2: AppCore.datasource_group 移除

已从 `hft/core/app/base.py` 移除 `datasource_group` 属性。
现在 AppCore 只包含：`exchange_group`、`indicator_group`、`strategy_group`、`executor`

### Phase 3: 回退路径移除

1. **LazyIndicator.get_datasource()** - 只使用 IndicatorGroup
2. **avellaneda_stoikov_executor._get_datasource()** - 只使用 IndicatorGroup
3. DataType 枚举不再被导入

### Phase 4: 旧模块废弃

`hft/datasource/group.py` 已添加 `.. deprecated::` 标记：
- `DataSourceGroup` 类
- `TradingPairDataSource` 类
- `DataType` 枚举
- `DataArray` 类

`hft/datasource/__init__.py` 已更新，不再导出废弃的类。
