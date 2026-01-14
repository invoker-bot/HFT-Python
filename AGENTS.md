# Repository Guidelines

> 快速入口：设计文档 `docs/`；功能提案 `features/*.md`；Bug/争议 `issue/*.md`（附复现/影响/方案）。

## Project Structure
- `hft/`：核心包（`core/`, `exchange/`, `datasource/`, `strategy/`, `executor/`, `database/`, `plugin/`, `indicator/`）
- `tests/`：pytest；`docs/`：设计文档；`examples/`：示例；`conf/`：配置模板
- `data/`, `logs/`：运行产物（不要提交）

## Architecture Notes
- **Listener 树**：所有运行时组件继承 `Listener`，统一生命周期（`STOPPED → STARTING → RUNNING → STOPPING → STOPPED`）。
- **依赖获取**：通过 `parent`/`root` 查找；避免在构造函数里传入其他 Listener（降低耦合、利于 pickle 恢复）。
- **配置驱动**：`conf/` 下 YAML，通过 `BaseConfig.load()` 加载并实例化；敏感字段用 Fernet 加密。
- **组合模式**：Strategy 产出目标仓位（USD），Executor 决定执行方式；插件（pluggy hooks）用于风控/审计/通知扩展。

## Development Commands
- 安装：`pip install -r requirements.txt`；开发：`pip install -e .`
- 运行：`hft -p <password> run main <app_config>`（例：`hft run main app` → `conf/app/app.yaml`），测试时使用的密码为null。
- 测试：`pytest -q`（单测：`pytest tests/test_listener.py -q`）
- ClickHouse（可选）：`docker compose up -d clickhouse`

## 角色分离原则

**除非用户明确指出，否则一次只能执行一个角色的工作：**

1. **提交角色**：创建或更新 `issue/*.md` 或 `features/*.md`，描述问题或提议
2. **实现角色**：修改代码实现 issue/feature，或反驳不切实际的提议并说明理由
3. **审核角色**：审核 issue/feature 的完成质量，判定是否通过或需要返工
4. **提交角色**：执行 git commit/push/PR 操作

**禁止行为**：
- ❌ 修复代码后自行判定完成并更新 issue 状态
- ❌ 实现功能后自己审核并标记为"审核通过"
- ❌ 未经用户明确批准就提交代码
- ❌ 一次性完成"实现→审核→提交"的完整流程

**正确流程示例**：
- 实现代码后：向用户**报告**完成情况和测试结果，**等待**用户审核或下一步指示
- 审核通过后：等待用户明确要求再更新状态或提交代码
- 角色切换：需要用户明确指示（如"现在审核这个 issue"、"提交代码"）

## 开发流程
1. **理解全局**：先读 `docs/architecture.md`，理解相关模块
2. **修改代码**：遇到难懂代码加注释；废弃代码标 `DEPRECATED`；待做标 `TODO`
3. **写测试**：功能完成后补充单元测试，运行 `pytest tests/ -v`
4. **更新文档**：重大变更同步更新 `docs/`
5. **新增功能**：全局规划写入 `features/*.md`，每条 TODO 用单行状态标注：`- [ ] 事项（待实现/待审核/审核完成）`；若提议不切实际给出否决理由
6. **BUG 修复**：报告/遇到的问题写入 `issue/*.md`，同样使用 `- [ ] 事项（待实现/待审核/审核完成）` 的单行状态标注
7. **代码审核**：提交前检查 `features/`、`issue/` 中的待审核项；审核通过将状态改为“审核完成”并勾选，若驳回需写明理由
8. **代码提交**：仅在用户明确要求/批准时提交；通过审核和单元测试后提交代码，并写明此次更新内容

## Coding Style
- Python 3.13+，4 空格缩进；尽量写完整类型注解（循环引用用 `TYPE_CHECKING`）。
- 命名：类 `PascalCase`；方法/变量 `snake_case`；私有 `_prefix`；需要初始化后调用的方法用 `medal_` 前缀。
- 文件命名：按类型后缀（`*_executor.py`, `*_strategy.py`, `*_datasource.py`, `*_indicator.py`）；基类/配置保持 `base.py`/`config.py`/`group.py`。
- 日志：用 `self.logger` + `%s` 格式（避免 f-string 直接拼日志）。
- 异常：优先捕获具体异常并返回可处理结果；注意 `asyncio.CancelledError` 属于 `BaseException`。

## Testing
- 使用 pytest；异步用例用 `@pytest.mark.asyncio`；测试文件命名：`tests/test_*.py`。
- 变更后至少跑：`pytest tests/ -v`（快速检查可用 `pytest -q`）。

## Commit & Pull Requests
- Commit message：动词开头的祈使句（`Add ...` / `Fix ...` / `Refactor ...`），可在摘要里点明模块（参考 `git log --oneline`）。
- PR：说明目的/影响、验证方式（命令+结果）、关联 `issue/*.md` / `features/*.md`（如适用）。

## Windows Encoding
- 文本文件统一 UTF-8；PowerShell 5.1 读取用 `Get-Content -Encoding utf8 <file>`，或直接使用 `pwsh`（PowerShell 7）。
