# Scope 系统执行流程详解

本文档详细说明 Scope 系统在 AppCore、Strategy、Executor 中的完整执行流程。

## 1. 初始化阶段（AppCore）

### 1.1 创建 ScopeManager

```python
# hft/core/app/base.py
class AppCore:
    def __init__(self, config: AppConfig):
        # 创建 Scope 管理器
        from ..scope.manager import ScopeManager
        self.scope_manager = ScopeManager()
```

### 1.2 Strategy 注册自定义 Scope 类

```python
# hft/strategy/base.py
class BaseStrategy:
    def on_start(self):
        # 注册自定义 Scope 类
        self._register_custom_scopes()
```

**注册示例**：
```python
def _register_custom_scopes(self):
    from ..core.scope.manager import ScopeManager
    scope_manager: ScopeManager = self.root.scope_manager

    # 注册自定义 Scope 类
    scope_manager.register_scope_class(
        "trading_pair_class_group",
        TradingPairClassGroupScope
    )
```

## 2. 每次 Tick 的执行流程

### 2.1 Strategy 构建 Scope 树

**调用入口**：`BaseStrategy._build_scope_trees()`

**重要概念**：Link 定义的是**层级关系**，而非单一路径。展开时会遍历每一层的所有 children。

**执行步骤**：

1. 遍历每个 link（如 `["global", "exchange", "trading_pair"]`）
2. 从根节点开始，逐层展开：
   - 第一层：创建/获取根节点（如 `global`）
   - 第二层：遍历根节点的所有 children（如所有 `exchange`）
   - 第三层：对每个 parent，遍历其所有 children（如每个 `exchange` 的所有 `trading_pair`）
3. 建立 parent-child 关系
4. 形成完整的树结构

**代码示例**：
```python
def _build_scope_trees(self):
    for link in self.config.links:
        # link = ["global", "exchange", "trading_pair"]
        parent_scope = None

        for scope_class_id in link:
            # 获取或创建 Scope 实例
            scope = self.scope_manager.get_or_create(
                scope_class_id=scope_class_id,
                scope_instance_id=self._get_instance_id(scope_class_id),
                parent=parent_scope
            )
            parent_scope = scope
```

### 2.2 Indicator 注入

**时机**：在计算 vars 之前

**执行步骤**：
1. 根据 Indicator 的 `scope_level` 确定注入层级
2. 调用 Indicator 的 `calculate_vars()` 获取变量
3. 将变量注入到对应的 Scope 实例

**代码示例**：
```python
# 在 trading_pair 层级注入 ticker 变量
ticker_indicator.scope_level = "trading_pair"
vars_dict = ticker_indicator.calculate_vars(direction=1)
# vars_dict = {"mid_price": 50000, "best_bid": 49999, ...}

trading_pair_scope.set_var("mid_price", 50000)
trading_pair_scope.set_var("best_bid", 49999)
```

### 2.3 计算 vars

**时机**：在 Indicator 注入之后

**计算顺序**：沿着 link 从根到叶依次计算

**执行步骤**：
1. 从根节点（如 `global`）开始
2. 依次计算每个 Scope 的 vars
3. 子节点可以访问父节点的 vars（通过 ChainMap）
4. 父节点可以访问子节点的 indicator 变量（通过 `children` 字典）

**示例**：
```yaml
# global scope 计算
scopes:
  global:
    vars:
      - name: total_amount
        value: sum([scope["amount"] for scope in children.values()])
```

### 2.4 输出 targets

**调用入口**：`BaseStrategy.get_output()`

**返回格式**：
```python
{
    ("okx/main", "BTC/USDT"): {
        "position_usd": 1000,
        "speed": 0.5
    }
}
```

## 3. Executor 中的使用

### 3.1 接收 Strategy 输出

Executor 通过 `strategies` namespace 接收多个 Strategy 的聚合输出。

**示例**：
```python
# strategies namespace
strategies = {
    "position_usd": [1000, 2000],  # 来自两个 Strategy
    "speed": [0.5, 0.3]
}
```

### 3.2 Executor 计算顺序

1. 收集 Indicator 变量
2. 注入 `strategies` namespace
3. 计算 Executor 的 vars
4. 执行订单逻辑

## 4. 完整示例

详细的端到端示例请参考 `examples/004-market-neutral-positions-strategy.md`。
