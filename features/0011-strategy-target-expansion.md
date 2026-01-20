# Feature 0011: Strategy Target 展开式与去特殊化

> **状态**：全部通过

## 概述

重构 Strategy 的 target 配置机制，实现：
1. **命名变更**：`keep_positions` → `static_positions`（更准确反映"静态仓位策略"）
2. **展开式写法**：引入 `target_pairs` + `target` 机制（类似 Executor 的 `order_levels` + `order`）
3. **去特殊化**：Strategy 输出为通用字典字段；输出字段以 list 口径注入到 Executor 的 `strategies[...]` namespace（当前单策略列表长度为 1）
4. **显式聚合**：Executor 可在 `vars/condition` 中显式聚合 `strategies[...]`（统一使用 `strategies["field"]` 访问）

## 动机

### 当前问题

**问题 1：命名不准确**
- `keep_positions` 名称暗示"保持仓位"，但实际是"维持目标仓位"
- `static_positions` 更准确地反映"静态仓位策略"的含义

**问题 2：配置重复**
```yaml
targets:
  - exchange_class: okx
    symbol: BTC/USDT
    position_usd: 1000
    speed: 0.1
  - exchange_class: okx
    symbol: ETH/USDT
    position_usd: 1000  # 重复
    speed: 0.1          # 重复
  - exchange_class: okx
    symbol: SOL/USDT
    position_usd: 1000  # 重复
    speed: 0.1          # 重复
```

**问题 3：字段特殊化**
- `position_usd`/`speed` 在 Strategy 和 Executor 中被当作"特殊变量"
- Executor 直接读取这些字段，缺乏灵活性
- 无法自定义聚合方式（sum/mean/max/weighted）

### 设计目标

1. **统一性**：Strategy 和 Executor 都使用通用字典输出，没有特殊字段
2. **灵活性**：Executor 可以自由选择如何聚合 `strategies[...]` 列表（当前单策略列表长度为 1）
3. **可扩展性**：Strategy 可以输出任意字段，不局限于 position_usd/speed
4. **简洁性**：展开式写法减少重复配置

---

## 重大变更

### 变更 1：命名变更

```yaml
# 旧命名
class_name: keep_positions

# 新命名
class_name: static_positions
```

**理由**：
- `keep_positions` 暗示"保持不变"，但实际是"维持目标仓位"
- `static_positions` 更准确：目标仓位是静态配置的（相对于动态策略）

### 变更 2：展开式写法

类似于 Executor 的 `order_levels` + `order` 机制，引入 `target_pairs` + `target`。

**旧写法（targets 列表）**：
```yaml
class_name: static_positions

targets:
  - exchange_class: okx
    symbol: BTC/USDT
    position_usd: 1000
    speed: 0.1
  - exchange_class: okx
    symbol: ETH/USDT
    position_usd: 1000
    speed: 0.1
  - exchange_class: okx
    symbol: SOL/USDT
    position_usd: 1000
    speed: 0.1
```

**新写法（target_pairs + target）**：
```yaml
class_name: static_positions

target_pairs:
  - BTC/USDT  # string 简写：exchange="*", exchange_class="*", symbol=BTC/USDT
  - ETH/USDT
  - SOL/USDT

target:
  position_usd: 1000
  speed: 0.1
```

**支持的 target_pairs 格式**：

```yaml
target_pairs:
  # 格式 1：string 简写
  - ETH/USDT  # exchange="*", exchange_class="*", symbol=ETH/USDT

  # 格式 2：指定 exchange_class
  - symbol: ETH/USDT
    exchange_class: okx

  # 格式 3：指定特定交易所实例
  - symbol: ETH/USDT
    exchange: okx/main

  # 格式 4：完整配置
  - symbol: ETH/USDT
    exchange_class: okx
    exchange: okx/main
```

**合并规则（避免误用）**：
- `target` 是默认模板（defaults）。
- `target_pairs` 的单项（dict 或 string 简写展开后的 dict）会覆盖 `target` 的同名字段。
- string 简写会强制带上 `exchange="*"` / `exchange_class="*"`，因此如果你想把 `exchange_class/exchange` 写在 `target:` 模板里生效，请改用 dict 格式的 `target_pairs`（并在单项里显式写 `exchange_class/exchange`）。

