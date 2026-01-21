# Issue 0019: Scope 架构重构 - 分离 Scope 和 Tree 概念

## 背景

当前 `BaseScope` 的实现存在架构问题：`BaseScope` 直接存储了 `parent` 和 `children`，混淆了 Scope 本身和树形结构的概念。

## 问题描述

### 当前错误的设计

```python
class BaseScope:
    def __init__(self, scope_class_id: str, scope_instance_id: str, parent: Optional['BaseScope'] = None):
        self.scope_class_id = scope_class_id
        self.scope_instance_id = scope_instance_id
        self.parent = parent  # ❌ 错误：Scope 不应该记录 parent
        self.children: dict[str, 'BaseScope'] = {}  # ❌ 错误：Scope 不应该记录 children
```

**问题：**
1. `BaseScope` 混淆了"Scope 本身"和"Scope 在树中的位置"两个概念
2. 导致 `vars` 属性依赖 `parent`，无法独立使用
3. 导致 `mark_not_ready()` 等方法依赖 `children`
4. 违反了单一职责原则

### 正确的设计

**BaseScope** - 只负责存储 Scope 的标识和变量：
```python
class BaseScope:
    def __init__(self, scope_class_id: str, scope_instance_id: str):
        self.scope_class_id = scope_class_id
        self.scope_instance_id = scope_instance_id
        self._vars: dict[str, Any] = {}
        self._not_ready: bool = False
```

**LinkedScopeNode** - 负责树形结构：
```python
class LinkedScopeNode:
    def __init__(self, scope: BaseScope, parent: Optional['LinkedScopeNode'] = None):
        self.scope = scope
        self.parent = parent
        self.children: list['LinkedScopeNode'] = []
```

**LinkedScopeTree** - 负责管理整个树：
```python
class LinkedScopeTree:
    def __init__(self, root: LinkedScopeNode):
        self.root = root

    def get_vars(self, node: LinkedScopeNode) -> ChainMap:
        """获取节点的变量（包含祖先变量）"""
        if node.parent is None:
            return ChainMap(node.scope._vars)
        return ChainMap(node.scope._vars, self.get_vars(node.parent))

    def mark_not_ready(self, node: LinkedScopeNode) -> None:
        """标记节点及其所有子节点为 not_ready"""
        node.scope._not_ready = True
        for child in node.children:
            self.mark_not_ready(child)
```

## 影响范围

### 需要修改的文件

1. **hft/core/scope/base.py**
   - 移除 `parent` 和 `children` 参数
   - 移除 `vars` 属性（改为 `LinkedScopeTree` 提供）
   - 移除 `add_child()`, `get_child()` 方法
   - 移除 `mark_not_ready()` 中的递归逻辑
   - 移除 `get_all_descendants()`, `get_ancestor_chain()` 方法

2. **hft/core/scope/manager.py**
   - 修改 `get_or_create()` 不再传递 `parent` 参数
   - 修改 `build_scope_tree()` 返回 `LinkedScopeTree` 而不是 `list[BaseScope]`

3. **新增文件**
   - `hft/core/scope/tree.py` - 实现 `LinkedScopeNode` 和 `LinkedScopeTree`

4. **使用 Scope 的地方**
   - Strategy 中使用 `scope_trees` 而不是直接使用 `scope`
   - Indicator 中通过 `LinkedScopeTree` 访问变量

## 实现任务

### 阶段 1：创建新的 Tree 类（待实现）

- [ ] 创建 `hft/core/scope/tree.py`（待实现）
- [ ] 实现 `LinkedScopeNode` 类（待实现）
- [ ] 实现 `LinkedScopeTree` 类（待实现）
- [ ] 实现 `get_vars()` 方法（待实现）
- [ ] 实现 `mark_not_ready()` 方法（待实现）

### 阶段 2：重构 BaseScope（待实现）

- [ ] 移除 `BaseScope.__init__()` 中的 `parent` 参数（待实现）
- [ ] 移除 `self.parent` 和 `self.children` 属性（待实现）
- [ ] 移除 `vars` 属性（待实现）
- [ ] 移除 `add_child()`, `get_child()` 方法（待实现）
- [ ] 简化 `mark_not_ready()` 只标记自己（待实现）
- [ ] 移除 `get_all_descendants()`, `get_ancestor_chain()` 方法（待实现）

### 阶段 3：重构 ScopeManager（待实现）

- [ ] 修改 `get_or_create()` 签名（待实现）
- [ ] 修改 `build_scope_tree()` 返回 `LinkedScopeTree`（待实现）
- [ ] 更新树构建逻辑（待实现）

### 阶段 4：更新使用方（待实现）

- [ ] 更新 Strategy 使用 `LinkedScopeTree`（待实现）
- [ ] 更新 Indicator 使用 `LinkedScopeTree`（待实现）
- [ ] 更新测试用例（待实现）

## 验收标准

1. `BaseScope` 不再包含 `parent` 和 `children` 属性
2. 所有树形操作通过 `LinkedScopeTree` 完成
3. 所有测试通过
4. 文档更新反映新架构

## 注意事项

1. **向后兼容**：这是一个破坏性变更，需要仔细规划迁移路径
2. **性能影响**：需要评估新架构对性能的影响
3. **测试覆盖**：需要确保所有边界情况都有测试覆盖

## 相关 Issue

- Issue 0012: Scope 系统实现（原始实现）
- 本 Issue 是对 Issue 0012 的架构改进
