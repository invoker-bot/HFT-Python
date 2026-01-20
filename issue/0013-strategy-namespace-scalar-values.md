# Issue 0013: 单策略口径收敛为标量（strategies namespace 不再返回 list）

> **状态**：全部通过

## 背景

当前文档中为兼容"未来多策略聚合"，约定 Executor 注入的 `strategies` namespace 为 list 口径（即便当前只支持单策略，也会是长度为 1 的列表）。

但在"单策略为主"的现实使用场景下，这会导致：
- 表达式处处要写 `sum(strategies["x"])`/`avg(...)`，冗余且易误用
- 文档示例容易暗示"已有多策略"，与实现现状（仅单策略）产生语义噪音

## 目标（新口径）

- 当前"单策略"场景下：`strategies["field"]` **直接是标量值**（而不是 `[value]`）。
- 文档示例统一按标量口径书写（避免 `sum([...])` 这类无意义聚合）。
- 若未来重新引入"多策略"能力：需要另行设计"聚合变量命名/结构"，避免把单策略口径再次变成列表。

## 实现完成

### 代码变更

- `hft/strategy/group.py`:
  - `get_aggregated_targets()` 不再聚合为列表，直接返回标量字典
  - 移除 `defaultdict` 导入

- `hft/executor/base.py`:
  - `_process_single_target()` 参数类型从 `dict[str, list[Any]]` 改为 `dict[str, Any]`
  - 移除 `sum(position_list)` 聚合逻辑，直接使用标量值
  - 更新 `collect_context_vars()` 类型注解和文档
  - 更新文件顶部 docstring

- `tests/test_executor_vars_system.py`:
  - 更新 `test_strategies_namespace` 测试为标量格式

### 文档更新

- `docs/strategy.md`: 更新 strategies namespace 章节为标量说明
- `docs/executor.md`: 更新计算顺序说明
- `docs/vars.md`: 更新 sum/avg 函数示例（不再使用 strategies 作为示例）
- `examples/001-stablecoin-market-making.md`: 所有 `sum(strategies[...])` 改为 `strategies[...]`
- `proposal/003-static-positions-strategy.md`: 同上
- `features/0008-strategy-data-driven.md`: 同上
- `features/0010-executor-vars-system.md`: 同上
- `features/0011-strategy-target-expansion.md`: 同上

## 验收标准

- ✅ 文档/示例中不再出现"单策略却用 list 聚合"的写法
- ✅ Executor 表达式默认示例可直接运行（不依赖 `sum/avg` 处理 list）
- ✅ 所有 480 个测试通过

## TODO

- [x] 明确 `strategies namespace` 的标量结构（已通过）
- [x] 更新 `docs/strategy.md` 的示例与描述（已通过）
- [x] 更新 `docs/executor.md` 的示例与描述（已通过）
- [x] 更新 `docs/vars.md` 中 `strategies[...]` 相关内容（已通过）
- [x] 全量检索并更新 `examples/*.md` 的相关示例（已通过）
- [x] 补充最小化单测：覆盖策略输出→Executor 注入的标量语义（已通过）