### 变更 3：去特殊化（核心变更）

**核心理念**：`position_usd`/`speed` 等字段不再是特殊变量，而是普通的通用字典字段。

#### 旧设计：字段特殊化

```python
# Strategy 输出
{("okx/main", "BTC/USDT"): {"position_usd": 100, "speed": 0.1}}

# Executor 直接读取特殊字段
position_usd = target_dict["position_usd"]  # 隐式假设存在
speed = target_dict["speed"]
```

**问题**：
- `position_usd`/`speed` 被硬编码为特殊字段
- Executor 无法自定义聚合方式
- （如未来恢复多策略）多个 Strategy 的输出如何合并？（sum? mean? max?）

#### 新设计：去特殊化

```python
# Strategy 输出（通用字典）
{("okx/main", "BTC/USDT"): {"position_usd": 100, "speed": 0.1, "custom_field": 42}}

# 输出字段以 list 口径注入到 strategies namespace（当前单策略列表长度为 1）
{("okx/main", "BTC/USDT"): {"position_usd": [100], "speed": [0.1], "custom_field": [42]}}

# Executor 显式聚合
vars:
  - name: position_usd
    value: sum(strategies["position_usd"])  # [100] → 100
  - name: speed
    value: avg(strategies["speed"])         # [0.1] → 0.1
```

**注意（避免误解）**：
- `strategies` 是聚合后的 dict namespace；文档规范统一使用 `strategies["field"]`，不要使用 `strategies.field`。
- 原因：字段名可能与 dict 方法/属性（如 `keys/items/get`）冲突；且非合法标识符字段名（含 `-`、`.`、空格、数字开头等）无法用点号访问。
- 当前实现中，BaseExecutor 会先用默认聚合从 `strategies_data` 计算 `target_usd/speed/delta_usd` 并决定是否执行；`vars/condition` 主要用于"条件判断 + 动态参数"，不会反向覆盖 BaseExecutor 的目标仓位计算逻辑。

**好处**：
- **灵活聚合**：Executor 可以选择 sum/mean/max/min/weighted
- **可扩展**：Strategy 可以输出任意字段
- **统一机制**：Strategy 和 Executor 都是通用字典

#### 完整示例对比

**旧配置**：

```yaml
# conf/strategy/old.yaml
class_name: keep_positions

targets:
  - exchange_class: okx
    symbol: BTC/USDT
    position_usd: 1000
    speed: 0.1
  - exchange_class: okx
    symbol: ETH/USDT
    position_usd: 500
    speed: 0.1

# conf/executor/old.yaml
class_name: limit

# Executor 隐式读取 position_usd/speed
# 内部代码：delta_usd = position_usd - current_position_usd
```

**新配置**：

```yaml
# conf/strategy/new.yaml
class_name: static_positions

target_pairs:
  - BTC/USDT
  - ETH/USDT

target:
  position_usd: 1000  # 普通字段，非特殊变量
  speed: 0.1          # 普通字段，非特殊变量

# conf/executor/new.yaml
class_name: limit

vars:
  # 显式聚合 Strategy 输出
  - name: position_usd
    value: sum(strategies["position_usd"])
  - name: speed
    value: avg(strategies["speed"])
  - name: delta_usd
    value: position_usd - current_position_usd
```

---

## 设计细节

### 1. target_pairs 展开机制

```python
# 配置
target_pairs = ["BTC/USDT", "ETH/USDT"]
target = {"position_usd": 1000, "speed": 0.1}

# 展开为
targets = [
    {"symbol": "BTC/USDT", "exchange_class": "*", "exchange": "*", "position_usd": 1000, "speed": 0.1},
    {"symbol": "ETH/USDT", "exchange_class": "*", "exchange": "*", "position_usd": 1000, "speed": 0.1},
]
```

#### 展开层级（必须澄清）

Strategy 的 “展开” 分为两层，避免与 Executor 的 `order_levels` 展开混淆：

