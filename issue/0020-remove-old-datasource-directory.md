# Issue 0020: 移除旧的 hft/datasource 目录

> **状态**：全部通过

## 问题描述

`hft/datasource/` 目录已被标记为 deprecated，应该被 `hft/indicator/datasource/` 替代，但仍有代码在使用旧目录，导致无法安全移除。

## 当前依赖情况

### 1. MedalAmountDataSource - 只存在于旧目录
- `hft/indicator/factory.py` - 生产代码
- `tests/test_feature_0013_market_neutral.py` - 测试代码

### 2. TradeData 类型定义 - 从旧目录导入
- `hft/executor/smart_executor/executor.py`
- `hft/indicator/intensity_indicator.py`
- `hft/indicator/computed/medal_edge_indicator.py`
- `hft/indicator/computed/volume_indicator.py`

### 3. 其他 DataSource 类 - 从旧目录导入
- `hft/indicator/computed/mid_price_indicator.py` → `OrderBookDataSource`
- `hft/indicator/computed/rsi_indicator.py` → `OHLCVDataSource`
- `hft/indicator/computed/medal_edge_indicator.py` → `TradesDataSource`
- `hft/indicator/computed/volume_indicator.py` → `TradesDataSource`

## 不兼容问题

1. **架构差异**：
   - 旧 `hft/datasource/` 的类继承自旧的 `BaseDataSource`
   - 新 `hft/indicator/datasource/` 的类继承自新的 `BaseIndicator`
   - 两者接口和实现完全不同

2. **类型定义差异**：
   - 旧的 `TradeData.timestamp` 是 `int`（毫秒）
   - 新的 `TradeData.timestamp` 是 `float`（秒）

## 任务列表

- [x] 迁移 MedalAmountDataSource 到新架构（已通过）
- [x] 更新所有导入语句使用新的 datasource（已通过）
- [x] 验证所有测试通过（已通过）
- [x] 移除旧的 hft/datasource 目录（已通过）

## 实施计划

### Phase 1: 迁移 MedalAmountDataSource
1. 在 `hft/indicator/datasource/` 创建新的 `medal_amount_datasource.py`
2. 基于新的 `BaseIndicator` 架构重写
3. 更新 `hft/indicator/datasource/__init__.py` 导出

### Phase 2: 更新所有导入
1. 更新 `hft/indicator/` 下所有文件的导入语句
2. 更新 `hft/executor/` 下的导入语句
3. 更新测试文件的导入语句

### Phase 3: 验证和清理
1. 运行所有测试确保无回归
2. 删除旧的 `hft/datasource/` 目录
3. 更新相关文档

## 预期影响

- 代码库更清晰，移除 deprecated 代码
- 统一 DataSource 架构
- 减少维护负担
