# Claude Code 项目指南

本文档定义了项目的设计原则和编码约定，供 Claude Code 参考。

> **维护说明**: 当发现新的有价值的设计原则或约定时，应更新本文档；当某些内容不再适用时，应及时清理。保持文档与实际代码实践同步。

## 设计原则

### 1. DRY (Don't Repeat Yourself)

公共逻辑提取到基类或工具方法中，避免代码重复。

```python
# Good: 基类提供公共方法
class BaseExecutor:
    def usd_to_amount(self, exchange, symbol, usd, price) -> float:
        """USD 转合约数量 - 所有执行器共用"""
        base_amount = usd / price
        contract_size = exchange.get_contract_size(symbol)
        return base_amount / contract_size

class MarketExecutor(BaseExecutor):
    async def execute_delta(self, ...):
        amount = self.usd_to_amount(exchange, symbol, delta_usd, price)

class LimitExecutor(BaseExecutor):
    async def execute_delta(self, ...):
        amount = self.usd_to_amount(exchange, symbol, level.per_order_usd, price)
```

### 2. 模板方法模式 (Template Method)

基类定义算法骨架，子类实现具体步骤。

```python
class BaseExecutor:
    async def on_tick(self):
        """算法骨架 - 不变"""
        targets = self.strategy_group.get_aggregated_targets()
        await self._process_targets(targets)

    @abstractmethod
    async def execute_delta(self, ...):
        """具体执行 - 子类实现"""
        ...
```

### 3. 配置驱动 (Configuration-Driven)

通过配置文件定义行为，运行时动态加载。

```
conf/
├── app/
│   └── market_making.yaml      # 应用配置
├── exchange/
│   └── okx_demo.yaml           # 交易所配置
├── strategy/
│   └── keep_positions/
│       └── neutral_btc.yaml    # 策略配置
└── executor/
    └── limit/
        └── simple.yaml         # 执行器配置
```

**BaseConfig 模式**:
```python
class BaseConfig[T](BaseModel):
    class_name: ClassVar[str]       # 类标识
    class_dir: ClassVar[str]        # 配置目录

    @classmethod
    def load(cls, path: str) -> Self:
        """从 YAML 加载配置"""

    @cached_property
    def instance(self) -> T:
        """延迟实例化"""
        return self.get_class_type()(self)
```

### 4. 组合优于继承 (Composition over Inheritance)

通过组合实现灵活的功能组合。

```yaml
# 策略定义"目标是什么"
# conf/strategy/keep_positions/neutral_btc.yaml
positions_usd:
  BTC/USDT:USDT: 0    # 中性仓位

# 执行器定义"如何执行"
# conf/executor/limit/simple.yaml
orders:
  - spread: 0.001     # 挂单距离
    per_order_usd: 50

# 应用组合两者
# conf/app/market_making.yaml
strategies:
  - keep_positions/neutral_btc
executor: limit/simple
```

同一个策略可以搭配不同执行器:
- `keep_positions + market` = 快速调仓
- `keep_positions + limit` = 做市挂单

### 5. 单一职责原则 (Single Responsibility)

每个类只有一个改变的理由。

| 类 | 职责 |
|---|---|
| `BaseExchange` | 交易所 API 封装 |
| `ExchangeGroup` | 多账户分组管理 |
| `BaseStrategy` | 计算目标仓位 |
| `StrategyGroup` | 聚合多策略目标 |
| `BaseExecutor` | 执行交易逻辑 |
| `BaseDataSource` | 外部数据获取（API、WebSocket） |
| `Data` | ClickHouse 数据库读写 |
| `Listener` | 生命周期管理 |

### 6. Listener 构造函数原则

**Listener 子类的构造函数只能接受以下参数**：
1. **无参数** - 最简单的情况
2. **config 对象** - 配置信息
3. **简单配置值** - 如 interval, name 等

**禁止传入其他 Listener 实例**（如 exchange, strategy），应通过树形结构动态获取。

```python
# Good: 无参数或只接受 config
class StrategyGroup(Listener):
    def __init__(self):
        super().__init__("StrategyGroup", interval=60.0)

class BaseExecutor(Listener):
    def __init__(self, config: BaseExecutorConfig):
        super().__init__(name=config.path, interval=config.interval)
        self.config = config

# Good: 通过树形结构获取依赖
@property
def exchange_group(self) -> "ExchangeGroup":
    return self.root.exchange_group  # 从根节点获取

@property
def exchange(self) -> "BaseExchange":
    return self.parent  # 从父节点获取

# Bad: 构造函数传入其他 Listener
class WatchListener(Listener):
    def __init__(self, exchange: BaseExchange):  # 不要这样做！
        self.exchange = exchange
```

这样设计的好处：
- 避免循环依赖
- pickle 恢复时不会出 bug（依赖是动态获取的）
- 符合 Listener 树形架构
- 便于测试和替换

### 7. GroupListener - 动态子节点管理

需要动态创建/删除子节点的 Listener 应继承 `GroupListener`。

