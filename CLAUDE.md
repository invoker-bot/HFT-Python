# Claude Code 项目指南

> 当有对项目有益的新规则发现时，主动更新 CLAUDE.md
> 详细文档见 `docs/`，规划见 `features/`，问题见 `issue/`

## 核心架构

**Listener 树形结构**：所有组件继承 Listener，统一生命周期管理。
- 状态机：`STOPPED → STARTING → RUNNING → STOPPING → STOPPED`
- 依赖通过 `parent`/`root` 获取，禁止构造函数传入其他 Listener
- 详见 [docs/listener.md](docs/listener.md)

**配置驱动**：`conf/` 下 YAML 配置，`BaseConfig.load()` 加载并实例化。

**组合模式**：Strategy 定义目标 + Executor 定义执行方式，自由组合。

**插件系统**：基于 pluggy 的 Hook 机制，详见 [docs/plugin.md](docs/plugin.md)。

## 模块结构

```
hft/
├── core/           # 核心基础设施
│   ├── listener.py # Listener 基类、GroupListener
│   └── app/        # AppCore 应用核心
├── exchange/       # 交易所抽象层
├── strategy/       # 策略层（定义目标仓位）
├── executor/       # 执行层（实现交易逻辑）
├── datasource/     # 数据源层（市场数据）
├── indicator/      # 指标计算
├── plugin/         # 插件系统
└── database/       # 数据持久化
```

## 设计原则

| 原则 | 说明 |
|------|------|
| DRY | 公共逻辑提取到基类 |
| 单一职责 | 每个类只做一件事 |
| 模板方法 | 基类定义骨架，子类实现细节 |
| GroupListener | 动态子节点用 `sync_children_params()` + `create_dynamic_child()` |
| lazy_start | 资源密集型组件初始为 STOPPED，首次访问时启动 |
| HealthyData | 数据带新鲜度检查，`is_healthy()` 判断是否过期 |

## 编码约定

**命名**：
- 类 `PascalCase`，方法/变量 `snake_case`，私有 `_prefix`
- 需要初始化后调用的方法用 `medal_` 前缀

**文件命名**：模块文件名应包含类型后缀，与类名对应

| 模块 | 文件命名 | 示例 |
|------|----------|------|
| executor | `*_executor.py` | `market_executor.py` → `MarketExecutor` |
| strategy | `*_strategy.py` | `keep_positions.py` → `KeepPositionsStrategy` |
| indicator | `*_indicator.py` | `lazy_indicator.py` → `LazyIndicator` |
| datasource | `*_datasource.py` | `ticker_datasource.py` → `TickerDataSource` |
| 基类/配置 | `base.py`, `config.py`, `group.py` | 保持不变 |

**类型注解**：
- 使用完整注解，循环引用用 `TYPE_CHECKING`
- 泛型参数用 `Any`（大写，非 `any`）

**日志**：用 `self.logger`，格式用 `%s`（非 f-string）

**异常**：捕获具体异常，返回错误结果而非抛出

**导入**：
- 避免循环依赖，必要时使用延迟导入
- Plugin 在 Listener 等基类中使用延迟导入：`from ..plugin import pm`

## 注释规范

```python
# 复杂逻辑需要解释原因
# NOTE: 这里用 weakref 是因为要避免循环引用导致内存泄漏

# 待优化/待实现
# TODO: 描述
# TODO(P1): 高优先级

# 废弃代码
# DEPRECATED: 原因，将在 vX.X 移除

# 废弃模块（在模块级 docstring 中标注）
"""
模块说明

.. deprecated::
    原因，推荐使用 xxx 替代。

已知问题：
- 问题1
- 问题2
"""
```

## 单位约定

| 字段 | 单位 | 说明 |
|------|------|------|
| `*_usd` | USD | 仓位/订单价值 |
| `amount` | 合约数量 | 已除以 contract_size |
| `spread` | 比例 | 0.001 = 0.1% |
| `interval` | 秒 | 时间间隔 |

## 关键类型

**TargetPositions**：策略输出的目标仓位格式
```python
# {(exchange_path, symbol): (position_usd, speed)}
TargetPositions = dict[tuple[str, str], tuple[float, float]]
```

**AggregatedTargets**：聚合后的目标仓位（与 TargetPositions 相同格式）

## 开发流程

1. **理解全局**：先读 `docs/architecture.md`，理解相关模块
2. **修改代码**：遇到难懂代码加注释，废弃代码标 DEPRECATED，待做标 TODO
3. **写测试**：功能完成后补充单元测试，`pytest tests/ -v`
4. **更新文档**：重大变更同步更新 `docs/`
5. **新增功能**：全局规划写入 `features/`，全部完成后删除当前目录下的 `features/`，同时写入 git commit 中
6. **BUG 修复**：报告或遇到的问题写入 `issue/`，全部完成后删除当前目录下的 `issue/`，同时也写入 git commit 中

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/architecture.md](docs/architecture.md) | 整体架构概览 |
| [docs/listener.md](docs/listener.md) | Listener 状态机和生命周期 |
| [docs/plugin.md](docs/plugin.md) | 插件系统和 Hook 定义 |
| [docs/datasource.md](docs/datasource.md) | 数据源三层架构 |
| [docs/executor.md](docs/executor.md) | 执行器设计 |
| [docs/indicator.md](docs/indicator.md) | 指标计算 |
| [docs/exchange.md](docs/exchange.md) | 交易所抽象 |
| [docs/database.md](docs/database.md) | 数据持久化 |

## 运行

```bash
hft -p <password> run main <app_config>
```

配置中敏感信息用 Fernet 加密，`-p` 指定解密密码。