1) **配置层展开（target_pairs → targets）**
- 发生时机：配置加载（Pydantic `model_validator`）
- 输入：`target_pairs + target`
- 输出：`targets: list[TargetDefinition]`
- 结果特征：
  - 仍允许包含通配/模式：`exchange: "*"` / `exchange_class: "*"`（或更复杂的 `fnmatch` pattern）
  - 只是“写法去重 + 生成 targets 列表”，不涉及真实 Exchange 实例

2) **运行时展开（targets[*] 匹配 → 绑定到具体 exchange 实例）**
- 发生时机：Strategy 每次计算输出时（例如 `StaticPositionsStrategy.get_target_positions_usd()`）
- 输入：`targets[*]`（其中 `exchange/exchange_class` 可能是 pattern）
- 输出：按 **具体 exchange 实例** 展开的 StrategyOutput key：`(exchange_path, symbol)`
- 结果特征：
  - `*`/pattern 会被“展开/匹配掉”：最终输出里是 concrete `exchange_path`，不会再出现 `*`
  - 同一个 target 可能命中多个 exchange 实例，因此会产生多条 `(exchange_path, symbol)` 输出

与 Executor 的 `order_levels` 展开类比（用于理解“局部变量”）：
- `order_levels: N` 会把单个 `order` template 展开为 `level ∈ {-N, ..., -1, 1, ..., N}`（局部变量名为 `level`）
- `entry_order_levels/exit_order_levels` 在各自循环内提供 `entry_level/exit_level`
- Strategy 的 targets 展开不引入 `level` 这种数值索引变量；它的“展开维度”是 **exchange 实例绑定**：
  - 每个展开后的条目都拥有明确的 `(exchange_path, exchange_class, symbol)` 绑定关系

### 2. strategies namespace 聚合机制（单策略）

```python
# Strategy 输出（单策略）：
{("okx/main", "BTC/USDT"): {"position_usd": 100, "speed": 0.1, "urgency": 0.8}}

# 注入到 strategies namespace（list 口径）：
{
    ("okx/main", "BTC/USDT"): {
        "position_usd": [100],
        "speed": [0.1],
        "urgency": [0.8]
    }
}
```

**聚合规则**：
- 相同 `(exchange_path, symbol)` 的输出合并
- 相同字段的值聚合为列表
- 单个 Strategy 的字段值也包装为列表（保持一致性）

#### 运行时展开后的数据流（为什么输出里不应再出现 `*`）

1) Strategy 输出前：`targets[*]` 仍可能包含 `exchange="*"` / `exchange_class="*"` 等 pattern
2) Strategy 输出时：对每个 target 做“匹配 + 展开”，把 pattern 绑定到 app 当前已加载的 exchange 实例：
   - 得到 concrete `exchange_path`（来自 exchange config path）
   - 得到 concrete `exchange_class`（来自 exchange 的 class_name）
   - 得到 `symbol`
3) 对每个 concrete `(exchange_path, symbol)`：
   - 收集该 exchange/symbol 上下文变量（requires/vars/indicator 等）
   - 计算并输出字段 dict（如 `position_usd/speed/...`）
4) 聚合层：把 Strategy 的输出按 `(exchange_path, symbol)` 注入到 `strategies["field"] = [v]` 列表（单策略列表长度为 1）
5) Executor：每个 tick 对每个 `(exchange_path, symbol)` 执行一次处理，并把 `strategies` 作为 namespace 注入表达式上下文

### 3. Strategy condition 语义（全局 + target 级）

为与 Executor 的 `condition` / Order 的 `condition` 语义保持一致，Strategy 的配置也支持 `condition` 表达式门控：

- **全局 `condition`**：定义在 Strategy config 顶层（默认 `null`，等价 True）。
- **target 级 `condition`**：定义在 `target` / `targets[*]` 内（默认 `null`，等价 True）。
- **生效规则**：
  - 全局 `condition`：只求值一次（使用一个代表性的 `(exchange_path, symbol)` 上下文）；为 False/异常/None 时直接返回空 `{}`。
  - target 级 `condition`：对每个展开后的 `(exchange_path, symbol)` 分别求值；为 False/异常/None 时仅跳过该 target。
  - 若你需要“全局条件对每个 target 都用各自上下文求值”的行为，请把表达式下放到 target 级 `condition`（多策略当前不支持）。

