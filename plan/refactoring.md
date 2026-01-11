# 重构计划

## 1. Executor 模块重构 (P1) - 部分完成

### 已完成 ✓

1. **配置系统重构**
   - ✓ 创建 `hft/executor/config.py` - BaseExecutorConfig, MarketExecutorConfig, LimitExecutorConfig
   - ✓ 执行器通过 config 对象初始化
   - ✓ 支持 `conf/executor/` 配置文件

2. **执行器拆分**
   - ✓ `hft/executor/market.py` - MarketExecutor
   - ✓ `hft/executor/limit.py` - LimitExecutor
   - ✓ `hft/executor/pca.py` - PCAExecutor

3. **公共逻辑提取**
   - ✓ `usd_to_amount()` - USD 转合约数量
   - ✓ `manage_limit_orders()` - 统一限价单管理

### 待完成

1. **OrderManager 提取** (可选)
   - 将订单追踪逻辑提取到独立类
   - 统一订单状态管理

### 当前结构

```
hft/executor/
├── base.py              # BaseExecutor 基类
├── config.py            # 配置系统 ✓
├── market.py            # 市价单执行器 ✓
├── limit.py             # 限价单执行器 ✓
├── pca.py               # PCA 执行器 ✓
└── intensity.py         # 已迁移到 hft/indicator/intensity.py
```

---

## 2. Exchange 监听器重构 (P2)

### 当前问题

`hft/exchange/listeners.py` 存在以下问题：

1. **parent 链访问不安全**
   - 多处 `self.parent.parent` 访问
   - 容易 NoneType 错误

2. **重复的 exchange 属性**
   - 每个监听器都定义相似的 `exchange` 属性

### 重构方案

#### 提取基类

```python
class ExchangeChildListener(Listener):
    """Exchange 子监听器基类"""

    @property
    def exchange(self) -> "BaseExchange | None":
        """安全获取 exchange"""
        # 向上遍历找到 BaseExchange 类型的祖先
        node = self.parent
        while node is not None:
            if isinstance(node, BaseExchange):
                return node
            node = node.parent
        return None
```

---

## 3. 配置系统重构 (P2)

### 当前问题

1. **配置验证分散**
   - 部分在 pydantic model
   - 部分在实例化时

2. **默认值不一致**
   - 有些在 Field(default=...)
   - 有些在 `__init__`

### 重构方案

1. 所有验证放在 pydantic model
2. 使用 `model_validator` 做跨字段验证
3. 添加 `model_config` 统一配置

```python
class BaseExecutorConfig(BaseConfig):
    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        if self.interval <= 0:
            raise ValueError("interval must be positive")
        return self
```

---

## 4. 测试重构 (P2)

### 当前问题

1. **Mock 类分散**
   - conftest.py 有多个 Mock 类
   - 部分测试文件有自己的 Mock

2. **Fixture 重复**
   - 相似的 fixture 在多处定义

### 重构方案

```
tests/
├── conftest.py          # 公共 fixture
├── mocks/               # Mock 类集中管理
│   ├── __init__.py
│   ├── listener.py      # MockListener, MockGroupListener
│   ├── executor.py      # MockExecutor
│   └── exchange.py      # MockExchange
└── fixtures/            # 复杂 fixture
    ├── __init__.py
    └── app.py           # AppCore 相关 fixture
```

---

## 5. 日志系统重构 (P3)

### 当前问题

类似 self.logger.warning("[%s] Failed to cancel order %s: %s",
    exchange.name, order_id, error) 这种，其实不需要加上 [%s]，因为self.logger的时候已经定义了logger name了。

1. **logger 名称不统一**
   - 有些用 `self.name`
   - 有些用 `self.logger_name`

2. **日志格式不一致**
   - 有些用 `%s` 格式化
   - 有些用 f-string

### 重构方案

1. 统一使用 `self.logger`（已是属性）
2. 统一使用 `%s` 格式化（性能更好）
3. 添加结构化日志支持

```python
# 统一格式
self.logger.info(
    "Order created",
    extra={"order_id": order_id, "symbol": symbol, "side": side}
)
```
