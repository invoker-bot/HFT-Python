# Issue 0006: Feature 0005 实现缺陷

> **状态**：全部通过

## 问题描述

Feature 0005（Executor 动态条件与变量注入）的部分任务虽然标记为"待审核"，但实际上存在实现不完整或缺失的问题。

## 问题列表

### 1. requires ready gate 实现不明确（已修复）

**状态**：已实现并补齐测试（已通过）

**问题（历史）**：
- `hft/executor/base.py` 的 `check_requires_ready()` 已实现并在 `_process_single_target()` 中调用
- 历史上存在 `condition=None` 时不调用 `collect_context_vars()` 的行为不一致问题，已修复：现在无论 `condition` 是否为 None 都会先 collect context 并做 gate/求值

**需要**：
- ✅ 代码已实现（`check_requires_ready()` + `_process_single_target()` 接入）
- ✅ 已补充测试：覆盖 `condition=None` 且 requires 有值时的 gate 行为

**验收标准**：
1. 所有执行器（MarketExecutor、LimitExecutor、SmartExecutor）都正确调用 `check_requires_ready()`
2. 当任一 requires indicator 未 ready 时，跳过执行（返回 None）
3. 测试覆盖：condition=None 但 requires 有值的场景

### 2. 计算类 Indicator 缺少 ready_internal() 实现（已修复）

**状态**：已实现（已通过）

**问题（历史）**：
- 计算类 Indicator（RSI/MidPrice/MedalEdge/Volume）曾缺少 `_data` 维护与 `ready_internal()`，导致在 `query_indicator()` 语义下长期 not ready
- 该问题已修复：上述 indicator 已实现 requires 模式 `_data` 更新、`ready_internal()` 与 lazy cache（详见 `issue/0007-feature-0005-computed-indicators-not-ready.md`）

**需要**：
1. 计算类 Indicator 应该维护自身的 `_data: HealthyDataArray`
2. 在 `on_tick()` 或类似方法中定期更新 `_data`
3. 实现 `ready_internal()` 方法，至少检查"是否有至少 1 个可用数据点"
4. 可选：更严格的 ready 条件（如 RSI 需要至少 period+1 个数据点）

**影响**：
- 当前 requires ready gate 对计算类 Indicator 可能无效
- `indicator.is_ready()` 可能返回 True，但实际上数据不足

**示例**：
```python
class RSIIndicator(BaseIndicator[float]):
    def __init__(self, ..., **kwargs):
        super().__init__(..., window=100, **kwargs)  # 维护最近 100 个 RSI 值
        self._period = period

    async def on_tick(self) -> bool:
        """定期计算 RSI 并更新 _data"""
        ohlcv = self._get_ohlcv_indicator()
        if ohlcv and ohlcv.is_ready():
            closes = [c.close for c in ohlcv._data]
            rsi = self._calculate_rsi(closes)
            self._data.append(rsi, timestamp=time.time())
        return False

    def ready_internal(self) -> bool:
        """覆盖默认实现，要求至少有 1 个 RSI 值"""
        return len(self._data) > 0  # 或者更严格：len(self._data) >= self._period
```

### 3. ready_condition 配置加载机制缺失（已修复）

**状态**：已实现（已通过）

**问题（历史）**：
- 历史上 `ready_condition` 若放在 `params` 外会被忽略
- 当前已支持 `ready_condition` 单独字段：由 `IndicatorFactory` 创建实例后调用 `set_ready_condition()` 注入

**需要**：
1. 在 `BaseIndicator` 中添加 `set_ready_condition(condition: str)` 方法
2. 修改 `AppCore._register_indicator_factories()` 以支持：
   ```yaml
   indicators:
     trades:
       class: TradesDataSource
       ready_condition: "timeout < 60 and cv < 0.8"
       params:
         window: 300.0
   ```
3. 配置加载时，先创建 Indicator，再调用 `set_ready_condition()`

**原因**：
- `ready_condition` 是运行时配置，不是 Indicator 的构造参数
- 不同环境可能需要不同的 ready_condition（测试 vs 生产）
- 避免 `ready_condition` 污染 Indicator 的构造参数列表

### 4. Feature 0005 文档状态不一致（已修复）

**问题**：
- 第 616 行任务标记为"待审核"，但代码已实现
- 第 628 行任务标记为"待审核"，但文档已更新
- 缺少对问题 1、2、3 的明确说明

**需要**：
- 更新 Feature 0005 TODO 列表，明确标注每个任务的真实状态
- 添加验收标准的详细说明
- 标注哪些是"代码已实现但测试/文档不完整"，哪些是"完全未实现"

## 设计决策（已澄清）

### 1. 计算类 Indicator 的 ready 语义

**决策**：混合模式 - 被 requires 的定期更新，否则 lazy 计算

- **被 requires 的 Indicator**：
  - 在 `on_tick()` 中定期计算并缓存到 `_data`
  - `ready_internal()` 检查 `_data` 是否有足够数据点
  - 由 IndicatorGroup 在创建时标记为"被依赖"

- **未被 requires 的 Indicator**：
  - `calculate_vars()` 按需计算（lazy）
  - 如果缓存失效，重新计算
  - 只有被 requires 的才会定期更新

### 2. ready_condition 配置方式

**决策**：分离配置 - params 外单独字段

