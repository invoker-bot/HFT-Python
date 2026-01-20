# Feature 0014: ConfigPath Types + Listener Cache Pickle

> **状态**：全部通过

## 背景与目标

本 Feature 是一次“基础架构调整”，目标是把 **配置加载** 与 **运行时状态持久化（pickle）** 做成更可控、更低耦合、更易迁移的体系：

1. **配置路径字段类型化**：用 Pydantic 自定义字段类型（`*ConfigPath` / `*ConfigPathGroup`）替代裸 `str`，统一加载/保存/缓存行为。
2. **统一根路径**：所有 `BaseConfig.load/save/list_configs` 的 `cwd` 默认值改为 `os.getenv("HFT_ROOT_PATH", ".")`。
3. **AppConfig 单策略**：App 只加载 1 条 Strategy（`strategy`），不再通过 `strategies:` 加载多条 Strategy。
4. **pickle 只保存 Listener cache，不保存整棵树链接**：`children` 从 pickle 中排除；恢复时用 `get_or_create(cache, Class, name, parent)` 重新链接树。
5. **Listener 构造函数无参数**：不再在构造函数里传 `name/interval/其他 Listener/config`；在 `initialize()`/`on_start()` 中通过 `self.root` 获取配置并设置运行参数。

---

## 术语与约束

- **HFT_ROOT_PATH**：运行时配置根目录。默认 `"."`，用于加载 `conf/**.yaml`。
- **Config ID / Pathname**：指 `BaseConfig.load(<pathname>)` 的 `<pathname>`，不含 `.yaml`，相对其 `class_dir`。
- **Listener cache**：`dict[str, Listener]`，key 由 `build_cache_key(cls, name, parent)` 生成（格式：`"ClassName:name/parent_key"`）。
- **严格树（instance-level）**：Listener/Scope 等运行时实例只有一个 `parent`，`children` 由 parent 持有。

---

## 1. BaseConfig 行为调整（cwd 默认值）

### 1.1 新默认行为

所有调用 `BaseConfig.load/save/list_configs` 时，若未显式传入 `cwd`：

```python
cwd = os.getenv("HFT_ROOT_PATH", ".")
```

### 1.2 验收标准

- 未设置 `HFT_ROOT_PATH` 时：行为与现状一致（相对当前工作目录加载）。
- 设置 `HFT_ROOT_PATH=/path/to/repo` 时：可在任意工作目录运行并正确加载配置。

---

## 2. ConfigPath 类型体系

### 2.1 BaseConfigPath（Pydantic 自定义字段类型）

目标：把“配置引用”从 `str` 升级为对象，提供统一 API：

```python
class BaseConfigPath:
    class_dir: ClassVar[str] = "conf/"
    name: str

    def load(self) -> BaseConfig: ...
    def save(self, config: BaseConfig) -> None: ...

    @property
    def instance(self): ...
```

建议实现为 **Pydantic BaseModel 包装**（而不是裸 `str`），便于：
- YAML 中仍以字符串形式表达（`name`）
- 运行时提供 `load()/instance` 方法
- 为 `instance` 做 lazy cache（一次加载，多处复用）

### 2.2 专用 Path 类型

举例（命名仅示意，按现有 config 类调整）：

- `AppConfigPath(BaseConfigPath)`：加载 `conf/app/<name>.yaml`
- `StrategyConfigPath(BaseConfigPath)`：加载 `conf/strategy/<name>.yaml`
- `ExecutorConfigPath(BaseConfigPath)`：加载 `conf/executor/<name>.yaml`
- `ExchangeConfigPath(BaseConfigPath)`：加载 `conf/exchange/<name>.yaml`

### 2.3 AppConfig 使用方式

新的 AppConfig 结构：

```python
class AppConfig(BaseConfig[AppCore]):
    exchanges: ExchangeConfigPathGroup
    strategy: StrategyConfigPath
    executor: ExecutorConfigPath
```

说明：
- 不再是 `strategies: list[StrategyConfigPath]`
- 单条策略由 `strategy` 字段引用

### 2.4 验收标准

- `AppConfig` YAML 中 `strategy/executor/exchanges` 字段可被 Pydantic 正确解析。
- `*.instance` 访问会加载对应 config 并返回 `.instance`（实现对象）。
- 多处访问 `.instance` 不重复加载（有缓存）。

---

## 3. ExchangeConfigPathGroup（选择器 + 映射）

### 3.1 输入与语义

`ExchangeConfigPathGroup` 的输入是 `list[str]`（选择器列表），支持：
- `*`：匹配所有
- `!pattern`：排除
- glob pattern：如 `okx/*`、`binance/demo-*`

当选择器列表为空（`[]`）时，语义等价 `["*"]`（匹配所有）。

### 3.2 过滤语法（运行时参数）

对外提供 `id_filter` 字符串参数：逗号分隔，语义与选择器一致：

- 空字符串 `""`：等价 `"*"`（匹配所有）
- `"okx/*,!okx/a"`：先包含再排除

> 实现建议：可用 `younoyou` 做 include/exclude 过滤；否则用 `fnmatch` + 简单 include/exclude 两段式也可。

### 3.3 API 设计

#### 3.3.1 get_id_map

```python
@lru_cache
def get_id_map(self, id_filter: str = "*") -> dict[str, ExchangeConfigPath]:
    return {
        "okx/a": ExchangeConfigPath(name="okx/a"),
        ...
    }
```

说明：
- key 为 exchange config 的 pathname（如 `"okx/a"`）
- value 为对应的 `ExchangeConfigPath`
- `@lru_cache` 用于缓存（参数化方法不要用 `@cached_property`）

#### 3.3.2 get_grouped_id_map

