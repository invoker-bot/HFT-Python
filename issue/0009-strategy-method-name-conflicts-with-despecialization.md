# Issue 0009: Strategy 方法名与"去特殊化"设计冲突

## 问题描述

`BaseStrategy.get_target_positions_usd()` 方法名暗示了 `position_usd` 是特殊字段，这与 Feature 0011 的"去特殊化"设计理念冲突。

### 设计冲突

根据 [Feature 0011](../features/0011-strategy-target-expansion.md) 的核心设计理念（第131-179行）：

> **核心理念**：`position_usd`/`speed` 等字段不再是特殊变量，而是普通的通用字典字段。

Feature 0011 的设计目标：
1. **统一性**：Strategy 和 Executor 都使用通用字典输出，没有特殊字段
2. **灵活性**：Executor 可以自由选择如何聚合多个 Strategy 的输出
3. **可扩展性**：Strategy 可以输出任意字段，不局限于 position_usd/speed

但当前的方法名 `get_target_positions_usd()` 违反了这些原则：
- 方法名强调了 `position_usd`，暗示这是特殊字段
- 与"去特殊化"的设计理念直接冲突
- 限制了 Strategy 输出的语义扩展性

### 当前状态

**方法定义**：
- `hft/strategy/base.py:276` - 抽象方法定义
- `hft/strategy/base.py:80` - 文档示例

**方法实现**（3个 Strategy 类）：
- `hft/strategy/static_positions.py:383` - StaticPositionsStrategy
- `hft/strategy/keep_balances.py:95` - KeepBalancesStrategy
- `hft/strategy/arbitrage/strategy.py:382` - ArbitrageStrategy

**方法调用**：
- `hft/strategy/group.py:147` - StrategyGroup 聚合时调用（核心调用）
- `tests/test_strategy_data_driven.py:367,398,432` - 测试调用（3处）

**文档引用**：
- `hft/strategy/base.py:7,12` - 文件头注释
- `hft/core/app/base.py:66` - AppCore 注释
- `features/0011-strategy-target-expansion.md:264` - Feature 文档

## 解决方案

### 方案 1：重命名为 `get_output()`（推荐）

**优点**：
- 完全通用，不暗示任何特殊字段
- 与 Feature 0011 的"去特殊化"理念完全一致
- 语义清晰：Strategy 输出通用字典

**缺点**：
- 方法名较短，可能不够描述性

### 方案 2：重命名为 `get_targets()`

**优点**：
- 保留了 "targets" 语义，与配置中的 `targets` 字段对应
- 比 `get_output()` 更具描述性

**缺点**：
- 仍然暗示了 "target positions" 的概念
- 不如 `get_output()` 通用

### 方案 3：重命名为 `calculate()` 或 `compute()`

**优点**：
- 强调"计算"语义，符合 Strategy 的职责
- 完全通用

**缺点**：
- 过于抽象，不够描述性
- 不清楚计算的是什么

### 推荐方案

**采用方案 1：`get_output()`**

理由：
1. 完全符合"去特殊化"设计理念
2. 与 `StrategyOutput` 类型名称一致
3. 语义清晰：Strategy 的输出是通用字典
4. 为未来扩展留有空间（Strategy 可以输出任意字段）

## 影响范围

### 核心模块

| 文件 | 影响 | 说明 |
|------|------|------|
| `hft/strategy/base.py` | **重大** | 抽象方法定义 + 文档 |
| `hft/strategy/static_positions.py` | **重大** | 实现方法 |
| `hft/strategy/keep_balances.py` | **重大** | 实现方法 |
| `hft/strategy/arbitrage/strategy.py` | **重大** | 实现方法 |
| `hft/strategy/group.py` | **重大** | 调用方法 |

### 测试文件

| 文件 | 影响 | 说明 |
|------|------|------|
| `tests/test_strategy_data_driven.py` | **中等** | 3处调用 |

### 文档

| 文件 | 影响 | 说明 |
|------|------|------|
| `features/0011-strategy-target-expansion.md` | **小** | 1处引用 |
| `hft/core/app/base.py` | **小** | 注释引用 |

## 任务列表

### Phase 1: 重命名方法（P0）

- [ ] 重命名 `BaseStrategy.get_target_positions_usd()` → `get_output()`（待实现）
- [ ] 更新 `BaseStrategy` 文档和注释（待实现）
- [ ] 更新 `StaticPositionsStrategy.get_target_positions_usd()` → `get_output()`（待实现）
- [ ] 更新 `KeepBalancesStrategy.get_target_positions_usd()` → `get_output()`（待实现）
- [ ] 更新 `ArbitrageStrategy.get_target_positions_usd()` → `get_output()`（待实现）
- [ ] 更新 `StrategyGroup` 调用方（待实现）

### Phase 2: 更新测试（P1）

- [ ] 更新 `tests/test_strategy_data_driven.py` 中的调用（待实现）
- [ ] 运行所有测试，确保通过（待实现）

### Phase 3: 更新文档（P2）

- [ ] 更新 `features/0011-strategy-target-expansion.md` 引用（待实现）
- [ ] 更新 `hft/core/app/base.py` 注释（待实现）
- [ ] 更新 `hft/strategy/base.py` 文件头注释（待实现）
- [ ] 检查其他文档中的引用（待实现）

### Phase 4: 类型定义清理（P2）

- [ ] 考虑是否废弃 `TargetPositions` 类型（待实现）
- [ ] 统一使用 `StrategyOutput` 类型（待实现）
- [ ] 更新类型注解和文档（待实现）

## 相关文档

- [Feature 0011: Strategy Target 展开式与去特殊化](../features/0011-strategy-target-expansion.md)
- [Feature 0008: Strategy 数据驱动](../features/0008-strategy-data-driven.md)
- [docs/strategy.md](../docs/strategy.md)

## 备注

这是一个重大的重构任务，但对于保持设计一致性和可扩展性至关重要。建议在完成 Feature 0011 的所有其他任务后，再进行此重构。
