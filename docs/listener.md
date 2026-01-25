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

# 使用 lru_cache 缓存查找结果
# 树变动时在 root 调用 cache_clear() 清理缓存
```

### 查找方法

```python
# 查找第一个匹配
executor = root.find_child_by_class(BaseExecutor)

# 查找所有匹配
executors = root.find_children_by_class(BaseExecutor)

# 从指定节点开始查找
strategy = root.find_child_by_class_at_node(BaseStrategy, executor)
strategies = root.find_children_by_class_at_node(BaseStrategy, executor)
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
        # Listener 构造函数不再接收运行时参数；推荐把参数编码到 name 中，或通过 root.config 查找。
        # 这里示例把 key 写入 name：watch-{key}，child 通过解析 name 获取 key。
        #
        # 注意：实际实现应通过 get_or_create(cache, ...) 来确保：
        # 1) 相同 (cls, name) 复用同一实例；2) child.name 被正确设置；3) parent/children 链接被重建。
        child = get_or_create(cache=self.root.listener_cache, cls=ExchangeBalanceWatchListener, name=name, parent=self)
        return child
```

### 同步逻辑

```python
# _sync_children() 在 on_start() 和 on_tick() 中自动调用
# 1. 对比 sync_children_params() 返回的 names 和当前 children
# 2. 删除多余的 children
# 3. 创建缺少的 children
```

**重要**：`create_dynamic_child()` 返回的 child 必须具备稳定的 `name`（与 `sync_children_params()` 的 key 一致），以保证：

- 与 Listener cache 的 key 对齐（可复用/可恢复）
- 可用 `name` 作为配置查找键（常见：`self.root.config...get_id_map()[self.name]`）

## 持久化与恢复（Listener cache）

为降低耦合并支持“从 pickle 恢复运行态”，Listener 的持久化不再保存整棵树，而是保存一个**实例缓存**：

```python
# cache: {listener_key: listener_instance}
cache = {f"{ClassName}-{name}": instance}
```

`cache` 的生命周期通常由 `AppCore` 管理；实现上可将其挂在 `AppCore`/`root` 上（例如 `root.listener_cache`），或由启动器显式传入。

其中 `listener_key` 的生成必须稳定、无歧义。至少应包含：

- Listener 的类型（类名；更稳妥可使用“全限定名”）
- Listener 的 `name`（业务 ID；通常与配置 ID 对齐）

### 为什么不持久化 children

children 是运行时可推导结构（由配置与调度逻辑决定），持久化 children 会带来：

- pickle 体积膨胀与循环引用风险
- “恢复后结构滞后”：配置/代码变更后，旧 children 结构可能不再合法
- 组件强耦合：构造函数必须携带大量依赖才能重建图结构

因此：**pickle 只保存 Listener 实例的最小状态**，树结构在启动时重建。

### `get_or_create(cache, cls, name, parent=None)` 语义

`get_or_create` 是恢复/构建 Listener 树的唯一入口（核心要求：幂等 + O(1) 查找）：

1. 根据 `(cls, name)` 生成 `listener_key`
2. 若 `listener_key` 已存在于 `cache`：直接复用该实例
3. 否则：`cls()` 创建新实例并放入 `cache`
4. 若传入了 `parent`：设置 parent/children 关系（通常通过 `parent.add_child(child)`）
5. 对于不可 pickle 的对象（锁、task、logger handler 等），应在 `initialize()` 或 `on_start()` 内重建

### 恢复流程（严格顺序）

1. 加载 AppConfig（`AppConfigPath.instance`）
2. 若存在 pickle：加载得到 `cache`；否则 `cache = {}`
3. `app_core = get_or_create(cache, AppCore, "app/<name>")`
4. 将**唯一的** `AppConfig` 注入到 `app_core.config`
5. 启动 AppCore，由 AppCore/GroupListener 在 `on_start()` 或 `on_tick()` 中用 `get_or_create` 重建 children
6. 周期性将 `cache` 重新 pickle（不序列化树链接）

### Listener 获取配置（禁止构造函数注入）

除 `AppCore` 外，其他 Listener 不应持有“全局配置对象”；统一通过 `self.root.config` 获取：

- Exchange Listener：通过 `self.root.config.exchanges.get_id_map(...)[self.name].instance` 获取 `ExchangeConfig`
- Strategy/Executor Listener：通过 `self.root.config.strategy.instance` / `self.root.config.executor.instance` 获取配置

出于性能考虑，允许在 Listener 内部使用 `cached_property` 缓存一次解析结果（需确保 name/配置不会在运行中变更）。

```python
__pickle_exclude__ = (
    "_parent",
    "_children",         # 树结构运行时重建
    "_background_task",
    "_alock",
    "_class_index",
    "root",
)
```

## 最佳实践

### 构造函数

```python
# Good: 无参构造；运行时信息由 name/root.config 推导
class MyListener(Listener):
    def __init__(self):
        super().__init__()

# Bad: 在构造函数里传入其他 Listener（强耦合 + 不利于 pickle 恢复）
class MyListener(Listener):
    def __init__(self, exchange: BaseExchange):  # 错误！
        self.exchange = exchange
```

### 获取依赖

```python
# Good: 通过树形结构获取（parent/root），或通过 root.config 查找
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
