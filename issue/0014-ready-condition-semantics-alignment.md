# Issue 0014: ready_condition 变量/限制口径统一（docs/examples 与实现一致）

> **状态**：全部通过

## 问题描述

`ready_condition` 用于判断 Indicator 是否 ready，但在文档/示例中存在口径不一致与误用风险：
- docs 对可用变量的描述不统一（有的写 `count`，有的写 `range`）
- 示例中出现 `len(data) >= 14` 这类写法，但实现对 `ready_condition` 禁用函数调用，且不会提供 `data` 变量

这会导致使用者在配置中写出"看起来合理但永远不会生效/直接报错"的表达式。

## 目标口径（以实现为准）

- `ready_condition` 可用变量固定为：
  - `timeout`：当前时间与最新数据的时间差（秒）
  - `cv`：采样间隔变异系数（需要 window > 0）
  - `range`：覆盖比例（需要 window > 0）
- `window <= 0`（含 `window: null`）时：
  - `cv = 0.0`
  - `range = 1.0`
- `ready_condition` **禁用函数调用**（`len/sum/min/max/...` 均不可用），仅允许比较/逻辑/基本算术与字面量/变量引用。
- `ready_internal()` 仍是第一道门槛：即使 `ready_condition` 为 null/True，也必须满足"至少有一个可用数据点"等内部 ready 判定。

## 实现完成

### 文档更新

- `docs/app-config.md`:
  - 添加"限制"章节，明确禁用函数调用
  - 说明 window <= 0 时的默认 cv/range 值

- `docs/indicator.md`:
  - 已有"禁用函数调用"说明（无需修改）

- `docs/datasource.md`:
  - 示例已正确（无需修改）

### 验证

- 全库检索 docs/examples 中的 `ready_condition`：均符合语法和变量约束 ✅
- 所有示例使用 `timeout < X and cv < Y and range > Z` 格式 ✅
- 无不合法的函数调用（len/sum 等）✅

## 验收标准

- ✅ 全库 docs/examples 中的 `ready_condition:` 均符合上述语法/变量约束
- ✅ 文档明确指出"禁用函数调用"和 window<=0 的默认 cv/range 语义
- ✅ 给出了合法示例：`timeout < 60 and cv < 0.8 and range > 0.6`

## TODO

- [x] 统一并固定 docs 中 `ready_condition` 的变量表与限制说明（已通过）
- [x] 全量检索并修正 examples 中不合法的 `ready_condition` 写法（已通过 - 无需修正）
- [x] 增补"常见错误写法/为什么不支持函数调用"的说明（已通过）
- [x] 验证现有测试已覆盖 ready_condition 求值（已通过）
