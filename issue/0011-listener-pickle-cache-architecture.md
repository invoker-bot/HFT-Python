# Issue 0011: Listener/Pickle 缓存架构改进

## 问题描述

当前 Listener 的 pickle/cache 机制存在设计问题，不符合"cache dict + children 排除 + get_or_create 重建树"的目标架构。

## 当前问题

### 1. Listener 构造函数仍有 name/interval 参数
- **位置**：`hft/core/listener.py:78`
- **问题**：这些参数应该从缓存中恢复，而不是每次构造时传入

### 2. pickle 仍会包含 _children
- **位置**：`hft/core/listener.py:73`（`__pickle_exclude__` 未排除 `_children`）
- **问题**：children 应该通过 get_or_create 重建，而不是序列化整棵树

### 3. __setstate__ 会恢复 children/parent/class_index
- **位置**：`hft/core/listener.py:119`
- **问题**：这与"children 排除 + 重建树"的目标冲突

### 4. CacheListener 仍 pickle 整个 self.root
- **位置**：`hft/core/app/listeners.py:26`、`hft/core/app/listeners.py:73`
- **问题**：应该只保存 cache dict，而不是整棵 Listener 树

### 5. 缺少全局 get_or_create 函数
- **现状**：只有 Scope 有 `get_or_create`（`hft/core/scope/manager.py:116`）
- **需求**：需要 Listener 级别的 `get_or_create(cache, Class, name, parent=None)`

## 目标架构

### 1. Cache Dict 模式
```python
# 只保存每个 Listener 的自身状态（不含 children）
cache = {
    "AppCore": {"interval": 1.0, "state": "RUNNING", ...},
    "ExchangeGroup": {...},
    "ExchangeGroup/okx/main": {...},
    ...
}
```

### 2. Children 排除
```python
class Listener:
    __pickle_exclude__ = {
        '_parent', '_background_task', '_alock', '_class_index', 'root',
        '_children',  # 新增：排除 children
    }
```

### 3. get_or_create 重建树
```python
def get_or_create(
    cache: dict,
    listener_class: Type[Listener],
    name: str,
    parent: Optional[Listener] = None,
    **kwargs
) -> Listener:
    """
    从缓存获取或创建 Listener 实例。

    如果缓存中存在，恢复状态；否则创建新实例。
    """
    cache_key = _build_cache_key(listener_class, name, parent)

    if cache_key in cache:
        # 从缓存恢复
        instance = listener_class.__new__(listener_class)
        instance.__setstate__(cache[cache_key])
    else:
        # 创建新实例
        instance = listener_class(name=name, **kwargs)

    # 建立父子关系
    if parent is not None:
        parent.add_child(instance)

    return instance
```

### 4. CacheListener 改进
```python
class CacheListener:
    async def save_cache_async(self):
        # 收集所有 Listener 的状态（不含 children）
        cache_dict = self._collect_cache(self.root)
        data = pickle.dumps(cache_dict, protocol=pickle.HIGHEST_PROTOCOL)
        await asyncio.to_thread(self._write_cache_file, data)

    def _collect_cache(self, listener: Listener) -> dict:
        """递归收集所有 Listener 的状态"""
        result = {}
        key = self._build_cache_key(listener)
        result[key] = listener.__getstate__()  # 不含 children

        for child in listener.children.values():
            result.update(self._collect_cache(child))

        return result
```

## 任务列表

- [ ] 修改 `__pickle_exclude__` 排除 `_children`（待审核）
- [ ] 实现 Listener 级别的 `get_or_create` 函数（待审核）
- [ ] 修改 `__setstate__` 不恢复 children（待审核）
- [ ] 修改 CacheListener 只保存 cache dict（待审核）
- [ ] 实现 cache dict 的加载和树重建逻辑（待审核）
- [ ] 添加单元测试验证新机制（待审核）

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
