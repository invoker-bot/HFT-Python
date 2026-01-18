# Review Board

本文件用于汇总当前需要关注的 `features/*.md` 与 `issue/*.md`（避免漏审与重复阅读）。

规则：
- 本表用于记录“哪些编号已经通过/哪些还卡着”，避免下次忘记进度。
- 为节省篇幅：优先用区间合并（如 `0001-0010 已通过`）。仅当不同文件状态不同（或需要备注）时，才拆成多行。
- 若某个文件后续被修改：必须先移除该文件的 `> **状态**：全部通过`，并把受影响的任务条目回退到 `（待审核）`/`（待实现）`；同时把该文件重新加入本表。

状态口径（与任务条目一致）：`待实现` / `待审核` / `审核不通过` / `待商议` / `已通过` / `待更新（格式/标记缺失）`

## Features

| ID | 文件 | 状态 | 备注 |
|---:|---|---|---|
| 0001-0005, 0007 | `features/*.md` | 已通过 | 对应文件头均已标记 `> **状态**：全部通过` |
| 0006 | `features/0006-indicator-datasource-unification.md` | 待审核 | 补充 `window: null` 语义（等价 `0`） |
| 0008 | `features/0008-strategy-data-driven.md` | 待审核 | Phase 6: vars 简化格式支持（已实现代码和文档，待审核） |
| 0009-0011 | `features/*.md` | 已通过 | 对应文件头均已标记 `> **状态**：全部通过` |
| 0012 | `features/0012-scope-system.md` | 待审核 | Scope 系统（Phase 1-2-5 已完成：ScopeManager, BaseStrategy, AppCore 集成；Phase 3-4 暂缓） |
| 0013 | `features/0013-market-neutral-positions-strategy.md` | 待审核 | MarketNeutralPositions 策略（Phase 1 部分完成，阻塞于 Feature 0012） |

## Issues

| ID | 文件 | 状态 | 备注 |
|---:|---|---|---|
| 0001-0008 | `issue/*.md` | 已通过 | 对应文件头均已标记 `> **状态**：全部通过` |
| 0009 | `issue/0009-strategy-method-name-conflicts-with-despecialization.md` | 待实现 | Strategy 方法名与"去特殊化"设计冲突 |
| 0010 | `issue/0010-indicator-window-null-normalization.md` | 待实现 | `window: null` 语义等价 `0`，需要实现侧做 None -> 0 归一化 |
