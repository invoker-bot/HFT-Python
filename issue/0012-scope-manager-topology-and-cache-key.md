# Issue 0012: ScopeManager 不应维护 parent/children 拓扑（links 才是拓扑来源）

> **状态**：全部通过

## 问题描述

当前文档/设计口径已经明确：
- Scope 的 parent/children 拓扑由 `links` 构建 LinkTree 时决定
- ScopeManager 仅负责 **scope 实例缓存/注册/查询**，不应“记录拓扑”
- ScopeManager 缓存 key 口径为 `(scope_class_id, scope_instance_id)`（不包含 parent 链）

但当前实现存在相反行为，使得“谁负责拓扑/缓存语义”不一致。

## 现状（实现与口径冲突）

1. **ScopeManager 在 get_or_create 内挂接 children**
   - `hft/core/scope/manager.py` 中：创建 scope 后会执行 `parent.add_child(scope)`
   - 这等价于 ScopeManager 参与维护 parent/children（与“links 才是拓扑来源”的口径冲突）

2. **ScopeManager cache key 含 parent 链（scope_path）**
   - `hft/core/scope/manager.py` 使用 `"scope_class_id:scope_instance_id/parent_path"` 作为缓存 key
   - 这与“缓存 key 为 `(scope_class_id, scope_instance_id)`”的文档/设计口径冲突

## 影响

- **职责边界不清**：links/LinkTree 与 ScopeManager 都在“建树/挂接”，难以推断谁是权威来源
- **缓存语义不稳定**：同一 `(scope_class_id, scope_instance_id)` 在不同 parent 链下会产生多个实例，导致“全局唯一”口径失效
- **复用/性能风险**：不同 links 之间可能无法共享 scope 实例（或出现重复实例），增加内存与计算开销

## 期望行为（以文档口径为准）

- ScopeManager：
  - 只负责 `get_or_create`/缓存/注册
  - 不在内部做 `parent.add_child(...)`
  - 缓存 key 固定为 `(scope_class_id, scope_instance_id)`
- links/LinkTree 构建逻辑：
  - 决定 parent/children
  - 在构建 LinkTree 时挂接 parent/children（以及 ChainMap 继承链）

## TODO

- [x] 明确并统一 ScopeManager 缓存 key 语义（已通过：从 scope_path 收敛到 `(scope_class_id, scope_instance_id)`）
- [x] 从 ScopeManager 中移除 children 挂接逻辑（已通过：不再在 get_or_create 内调用 `parent.add_child(...)`）
- [x] 将 parent/children 挂接移动到 links/LinkTree 构建层（已通过：在 _build_scope_tree_recursive 中挂接）
- [x] 补充/更新单元测试：覆盖"同 scope 多 links 复用"与"拓扑由 links 决定"的断言（已通过：更新了 test_same_key_returns_same_instance）

