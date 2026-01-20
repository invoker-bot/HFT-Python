# 配置路径类型（ConfigPath）

本文定义“配置路径类型（ConfigPath）”机制：在 Pydantic 配置模型字段中直接使用强类型的“配置引用”，并提供**统一的路径解析、加载/保存、缓存与筛选**能力。

## 根目录：`HFT_ROOT_PATH`

所有配置文件的相对根目录来自环境变量：

- `HFT_ROOT_PATH`：配置根目录；默认值为 `.`（当前工作目录）

目录约定（相对于 `HFT_ROOT_PATH`）：

- `conf/app/`：App 配置
- `conf/exchange/`：Exchange 配置
- `conf/strategy/`：Strategy 配置
- `conf/executor/`：Executor 配置

## 术语与命名

- `config_id`：配置文件的逻辑 ID（不含 `.yaml`），例如 `okx/main`
- `config_file`：实际文件路径，例如 `$HFT_ROOT_PATH/conf/exchange/okx/main.yaml`
- `ConfigPath`：可被 Pydantic 字段解析的自定义类型；其值携带 `config_id`，并知道如何定位到 `config_file`

为避免歧义：本文中 `name` 与 `config_id` 同义（都是“不含扩展名的相对路径”）。

## `BaseConfigPath`

`BaseConfigPath` 是所有 ConfigPath 类型的基类，用于表达“某类配置文件的引用”。

### 字段

- `name: str`：`config_id`
- `class_dir: ClassVar[str]`：该配置类别在 `conf/` 下的目录（例如 `conf/exchange`）

### 路径解析规则

给定：

- `root = os.getenv("HFT_ROOT_PATH", ".")`
- `class_dir`（例如 `conf/exchange`）
- `name`（例如 `okx/main`）

则：

- `config_file = f"{root}/{class_dir}/{name}.yaml"`

### 行为语义

- `load() -> BaseConfig`：从 `config_file` 读取并解析为对应的 `BaseConfig` 子类实例
- `save(config: BaseConfig) -> None`：将给定 config 写回到 `config_file`（实现可选择原子写入）
- `instance -> BaseConfig`：对 `load()` 的**惰性缓存**（同一个 `ConfigPath` 对象多次访问应返回同一个 config 实例）

### 错误语义（必须清晰）

实现应区分并输出可定位的信息：

- 文件不存在：指出 `config_file`
- YAML 解析失败：指出 `config_file` 与行列（如可获得）
- Pydantic 校验失败：指出字段路径（field path）与错误原因

## 预定义 ConfigPath 类型

不同配置类别通过继承 `BaseConfigPath` 仅改变 `class_dir`：

| 类型 | `class_dir` | 示例 `name` | 示例 `config_file` |
|------|-------------|------------|--------------------|
| `AppConfigPath` | `conf/app` | `main` | `$HFT_ROOT_PATH/conf/app/main.yaml` |
| `ExchangeConfigPath` | `conf/exchange` | `okx/main` | `$HFT_ROOT_PATH/conf/exchange/okx/main.yaml` |
| `StrategyConfigPath` | `conf/strategy` | `static_positions/main` | `$HFT_ROOT_PATH/conf/strategy/static_positions/main.yaml` |
| `ExecutorConfigPath` | `conf/executor` | `smart/default` | `$HFT_ROOT_PATH/conf/executor/smart/default.yaml` |

## `ExchangeConfigPathGroup`

App 需要“引用多个交易所配置”。相比简单的 `list[ExchangeConfigPath]`，`ExchangeConfigPathGroup` 额外提供：

- **选择器（selector）语义**：`*`、排除 `!`、通配匹配
- **运行时过滤**：在同一份 App 配置上按需筛选一部分 exchange（例如 demo、回测、线上分流）
- **分组视图**：按交易所类型（如 `okx`/`binance`）分组返回

### YAML 表示

输入是 `list[str]`（每个元素都是一个 selector）：

```yaml
exchanges:
  - "*"           # 包含全部 exchange 配置
  - "!okx/test"   # 排除某个（或某类）配置
  - "okx/main"    # 也可以显式包含单个
  - "binance/*"   # 支持通配
```

### selector 语法

- `pattern`：包含匹配（include）
- `!pattern`：排除匹配（exclude）
- `*`：匹配全部
- `pattern` 支持类 shell 通配（建议 `fnmatch` 语义），例如 `okx/*`、`*/main`

### selector 求值规则（无歧义）

给定“所有可用 exchange 配置 ID 集合”为 `ALL_IDS`：

1. 若 `selectors` 为空列表：等价于 `["*"]`（即默认包含全部）
2. 若 `selectors` 非空且**全部**为 exclude（每条都以 `!` 开头）：等价于 `["*"] + selectors`（即“全量后排除”）
3. 其它情况按顺序逐条应用 selector（初始结果集为空）：
   - include：将匹配到的 ID 加入结果集
   - exclude：将匹配到的 ID 从结果集中移除

该规则允许形如 `["*", "!okx/test"]` 的“先全量、后排除”，也允许仅列出白名单（不写 `*`）。

### API（建议）

> 说明：以下函数返回 `ExchangeConfigPath`（配置引用），并不直接返回 Exchange Listener。

- `get_id_map(id_filter: str = "*") -> dict[str, ExchangeConfigPath]`
  - 返回 `{exchange_config_id: ExchangeConfigPath}` 映射
  - `id_filter` 与 selectors 使用同一套语法；当 `id_filter` 为空字符串时建议视为 `*`
- `get_grouped_id_map(id_filter: str = "*", group_filter: str = "*") -> dict[str, list[str]]`
  - 返回 `{exchange_class_id: [exchange_config_id, ...]}`
- `get_grouped_map(id_filter: str = "*", group_filter: str = "*") -> dict[str, list[ExchangeConfigPath]]`
  - 返回 `{exchange_class_id: [ExchangeConfigPath, ...]}`

其中 `exchange_class_id` 的定义必须明确且稳定；推荐定义为：

- `exchange_class_id = exchange_config_id.split("/", 1)[0]`

### 缓存与性能建议

为避免频繁 I/O 与重复计算：

- 扫描 `conf/exchange` 目录得到 `ALL_IDS` 需要缓存（进程级一次即可）
- `get_id_map/get_grouped_*` 建议使用 `@lru_cache`（或等价缓存）基于 `id_filter/group_filter` 缓存结果
- `ExchangeConfigPath.instance` 应缓存解析后的 config（避免每次访问都读取 YAML）

## 相关文档

- [app-config.md](app-config.md) - App 配置字段与示例
- [listener.md](listener.md) - Listener cache 与恢复流程（如何使用 ConfigPath 获取 config）
- [architecture.md](architecture.md) - 全局架构与加载流程