示例：

```yaml
class_name: static_positions

condition: equation_usd > 1000  # 全局 gate（默认 null = True）

targets:
  - symbol: BTC/USDT:USDT
    condition: rsi[-1] < 30  # target gate（默认 null = True）
    position_usd: 1000
    speed: 0.2
```

备注：
- `target_pairs + target` 展开式中，`condition` 也属于普通字段：可写在 `target:` 模板中，也可在 `target_pairs` 的单项 dict 中覆盖（与其他字段一致）。
- 已实现：Strategy 在运行时对全局/target condition 做 fail-safe 门控（异常/None 视为 False → 跳过）。

### 4. Executor 隔离机制

**关键问题**：Executor 的 `execute_delta()` 是否按 `(exchange_path, symbol)` 隔离调用？

**答案**：是的。

```python
# BaseExecutor._process_targets()
for (exchange_path, symbol), strategies_data in aggregated_targets.items():
    # 为每个 (exchange_path, symbol) 调用一次 _process_single_target()
    # 最终每个 symbol 都会触发一次 execute_delta(exchange, symbol, delta_usd, speed, current_price)
    await executor._process_single_target(exchange, symbol, strategies_data)
```

**tracking orders 隔离**：
- 每个 Executor 实例维护自己的订单追踪状态
- 订单追踪按 `(exchange_path, symbol)` 隔离
- 不同交易对的订单互不干扰

---

## 向后兼容性

### 1. 命名兼容

> **注意**：别名支持已在清理旧代码时移除，不再提供向后兼容。
> 所有配置必须使用 `class_name: static_positions`。

**迁移要求**：
- 所有配置文件中的 `class_name: keep_positions` 必须改为 `class_name: static_positions`
- 目录 `conf/strategy/keep_positions/` 已重命名为 `conf/strategy/static_positions/`

### 2. 配置兼容

**支持三种写法**：

```yaml
# 写法 1：旧写法（targets 列表）- 完全兼容
class_name: static_positions
targets:
  - symbol: BTC/USDT
    position_usd: 1000
    speed: 0.1

# 写法 2：新写法（target_pairs + target）- 推荐
class_name: static_positions
target_pairs:
  - BTC/USDT
target:
  position_usd: 1000
  speed: 0.1

# 写法 3：混合写法（同时存在）
class_name: static_positions
target_pairs:
  - BTC/USDT
target:
  position_usd: 1000
targets:  # 额外的 targets 会追加
  - symbol: ETH/USDT
    position_usd: 500
```

### 3. Executor 兼容性

**关键问题**：现有 Executor 如何兼容新的聚合机制？

**方案**：BaseExecutor 在 `_process_single_target()` 中提供默认聚合行为（向后兼容）

```python
# BaseExecutor._process_single_target() 当前默认聚合（摘录语义）
target_usd = sum(strategies_data.get("position_usd", []))
# speed: 按 |position_usd| 加权平均（缺省时 fallback=0.5）
```

**Executor 配置**：

```yaml
# 方式 1：使用默认聚合（向后兼容）
class_name: limit
# 不写 vars，使用默认聚合

# 方式 2：显式聚合（推荐）
class_name: limit
vars:
  - name: position_usd
    value: sum(strategies["position_usd"])
  - name: speed
    value: avg(strategies["speed"])
```

**注意（避免误解）**：
- “方式 2：显式聚合”当前主要用于 `condition/vars` 表达式（例如做风控 gate、动态参数）；不会改变 BaseExecutor 用于实际下单的默认 `target_usd/speed` 聚合口径（除非后续新增 override 机制）。

---

## 任务列表

### Phase 1: 命名变更（P2）

- [x] 重命名 `KeepPositionsStrategy` → `StaticPositionsStrategy`（已通过）
- [x] 重命名 `keep_positions.py` → `static_positions.py`（已通过）
- [x] ~~添加 `keep_positions` 别名支持~~ → 已移除，不再需要向后兼容（已通过）
- [x] ~~添加 DeprecationWarning~~ → 已移除别名类（已通过）
- [x] 更新配置注册（hft/bin/config.py）（已通过）