```python
@lru_cache
def get_grouped_id_map(
    self,
    id_filter: str = "*",
    group_filter: str = "*",
) -> dict[str, list[str]]:
    return {
        "okx": ["okx/a", "okx/b"],
        "binance": ["binance/main"],
    }
```

说明：
- group key 的默认规则：取 pathname 的第一个分组（如 `okx/a` -> `okx`）
- `group_filter` 支持同样的 include/exclude 语法

#### 3.3.3 get_grouped_map

```python
@lru_cache
def get_grouped_map(
    self,
    id_filter: str = "*",
    group_filter: str = "*",
) -> dict[str, list[ExchangeConfigPath]]:
    return {
        "okx": [ExchangeConfigPath("okx/a"), ExchangeConfigPath("okx/b")],
    }
```

### 3.4 验收标准

- `[]` 与 `["*"]` 行为一致：返回所有 exchange config。
- include/exclude 顺序明确，且对 `id_filter` 与 group 内部一致生效。
- 缓存命中正确：同一过滤参数重复调用不会重复扫描磁盘。

---

## 4. 单策略 App：strategy 替代 strategies

### 4.1 新旧字段

- 新：`strategy: StrategyConfigPath`
- 旧：`strategies: list[StrategyConfigPath]`

迁移策略：
- 文档与模板统一使用 `strategy`
- 代码迁移期可短暂兼容 `strategies`（若出现则取第一个 / 或报 warning 并拒绝启动，具体由实现方决定）

### 4.2 验收标准

- App 在仅配置 `strategy` 的情况下可完整启动并运行。
- 若实现提供兼容：旧配置可启动（行为明确且有日志提示）。

---

## 5. Listener cache + pickle 恢复

### 5.1 新的持久化对象

pickle 不再保存整棵 Listener 树（尤其不保存 `children` 的递归链接），而是保存：

- `cache: dict[str, Listener]`（或其 pickle 结果）
- `AppConfig` 不作为 cache 的一部分持久化（恢复时由外部重新加载并注入到 AppCore）

### 5.2 get_or_create 工厂

新增统一工厂函数（伪代码）：

```python
def get_or_create(cache: dict[str, Listener], cls: type[Listener], name: str, parent=None):
    # cache key 包含 parent 链
    key = build_cache_key(cls, name, parent)  # 格式："ClassName:name/parent_key"
    if key not in cache:
        cache[key] = cls()  # Listener 子类构造函数无参数
    inst = cache[key]
    inst.name = name
    inst.parent = parent
    if parent is not None:
        parent.add_child(inst)
    return inst
```

说明：
- cache key 包含 parent 链，支持同一 Listener 在不同父节点下有不同状态
- `parent` 由调用方决定（通常是当前 Listener）
- 通过 `parent.add_child()` 重建树链接
- 子 Listener 的 `config/interval/依赖` 在 `initialize()/on_start()` 阶段通过 `self.root` 获取并设置

### 5.3 恢复顺序（入口流程）

```
if 存在 pickle:
  cache = load_pickle()
else:
  cache = {}

config = AppConfigPath(...).load()
app_core = get_or_create(cache, AppCore, "app/main")
app_core.config = config   # 全局唯一
app_core.loop()            # 或 run_ticks(...)
```

### 5.4 组件如何取配置（统一规范）

仅 `AppCore` 直接持有 `self.config: AppConfig`，其他 Listener 通过 `self.root.config` 访问：

例：Exchange 取配置（示意）：

```python
exchange_config = (
    self.root.config.exchanges
    .get_id_map(...)[self.name]
    .instance
)
```

为避免重复长链调用：Exchange 内部可用 `@cached_property def config(self): ...` 做缓存（注意：配置热更新时需要明确失效策略）。

### 5.5 验收标准

- pickle 后重启：不会重复创建 Listener；同名 Listener 状态可恢复。
- 树链接由 restore 流程重建；pickle 本身不包含 children。
- Listener 不再从构造函数接收其他 Listener 或 config 对象。

---

## 6. 风险与需要决策的点

1. **兼容策略**：旧 `strategies` 字段如何处理（兼容/拒绝/警告 + 取第一个）。
2. **ConfigPath.instance 缓存失效**：是否支持热重载？如支持，失效时机与 API 需要明确。
3. **Listener key 规则稳定性**：`ClassName-name` 是否足够唯一；是否需要加 `scope/app_id` 前缀。
4. **磁盘扫描开销**：`list_configs` 扫描目录 + 过滤需要缓存；同时要避免缓存导致“新配置文件上线不可见”的困惑（需明确刷新入口）。

---

## TODO（实现清单）

- [x] 为 BaseConfig 的 `load/save/list_configs` 引入 `HFT_ROOT_PATH` 默认 cwd（已通过）
- [x] 定义 `BaseConfigPath`（Pydantic 字段类型）+ `*.instance` lazy cache（已通过）
- [x] 为 AppConfig 引入 `exchanges/strategy/executor` 三个 Path 字段，并移除 `strategies`（已通过）
- [x] 实现 `ExchangeConfigPathGroup` 的过滤语法与 `get_id_map/get_grouped_*` API（已通过）
- [x] Listener 构造函数无参数化改造 + `get_or_create(cache, cls, name, parent)`（已通过：cache key 包含 parent 链）
- [x] pickle 改为仅保存 cache，不保存 children；恢复时重建树链接（已通过：cache key 包含 parent 链，支持同一 Listener 在不同父节点下有不同状态）
- [x] 更新 conf 模板与 examples（迁移到新字段：`strategy`，exchange 选择器语法）（已通过）
- [x] 新增/补充最小单元测试：ConfigPath 加载、filter 语法、pickle 恢复（已通过：tests/test_listener_cache.py 包含 parent 链 cache key 的测试）