```python
class ExchangeBalanceListener(GroupListener):
    def sync_children_params(self) -> dict[str, Any]:
        """声明需要哪些 children（返回 {name: param}）"""
        exchange = self.parent
        params = {}
        for key in exchange.config.ccxt_instances.keys():
            params[f"watch-{key}"] = {"key": key, "type": "watch"}
            params[f"fetch-{key}"] = {"key": key, "type": "fetch"}
        return params

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        """根据参数创建 child"""
        if param["type"] == "watch":
            return ExchangeBalanceWatchListener(param["key"])
        return ExchangeBalanceFetchListener(param["key"])
```

GroupListener 特点：
- 自身可 pickle，但不保存 children（启动时重建）
- `on_tick()` 自动同步：缺少的创建，多余的删除
- 子类只需实现 `sync_children_params()` 和 `create_dynamic_child()`

### 8. Listener 树形架构

所有组件继承 Listener，形成树形结构，统一生命周期管理。

```python
class Listener:
    parent: Optional["Listener"]
    children: dict[str, "Listener"]

    async def start(self):
        await self.on_start()
        for child in self.children.values():
            await child.start()

    @cached_property
    def root(self) -> "Listener":
        """获取根节点（AppCore）"""
        return self.parent.root if self.parent else self
```

### 8. 级联退出

子节点完成 → 父节点检测 → 向上传递。

```python
# Strategy 完成
async def on_tick(self) -> bool:
    if self.target_reached:
        return True  # 触发退出

# StrategyGroup 检测
@property
def is_finished(self) -> bool:
    return self._initialized and len(self.children) == 0

# AppCore 响应
async def on_tick(self) -> bool:
    if self.strategy_group.is_finished:
        return True  # 程序退出
```

## 编码约定

### 命名规范

| 类型 | 规范 | 示例 |
|---|---|---|
| 类名 | PascalCase | `BaseExecutor`, `LimitOrderLevel` |
| 方法/变量 | snake_case | `execute_delta`, `per_order_usd` |
| 常量 | UPPER_SNAKE | `DEFAULT_INTERVAL` |
| 私有成员 | 前缀 `_` | `_active_orders`, `_initialized` |
| 配置路径 | 小写/下划线 | `keep_positions/neutral_btc` |

### 类型注解

使用完整的类型注解，提高代码可读性。

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange

async def execute_delta(
    self,
    exchange: "BaseExchange",
    symbol: str,
    delta_usd: float,
) -> ExecutionResult:
    ...
```

### 文档字符串

关键类和方法需要文档字符串。

```python
def usd_to_amount(self, exchange, symbol, usd, price) -> float:
    """
    将 USD 价值转换为下单数量（合约数量）

    计算公式：
        base_amount = usd / price
        amount = base_amount / contract_size

    Args:
        exchange: 交易所实例
        symbol: 交易对
        usd: USD 价值
        price: 当前价格

    Returns:
        合约数量
    """
```

### 日志规范

使用 `self.logger`（Listener 自带），logger name 已包含组件标识，无需重复。

```python
# Good: logger name 已标识来源
self.logger.info("%s %s: filled=%.6f @ %.2f",
    side.upper(), symbol, filled_amount, price)

self.logger.warning("Failed to cancel order %s: %s", order_id, error)

# Bad: 重复标识
self.logger.info("[%s] %s %s: ...", exchange.name, ...)  # exchange.name 多余
```

### 异常处理

捕获具体异常，记录日志，返回错误结果而非抛出。

```python
try:
    order = await exchange.create_order(...)
except Exception as e:
    self.logger.exception("[%s] Error executing order: %s", exchange.name, e)
    return ExecutionResult(success=False, error=str(e))
```

## 配置文件约定

### YAML 结构

```yaml
# 必填: 类标识
class_name: limit

# 通用配置
interval: 0.5

# 特定配置
orders:
  - spread: 0.001
    refresh_tolerance: 0.5
    timeout: 30
    per_order_usd: 50
```

### 配置路径

配置通过 `class_name/path` 引用：
- `executor: limit/simple` → `conf/executor/limit/simple.yaml`
- `strategies: [keep_positions/main]` → `conf/strategy/keep_positions/main.yaml`

## 单位约定

| 概念 | 单位 | 说明 |
|---|---|---|
| `position_usd` | USD | 仓位价值 |
| `per_order_usd` | USD | 单笔订单价值 |
| `amount` | 合约数量 | 下单数量（已除以 contract_size） |
| `price` | USDT | 价格 |
| `spread` | 比例 | 如 0.001 = 0.1% |
| `speed` | [0, 1] | 执行紧急度 |
| `interval` | 秒 | 时间间隔 |

### 合约数量转换

```python
# 获取仓位时：contracts * contract_size = base_amount
amount = float(position['contracts']) * exchange.get_contract_size(symbol)

# 下单时：base_amount / contract_size = contracts
base_amount = usd / price
contracts = base_amount / exchange.get_contract_size(symbol)
```

## 测试约定

### 调试模式

```yaml
# conf/app/market_making.yaml
debug: true           # 不实际下单
max_duration: 60.0    # 60秒后自动退出
```

### 运行命令

```bash
# 运行应用（会提示输入密码）
hft run main app

# 指定密码（用于解密配置中的 API 凭证）
hft -p mypassword run main app

# 指定配置
hft run main market_making
```

### 密码与加密

配置文件中的敏感信息（api_key, api_secret, passphrase）使用 Fernet 加密存储。
`-p` 参数指定解密密码，必须与加密时使用的密码一致，否则解密失败会跳过该交易所。
