# Issue 0008: 集成测试在无网络/DNS 环境下失败（应 opt-in 或清晰跳过）

> **状态**：全部通过

## 现象

在无网络或 DNS 不可用的环境下（例如 CI / 沙箱 / 断网环境），运行测试会出现：

- `pytest -q`：`tests/test_demo_config_loading.py` 中的 demo exchange 网络用例直接失败（而不是被跳过/可选执行）
- `INTEGRATION_TEST_ALLOW_LISTS="*" ...`：`tests/test_integration_trading.py`（真实下单集成测试）在进入用例前即因网络不可达报错

## 复现（修复前）

### 1) demo config 集成测试导致 `pytest -q` 失败

```bash
pytest -q
```

失败点（节选）：

- `tests/test_demo_config_loading.py::TestExchangeIntegration::test_exchange_load_markets[...]`
- `tests/test_demo_config_loading.py::TestExchangeIntegration::test_exchange_fetch_ticker[...]`
- `tests/test_demo_config_loading.py::TestExchangeIntegration::test_exchange_fetch_balance[...]`
- `tests/test_demo_config_loading.py::TestExchangeIntegration::test_exchange_fetch_order_book[...]`

常见错误形态：

```
socket.gaierror: [Errno -3] Temporary failure in name resolution
aiodns.error.DNSError: (11, 'Could not contact DNS servers')
```

### 2) 真实下单集成测试在网络不可达时直接报错

```bash
INTEGRATION_TEST_ALLOW_LISTS="*" pytest -q -m integration_test
```

常见错误形态：

```
socket.gaierror: [Errno -3] Temporary failure in name resolution
ccxt.base.errors.ExchangeNotAvailable: ...
```

## 影响

- 单元测试与集成测试边界不清晰：`pytest -q` 会触发网络调用（demo exchange），在无网环境下无法作为“快速健康检查”使用。
- 开发者体验/CI 稳定性差：网络不可用时直接 FAIL/ERROR，缺少“明确跳过原因”的提示与 opt-in 机制。

## 期望行为

- 网络相关测试默认不阻塞 `pytest -q`（默认应跳过或被 deselect），需要显式 opt-in 才执行。
- 当用户显式 opt-in（例如设置 `INTEGRATION_TEST_ALLOW_LISTS`）但网络仍不可用时：应输出更清晰的失败信息（或在约定下改为 skip/xfail），便于排障。

## 修复建议（方案待定）

1) 对 `tests/test_demo_config_loading.py`：
   - 方案 A：增加环境变量开关（例如 `DEMO_CONFIG_INTEGRATION=1`）未开启则整类跳过
   - 方案 B：通过 pytest 默认参数/配置让 `@pytest.mark.integration` 默认 deselect（仅在显式 `-m integration` 时运行）

2) 对 `tests/test_integration_trading.py`：
   - 增加“网络可达性预检”（DNS/连通性失败时给出明确提示），避免一上来堆栈报错导致误判为业务 bug。

## TODO

- [x] 明确项目口径：`pytest -q` 是否允许包含任何网络集成测试（已通过）
  - 结论：默认排除所有 integration 测试，用 `-m integration` 显式启用
- [x] 为 `tests/test_demo_config_loading.py` 增加 opt-in 机制（已通过）
  - 通过 pytest.ini 的 `addopts` 默认排除 `@pytest.mark.integration`
- [x] 为 `tests/test_integration_trading.py` 增加网络预检/更清晰的错误输出（已通过）
  - 添加 `check_network_connectivity()` 函数和 `check_network` fixture
  - 网络不可用时给出清晰的跳过信息

## 验收

- `pytest -q`：默认不触发任何网络调用（集成测试被 deselect），可作为快速健康检查
- `pytest -m integration --collect-only`：可显式收集 demo config 集成测试用例（需要网络时再执行）
- `pytest -m integration_test --collect-only`：可显式收集真实下单集成测试用例（需要网络 + 环境变量时再执行）

## 修复内容

### 1. pytest.ini 配置更新

```ini
# 默认排除集成测试（需要网络连接）
# 运行集成测试：pytest -m integration
# 运行全部测试：pytest -m ""
addopts = -m "not integration and not integration_test and not slow_integration_test"

markers =
    integration: marks tests as integration tests requiring network
    integration_test: marks tests requiring INTEGRATION_TEST_ALLOW_LISTS env
    slow_integration_test: marks slow integration tests (app tick level)
```

### 2. 网络预检机制

在 `test_integration_trading.py` 中添加：

```python
def check_network_connectivity(timeout: float = 3.0) -> tuple[bool, str]:
    """检查网络连通性，返回 (is_connected, message)"""
    # 尝试连接多个交易所域名
    ...

@pytest.fixture(scope="module")
def check_network():
    """网络预检 fixture，不可用时跳过并给出清晰信息"""
    is_connected, message = check_network_connectivity()
    if not is_connected:
        pytest.skip(f"Integration tests require network connectivity.\n  {message}")
```

### 3. 运行方式

```bash
# 默认（不运行集成测试）
pytest -q

# 运行 integration 标记的测试（需要网络）
pytest -m integration -v -s

# 运行真实下单集成测试（需要网络 + 环境变量）
INTEGRATION_TEST_ALLOW_LISTS="*" pytest -m integration_test -v -s

# 运行全部测试
pytest -m "" -v -s
```
