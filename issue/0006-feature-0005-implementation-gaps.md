# Issue 0006: Feature 0005 实现缺陷

## 问题描述

Feature 0005（Executor 动态条件与变量注入）的部分任务虽然标记为"待审核"，但实际上存在实现不完整或缺失的问题。

## 问题列表

### 1. requires ready gate 实现不明确（待实现）

**状态**：代码已实现，但文档与测试不完整

**问题**：
- `hft/executor/base.py#L582` 的 `check_requires_ready()` 方法已实现
- 在 `_process_single_target()` 的第 864 行已被调用
- 但是当 `condition=None` 时，`collect_context_vars()` 不会被调用
- 用户可能误认为 requires ready gate 没有工作

**需要**：
- ✅ 代码已实现（第 582-609 行 `check_requires_ready()`，第 864 行调用）
- ❌ 需要明确文档说明：即使 `condition=None`，ready gate 仍然生效
- ❌ 需要增加测试用例：验证 `condition=None` 时 ready gate 仍然工作

**验收标准**：
1. 所有执行器（MarketExecutor、LimitExecutor、SmartExecutor）都正确调用 `check_requires_ready()`
2. 当任一 requires indicator 未 ready 时，跳过执行（返回 None）
3. 测试覆盖：condition=None 但 requires 有值的场景

### 2. 计算类 Indicator 缺少 ready_internal() 实现（待实现）

**状态**：未实现

**问题**：
- 计算类 Indicator（RSIIndicator、MedalEdgeIndicator等）没有维护自身的 `_data`
- 没有实现/覆盖 `ready_internal()` 方法
- 当前实现只在 `calculate_vars()` 中检查依赖的数据源是否 ready
- 这导致 `is_ready()` 可能返回不正确的结果（因为没有 `_data`，`ready_internal()` 总是返回 True）

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

### 3. ready_condition 配置加载机制缺失（待实现）

**状态**：未实现

**问题**：
- `AppCore._register_indicator_factories()` 只把 `params` 传给 `IndicatorFactory`
- 如果 `ready_condition` 放在 `params` 外，会被忽略
- 当前所有 DataSource 都把 `ready_condition` 作为构造参数，放在 `params` 中
- Feature 0005 要求 `ready_condition` 应该通过 `set_ready_condition()` 单独注入，不放入 `params`

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

### 4. Feature 0005 文档状态不一致（待更新）

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

## 优先级（更新）

| 问题 | 优先级 | 原因 |
|------|--------|------|
| 问题 1.1 | **P0** | condition=None 时不调用 collect_context_vars() 是 BUG |
| 问题 1.2 | P2 | SmartExecutor 缺少 check_requires_ready() 调用 |
| 问题 2 | **P0** | 计算类 Indicator 需要实现被依赖时的定期更新 |
| 问题 3 | P1 | ready_condition 配置加载机制 |
| 问题 4 | P2 | 文档更新 |

## 任务列表

### Phase 1: 修复关键 BUG（P0）

- [x] 修复 BaseExecutor._process_single_target()：condition=None 时也要调用 collect_context_vars()（审核完成）
- [x] BaseIndicator 添加 set_requires_flag() 方法，标记是否被依赖（审核完成）
- [x] RSIIndicator：实现 requires 模式 on_tick() 定期更新 + `_data` 维护 + `ready_internal()`（审核完成）
- [ ] MidPrice/MedalEdge/Volume：补齐 requires 模式 `_data` 维护与 `ready_internal()`（待实现；见 `issue/0007-feature-0005-computed-indicators-not-ready.md`）

### Phase 2: 配置机制（P1）

- [x] BaseIndicator 添加 set_ready_condition() 方法（审核完成）
- [x] AppCore._register_indicator_factories() 支持 ready_condition 单独字段（审核完成）
- [ ] 迁移现有配置：将 ready_condition 从 params 中分离（待实现）

### Phase 3: SmartExecutor 和测试（P2）

- [x] SmartExecutor.execute_delta() 添加 check_requires_ready() 调用（审核完成）
- [x] 已有测试：condition=None 但 requires 有值的场景（37 tests passed）
- [ ] 添加测试：计算类 Indicator 的 ready 状态（待实现）

### Phase 4: 文档更新（P2）

- [x] 更新 Feature 0005 文档状态（审核完成）
- [ ] 更新 docs/indicator.md 关于 ready 语义（待实现）

## 实现总结

### 已完成

1. **BaseExecutor._process_single_target()** - 修复了 condition=None 时不调用 collect_context_vars() 的 BUG
2. **BaseIndicator** - 添加了 `_is_required` 标记、`set_requires_flag()`、`set_ready_condition()`、`ready_internal()` 方法
3. **BaseExecutor._get_indicator()** - 自动标记获取的 Indicator 为 required
4. **RSIIndicator** - 实现了 requires 模式的 on_tick() 定期更新、ready_internal()、lazy 缓存
5. **IndicatorFactory** - 支持 ready_condition 参数，创建后调用 set_ready_condition()
6. **AppCore._register_indicator_factories()** - 支持从配置中读取 ready_condition 单独字段
7. **SmartExecutor.execute_delta()** - 添加了 check_requires_ready() 调用

### 待完成

1. **配置迁移** - 将现有 DataSource 配置中的 ready_condition 从 params 中分离（可选，向后兼容）
2. **计算类 Indicator** - MidPrice/MedalEdge/Volume 的 ready 语义与 `_data` 维护（见 `issue/0007-feature-0005-computed-indicators-not-ready.md`）
3. **测试用例** - 添加计算类 Indicator 的 ready 状态测试
4. **文档更新** - docs/indicator.md 关于 ready 语义