```yaml
indicators:
  trades:
    class: TradesDataSource
    ready_condition: "timeout < 60 and cv < 0.8"  # 单独字段
    params:
      window: 300.0  # 构造参数
```

- AppCore 先创建 Indicator，再调用 `set_ready_condition()`
- 需要在 BaseIndicator 中添加 `set_ready_condition()` 方法

### 3. condition=None 时的行为

**决策**：总是调用 collect_context_vars()

- `condition=None` 相当于 `condition=True`（无条件执行）
- 应该调用 `collect_context_vars()` 以保证行为一致
- **当前实现有 BUG**：`condition=None` 时跳过了 `collect_context_vars()`

### 4. SmartExecutor 的 requires ready gate

**决策**：需要在 execute_delta() 中调用 check_requires_ready()

- 所有 BaseExecutor 行为应该一致
- SmartExecutor 可能有自己的 requires
- 即使委托给子 executor，也应该先检查外层的 requires

## 优先级（历史记录，已处理）

| 问题 | 优先级 | 原因 |
|------|--------|------|
| 问题 1.1 | **P0** | condition=None 时不调用 collect_context_vars()（已修复） |
| 问题 1.2 | P2 | SmartExecutor 缺少 check_requires_ready() 调用（已修复） |
| 问题 2 | **P0** | 计算类 Indicator 被 requires 时需定期更新（已修复） |
| 问题 3 | P1 | ready_condition 配置加载机制（已修复） |
| 问题 4 | P2 | 文档状态/说明同步（已修复） |

## 任务列表

### Phase 1: 修复关键 BUG（P0）

- [x] 修复 BaseExecutor._process_single_target()：condition=None 时也要调用 collect_context_vars()（已通过）
- [x] BaseIndicator 添加 set_requires_flag() 方法，标记是否被依赖（已通过）
- [x] RSIIndicator：实现 requires 模式 on_tick() 定期更新 + `_data` 维护 + `ready_internal()`（已通过）
- [x] MidPrice/MedalEdge/Volume：补齐 requires 模式 `_data` 维护与 `ready_internal()`（已通过）

### Phase 2: 配置机制（P1）

- [x] BaseIndicator 添加 set_ready_condition() 方法（已通过）
- [x] AppCore._register_indicator_factories() 支持 ready_condition 单独字段（已通过）
- [x] 迁移现有配置：将 ready_condition 从 params 中分离（已通过：`conf/` 内未发现任何 `ready_condition`，无需迁移）

### Phase 3: SmartExecutor 和测试（P2）

- [x] SmartExecutor.execute_delta() 添加 check_requires_ready() 调用（已通过）
- [x] 已有测试：condition=None 但 requires 有值的场景（已通过）
- [x] 添加测试：计算类 Indicator 的 ready 状态（已通过）

### Phase 4: 文档更新（P2）

- [x] 更新 Feature 0005 文档状态（已通过）
- [x] 更新 docs/indicator.md 关于 ready 语义（已通过：已补充 Ready 语义/ready_condition/requires gate/ready_internal 说明）

## 实现总结

### 已完成

1. **BaseExecutor._process_single_target()** - 修复了 condition=None 时不调用 collect_context_vars() 的 BUG
2. **BaseIndicator** - 添加了 `_is_required` 标记、`set_requires_flag()`、`set_ready_condition()`、`ready_internal()` 方法
3. **BaseExecutor._get_indicator()** - 自动标记获取的 Indicator 为 required
4. **RSIIndicator** - 实现了 requires 模式的 on_tick() 定期更新、ready_internal()、lazy 缓存
5. **IndicatorFactory** - 支持 ready_condition 参数，创建后调用 set_ready_condition()
6. **AppCore._register_indicator_factories()** - 支持从配置中读取 ready_condition 单独字段
7. **SmartExecutor.execute_delta()** - 添加了 check_requires_ready() 调用
8. **MidPriceIndicator** - 实现了 requires 模式的 `on_tick()` 定期更新、`ready_internal()`、lazy 缓存（已通过）
9. **MedalEdgeIndicator** - 实现了 requires 模式的 `on_tick()` 定期更新、`ready_internal()`、lazy 缓存（已通过）
10. **VolumeIndicator** - 实现了 requires 模式的 `on_tick()` 定期更新、`ready_internal()`、lazy 缓存（已通过）
11. **测试用例** - 添加了 `TestComputedIndicatorsReadyState` 测试类，覆盖计算类 Indicator 的 ready 状态（已通过）

### 待完成

无。所有任务已完成（已通过）。

## 审核结论

结论：本 issue 中所有任务已完成（已通过）。

验收依据：
- `hft/executor/base.py`：requires ready gate 在 `_process_single_target()` 中生效，且 `condition=None` 时仍会 collect context 并求值
- `hft/indicator/base.py`：`set_ready_condition()` + `ready_internal()` + `is_ready()` 组合语义
- `hft/indicator/computed/mid_price_indicator.py`、`hft/indicator/computed/medal_edge_indicator.py`、`hft/indicator/computed/volume_indicator.py`：requires 模式 `_data` 维护与 `ready_internal()`
- `docs/indicator.md`：新增 Ready 语义章节，包含 ready_condition 配置、ready_internal() 实现、requires ready gate 说明
- `tests/test_executor_dynamic_conditions.py` / `tests/test_executor_vars_system.py` / `tests/test_strategy_data_driven.py`：本地运行通过（`pytest -q ...`）