### Phase 2: 展开式写法（P1）

- [x] `StaticPositionsStrategyConfig` 添加 `target_pairs` 字段（已通过）
- [x] `StaticPositionsStrategyConfig` 添加 `target` 字段（已通过）
- [x] 实现 `target_pairs` 展开逻辑（model_validator）（已通过）
- [x] 支持 string 简写格式（`"BTC/USDT"` → `{"symbol": "BTC/USDT", "exchange_class": "*", "exchange": "*"}`）（已通过）
- [x] 支持 dict 格式（`{"symbol": "BTC/USDT", "exchange_class": "okx"}`）（已通过）
- [x] 支持混合写法（`target_pairs` + `targets` 同时存在）（已通过）

### Phase 3: 去特殊化（P0）

- [x] 聚合层已支持 `strategies["field"]` 的 list 口径注入（已通过）
  - 输出格式：`{(exchange_path, symbol): {"field": [val1, val2], ...}}`
- [x] `BaseExecutor` 已注入 `strategies` namespace（已通过）
- [x] `BaseExecutor._process_single_target()` 已有默认聚合逻辑（已通过）
- [x] 现有 Executor 已兼容 `strategies` namespace（已通过）
  - MarketExecutor、LimitExecutor、AvellanedaStoikovExecutor、PCAExecutor、SmartExecutor

### Phase 4: 测试和文档（P2）

- [x] 添加 `target_pairs` 展开测试（已通过）
- [x] 添加向后兼容性测试（已通过）
- [x] Strategy 支持全局 `condition` expr（默认 null=True；False 忽略所有 targets）（已通过）
- [x] `TargetDefinition` 支持 target 级 `condition` expr（默认 null=True；False 忽略该 target）（已通过）
- [x] 单元测试：覆盖全局/target `condition` 的 True/False/异常 fail-safe 行为（已通过）
- [x] 更新 `examples/003-static-positions-strategy.md`（已通过）
  - 全部使用 `static_positions`
  - 新增 `target_pairs` + `target` 展开式写法章节
  - 新增 condition 门控章节
  - 使用 `strategies["field"]` 语法访问聚合字段
- [x] 更新所有示例配置文件（已通过）
  - `conf/strategy/static_positions/*.yaml` 使用 `target_pairs` 展开式
  - `conf/strategy/demo/static_positions_eth.yaml` 使用 `target_pairs` 展开式
  - `conf/app/demo/*_static_positions.yaml` 文件重命名

---

## 影响范围

### 核心模块

| 模块 | 影响 | 说明 |
|------|------|------|
| `hft/strategy/static_positions.py` | **重大** | 主实现：策略/配置 + `target_pairs` 展开逻辑 |
| `hft/strategy/group.py` | **重大** | 修改聚合逻辑，输出列表格式 |
| `hft/executor/base.py` | **重大** | 注入 `strategies` namespace，添加默认聚合 |
| `hft/executor/*_executor.py` | **中等** | 修改为使用 `strategies` namespace |
| `conf/strategy/**/*.yaml` | **中等** | 更新 `class_name` 和配置格式 |
| `conf/executor/**/*.yaml` | **中等** | 添加显式聚合 vars |

### 测试文件

| 文件 | 影响 | 说明 |
|------|------|------|
| `tests/test_strategy_*.py` | **中等** | 更新测试用例 |
| `tests/test_executor_*.py` | **中等** | 更新测试用例 |
| `tests/test_integration_*.py` | **小** | 更新集成测试 |

---

## 相关文档

- [Feature 0008: Strategy 数据驱动](./0008-strategy-data-driven.md)
- [Feature 0010: Executor vars 系统](./0010-executor-vars-system.md)
- [docs/strategy.md](../docs/strategy.md)
- [docs/executor.md](../docs/executor.md)
- [Example 003: StaticPositionsStrategy 配置详解](../examples/003-static-positions-strategy.md)
