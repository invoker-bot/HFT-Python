# Claude Code 项目指南

> 详细文档见 `docs/`，规划见 `plan/`

## 核心架构

**Listener 树形结构**：所有组件继承 Listener，统一生命周期管理。
- 状态机：`STOPPED → STARTING → RUNNING → STOPPING → STOPPED`
- 依赖通过 `parent`/`root` 获取，禁止构造函数传入其他 Listener
- 详见 [docs/listener.md](docs/listener.md)

**配置驱动**：`conf/` 下 YAML 配置，`BaseConfig.load()` 加载并实例化。

**组合模式**：Strategy 定义目标 + Executor 定义执行方式，自由组合。

## 设计原则

| 原则 | 说明 |
|------|------|
| DRY | 公共逻辑提取到基类 |
| 单一职责 | 每个类只做一件事 |
| 模板方法 | 基类定义骨架，子类实现细节 |
| GroupListener | 动态子节点用 `sync_children_params()` + `create_dynamic_child()` |

## 编码约定

**命名**：类 `PascalCase`，方法/变量 `snake_case`，私有 `_prefix`

**类型注解**：使用完整注解，循环引用用 `TYPE_CHECKING`

**日志**：用 `self.logger`，格式用 `%s`（非 f-string）

**异常**：捕获具体异常，返回错误结果而非抛出

## 注释规范

```python
# 复杂逻辑需要解释原因
# NOTE: 这里用 weakref 是因为要避免循环引用导致内存泄漏

# 待优化/待实现
# TODO: 描述
# TODO(P1): 高优先级

# 废弃代码
# DEPRECATED: 原因，将在 vX.X 移除
```

## 单位约定

| 字段 | 单位 | 说明 |
|------|------|------|
| `*_usd` | USD | 仓位/订单价值 |
| `amount` | 合约数量 | 已除以 contract_size |
| `spread` | 比例 | 0.001 = 0.1% |
| `interval` | 秒 | 时间间隔 |

## 开发流程

1. **理解全局**：先读 `docs/architecture.md`，理解相关模块
2. **修改代码**：遇到难懂代码加注释，废弃代码标 DEPRECATED，待做标 TODO
3. **写测试**：功能完成后补充单元测试，`pytest tests/ -v`
4. **更新文档**：重大变更同步更新 `docs/`，规划写入 `plan/`，全部完成后删除当前目录下的plan但写入git commit中。

## 运行

```bash
hft -p <password> run main <app_config>
```

配置中敏感信息用 Fernet 加密，`-p` 指定解密密码。
