# Feature 0009: GridExecutor 网格交易执行器（已废弃）

> **状态**：全部通过
>
> **废弃说明**：本仓库当前不提供 GridExecutor（未引入/已移除）。
> 网格挂单建议使用 `LimitExecutor` 的 `orders` 显式列表实现（见示例与文档）。
>
> **推荐替代方案**：使用 `LimitExecutor`（Feature 0005 动态表达式 + `orders` 多层配置）。
> 参考：`examples/001-stablecoin-market-making.md`、`examples/002-executor-configurations.md`、`features/0010-executor-vars-system.md`。

## 概述

~~新增 GridExecutor，专门用于网格交易策略，简化多档限价单的配置。~~

**已废弃**：使用 LimitExecutor 的 `orders` 多层配置替代。

## 动机

当前使用 LimitExecutor 的 `orders` 数组模拟网格需要手动配置每一档，配置冗长且容易出错。

## 替代方案

### 旧设计（GridExecutor - 已废弃，仅示意）

```yaml
class_name: grid

levels: 3
spread: '0.0002 * mid_price'
center_price: 'mid_price'
per_level_usd: '100 - q * direction * 50'
timeout: 604800
refresh_duration: 604800
refresh_tolerance: 1.0
```

### 新设计（LimitExecutor + orders 显式列表）

```yaml
class_name: limit

requires:
  - ticker

vars:
  - name: q
    value: 'clip((current_position_usd - position_usd) / max_position_usd, -1, 1)'

# 使用 orders 数组模拟网格（每侧 3 档）；每层的字段支持表达式
orders:
  - spread: '0.0002 * mid_price'
    per_order_usd: '100 - q * 50'
    timeout: 604800
    refresh_tolerance: 1.0
  - spread: '0.0004 * mid_price'
    per_order_usd: '100 - q * 50'
    timeout: 604800
    refresh_tolerance: 1.0
  - spread: '0.0006 * mid_price'
    per_order_usd: '100 - q * 50'
    timeout: 604800
    refresh_tolerance: 1.0
```

### 优势对比

| 特性 | GridExecutor（旧） | LimitExecutor + orders（新） |
|------|-------------------|----------------------------------|
| 配置复杂度 | 简单 | 稍复杂 |
| 灵活性 | 低 | 高（支持 vars/表达式 + 多层 orders） |
| 统一性 | 专用 Executor | 复用统一的 BaseExecutor 订单管理 |
| 维护成本 | 额外维护 | 无额外成本 |

## 任务列表

### Phase 1: 废弃 GridExecutor（P2）

- [x] 标记 GridExecutor 为废弃（已通过）
- [x] 迁移现有配置到 LimitExecutor（已通过）
- [x] 更新文档和示例（已通过）

## 与现有 Feature 的关系

| Feature | 关系 |
|---------|------|
| Feature 0010 | 替代 GridExecutor，使用统一 order 配置 |

## 示例

参考 `examples/001-stablecoin-market-making.md` 方案一的网格交易配置（已更新为 LimitExecutor）。
参考 `examples/002-executor-configurations.md` 多层挂单示例。
