# Issue 0016: 文档/示例统一将 keep_positions 改为 static_positions

> **状态**：全部通过

## 背景

策略类名历史上使用过 `keep_positions`，但当前文档口径已收敛为 `static_positions`（更贴近语义：静态目标仓位）。

目前 docs/examples 中仍大量出现 `class_name: keep_positions`，会造成：
- 新用户误以为 `keep_positions` 是推荐/唯一写法
- 迁移/排障时难以判断"这是别名还是另一个策略"

## 目标

- docs/examples 中：统一使用 `static_positions`（包含示例配置、标题、说明文字）
- 实现已移除 `keep_positions` 别名支持（不再向后兼容）
- 文档中不再提及 `keep_positions`，除非作为历史说明

## 影响范围

- `docs/strategy.md` - 已清理
- `examples/001-stablecoin-market-making.md` - 已清理
- `proposal/003-static-positions-strategy.md` - 已清理
- `features/0011-strategy-target-expansion.md` - 已清理
- `features/0008-strategy-data-driven.md` - 已清理
- `features/0004-integration-trading-tests.md` - 已清理
- `CLAUDE.md` - 已清理
- `hft/strategy/static_positions.py` - 已清理代码注释

## 验收标准

- `rg "\bkeep_positions\b" docs examples proposal features` 仅剩历史说明，不再出现在推荐示例配置中 ✅
- 所有示例 YAML 的 `class_name` 已统一为 `static_positions` ✅

## TODO

- [x] 全量检索并替换 docs/examples/proposal/features 中的 `keep_positions`（已通过）
- [x] 别名已移除，不再向后兼容（Feature 0011 已完成）（已通过）
- [x] 文档已统一为 `static_positions`（已通过）
