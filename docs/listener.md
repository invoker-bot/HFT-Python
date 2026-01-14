# Listener 系统详解

## 概述

Listener 是 HFT-Python 的核心抽象，所有组件都继承自它，形成统一的树形结构。

## 核心概念

### 状态机

```python
class ListenerState(StrEnum):
    STARTING = "starting"   # 启动中
    RUNNING = "running"     # 运行中
    STOPPING = "stopping"   # 停止中
    STOPPED = "stopped"     # 已停止
    ERROR = "error"         # 错误状态
```

状态转换规则：
- `STOPPED` → `STARTING`：调用 `start()`
- `STARTING` → `RUNNING`：`tick()` 执行 `on_start()` 成功
- `RUNNING` → `STOPPING`：调用 `stop()` 或 `on_tick()` 返回 True
- `STOPPING` → `STOPPED`：`tick()` 执行 `on_stop()` 完成

### 生命周期回调

```python
class Listener(ABC):
    async def on_start(self):
        """启动时调用，子类可覆盖"""
        pass

    @abstractmethod
    async def on_tick(self) -> bool:
        """定时调用，必须实现
        返回 True 表示任务完成，触发停止
        """
        pass

    async def on_stop(self):
        """停止时调用，子类可覆盖"""
        pass

    async def on_health_check(self) -> bool:
        """健康检查，子类可覆盖"""
        return True
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | str | 监听器名称 |
| `interval` | float | tick 间隔（秒） |
| `state` | ListenerState | 当前状态 |
| `enabled` | bool | 是否启用 |
| `healthy` | bool | 是否健康 |
| `ready` | bool | enabled & healthy & RUNNING |
| `parent` | Listener | 父节点 |
| `children` | dict | 子节点 |
| `root` | Listener | 根节点（缓存） |
| `uptime` | float | 运行时长（秒） |

## 父子关系

### 添加/移除子节点

```python
# 添加子节点
parent.add_child(child)
# child.parent = parent
# 同时更新类索引

# 移除子节点
parent.remove_child("child_name")
# child.parent = None
# 同时更新类索引
```

### 递归操作

```python
# 递归启动所有子节点
await listener.start(recursive=True)

# 递归停止所有子节点
await listener.stop(recursive=True)

# 递归健康检查
await listener.health_check(recursive=True)
```

### 遍历

```python
# 深度优先遍历所有后代（包括自身）
for child in listener:
    print(child.name)
```

## 类索引

用于 O(1) 复杂度按类查找子节点。

### 注册机制

```python
# add_child 自动注册到根节点的类索引
root.add_child(executor)

# 索引结构：{Type: [(weakref, depth), ...]}
# root._class_index = {
#     MarketExecutor: [(ref, 1)],
#     BaseExecutor: [(ref, 1)],  # 父类也注册
# }
```

### 查找方法

```python
# 查找第一个匹配（最浅）
executor = root.find_child_by_class(BaseExecutor)

# 查找所有匹配（按深度排序）
executors = root.find_children_by_class(BaseExecutor)

# 查找指定深度的匹配
level1_executors = root.find_children_by_class_at_depth(BaseExecutor, 1)
```

## GroupListener

用于动态管理子节点的场景。

### 使用场景

- 根据配置动态创建/删除子节点
- 如：ExchangeBalanceListener 根据 ccxt_instances 创建 WatchListener

### 实现

```python
class ExchangeBalanceListener(GroupListener):
    def sync_children_params(self) -> dict[str, Any]:
        """声明需要的 children（必须实现）"""
        exchange = self.parent
        params = {}
        for key in exchange.config.ccxt_instances.keys():
            params[f"watch-{key}"] = {"key": key, "type": "watch"}
        return params

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        """创建 child 实例（必须实现）"""
        return ExchangeBalanceWatchListener(name=name, ccxt_instance_key=param["key"])
```

### 同步逻辑

```python
# _sync_children() 在 on_start() 和 on_tick() 中自动调用
# 1. 对比 sync_children_params() 返回的 names 和当前 children
# 2. 删除多余的 children
# 3. 创建缺少的 children
```

**重要**：`create_dynamic_child()` 的 `name` 参数必须传给子类，确保与 `sync_children_params()` 的 key 一致。

## 序列化

Listener 支持 pickle 序列化，用于状态持久化。

### 排除项

```python
__pickle_exclude__ = ("_parent", "_background_task", "_alock", "_class_index", "root")
```

### 恢复逻辑

```python
def __setstate__(self, state):
    self.__dict__.update(state)
    self.initialize()  # 重建不可序列化对象
    # 恢复子节点的 parent 引用和类索引
    for child in self._children.values():
        child.parent = self
        self._register_to_class_index(child, relative_depth=1)
```

## 最佳实践

### 构造函数

```python
# Good: 只接受配置或简单值
class MyListener(Listener):
    def __init__(self, config: MyConfig):
        super().__init__(config.name, config.interval)
        self.config = config

# Bad: 传入其他 Listener
class MyListener(Listener):
    def __init__(self, exchange: BaseExchange):  # 错误！
        self.exchange = exchange
```

### 获取依赖

```python
# Good: 通过树形结构获取
@property
def exchange(self) -> "BaseExchange":
    return self.parent  # 或 self.root.exchange_group.get(name)

# Good: 防御性检查
@property
def exchange(self) -> "BaseExchange | None":
    if self.parent is None or self.parent.parent is None:
        return None
    return self.parent.parent
```

### 健康检查

```python
async def on_health_check(self) -> bool:
    """返回 True 表示健康，False 或抛异常表示不健康"""
    if not self.some_condition:
        return False
    return True
```
