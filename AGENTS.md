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
- 运行：`hft -p <password> run main <app_config>`（例：`hft run main app` → `conf/app/app.yaml`）
- 测试：`pytest -q`（单测：`pytest tests/test_listener.py -q`）
- ClickHouse（可选）：`docker compose up -d clickhouse`

## Coding Style
- Python 3.13+，4 空格缩进；尽量写完整类型注解（循环引用用 `TYPE_CHECKING`）。
- 命名：类 `PascalCase`；方法/变量 `snake_case`；私有 `_prefix`；需要初始化后调用的方法用 `medal_` 前缀。
- 文件命名：按类型后缀（`*_executor.py`, `*_strategy.py`, `*_datasource.py`, `*_indicator.py`）；基类/配置保持 `base.py`/`config.py`/`group.py`。
- 日志：用 `self.logger` + `%s` 格式（避免 f-string 直接拼日志）。
- 异常：优先捕获具体异常并返回可处理结果；注意 `asyncio.CancelledError` 属于 `BaseException`。

## Testing
- 使用 pytest；异步用例用 `@pytest.mark.asyncio`；测试文件命名：`tests/test_*.py`。

## Commit & Pull Requests
- Commit message：动词开头的祈使句（`Add ...` / `Fix ...` / `Refactor ...`），可在摘要里点明模块（参考 `git log --oneline`）。
- PR：说明目的/影响、验证方式（命令+结果）、关联 `issue/*.md` / `features/*.md`（如适用）。

## Windows Encoding
- 文本文件统一 UTF-8；PowerShell 5.1 读取用 `Get-Content -Encoding utf8 <file>`，或直接使用 `pwsh`（PowerShell 7）。
