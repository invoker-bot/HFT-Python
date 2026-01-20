# Issue 0011: Listener/Pickle 缓存架构改进

> **状态**：全部通过

## 问题描述

本 Issue 记录 Listener 的 pickle/cache 机制的架构设计和实现状态。

## 架构设计

### 核心原则

1. **Cache Dict 模式**：pickle 保存的是 `dict[cache_key, state_dict]`，而不是整棵 Listener 树
2. **Children 排除**：`_children` 和 `_parent` 不被序列化
3. **树重建**：parent/children 关系在 load 后通过 `get_or_create(..., parent=...)` 重建
4. **异步 I/O**：使用 `asyncio.to_thread()` 将文件 I/O 放到线程池，避免阻塞主循环

### Cache Key 设计

**当前实现**：cache key 包含 parent 链

```python
# 格式："ClassName:name/parent_key"
# 示例："Exchange:okx/main/AppCore:app/main"
```

**设计理由**：
- 支持同一个 Listener 在不同父节点下有不同状态
- 确保 cache key 的唯一性和稳定性

## 当前实现状态

### 已实现的功能

1. **Cache Dict 模式**（`hft/core/app/listeners.py:71-95`）
   - `CacheListener.save_cache_async()` 收集所有 Listener 状态到 cache dict
   - 序列化 cache dict 为 bytes
   - 使用 `asyncio.to_thread()` 异步写入文件

2. **Children 排除**（`hft/core/listener.py`）
   - `__pickle_exclude__` 包含 `_children` 和 `_parent`
   - `__setstate__` 不恢复 children/parent

3. **get_or_create 机制**（`hft/core/listener_cache.py:40-80`）
   - 从缓存获取或创建 Listener 实例
   - 建立父子关系
   - 支持 cache key 包含 parent 链

4. **ListenerCache 类**（`hft/core/listener_cache.py:83-150`）
   - `collect()` 方法收集所有 Listener 状态
   - `restore()` 方法从 cache dict 恢复 Listener 树

## 任务列表

> **架构决策**：cache key **包含 parent 链**（格式：`"ClassName:name/parent_key"`），用于区分同名 Listener 在不同父节点下的状态。

- [x] 修改 `__pickle_exclude__` 排除 `_children`（已通过）
- [x] 实现 Listener 级别的 `get_or_create` 函数（已通过）
- [x] 修改 `__setstate__` 不恢复 children（已通过）
- [x] 修改 CacheListener 只保存 cache dict（已通过）
- [x] 实现 cache dict 的加载和树重建逻辑（已通过）
- [x] 添加单元测试验证新机制（已通过）

## 实现文件

- `hft/core/listener.py` - 修改 `__pickle_exclude__`、`initialize()`、`__setstate__()`
- `hft/core/listener_cache.py` - 新增文件，实现 `get_or_create`、`build_cache_key`、`ListenerCache`
- `hft/core/app/listeners.py` - 修改 `CacheListener` 使用新的缓存机制
- `tests/test_listener_cache.py` - 新增测试文件，15 个测试全部通过

## 注意事项

1. **向后兼容**：旧缓存文件需要能正常加载或优雅降级
2. **GroupListener 特殊处理**：动态子节点已经不保存 children，需要统一
3. **性能考虑**：get_or_create 应该高效，避免重复查找

## 参考

- Scope 的 get_or_create 实现：`hft/core/scope/manager.py:116`
- 当前 Listener pickle 实现：`hft/core/listener.py:67-135`
- 当前 CacheListener 实现：`hft/core/app/listeners.py:23-85`
