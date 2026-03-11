# Issue 0018: 性能测试框架与复杂度验证

**状态**: 部分完成（In Progress） — Mock Exchange 基础设施已完成，性能测试套件部分待实现

## 背景

当前缺乏系统的性能测试框架来验证系统在大规模交易对（n）下的行为。需要建立完整的测试方案来验证：

1. **处理复杂度**：Strategy O(n)、Executor O(1)
2. **网络请求次数**：趋近常数，不随 n 增长
3. **内存消耗**：HealthyDataArray O(n*m)，不随时间无限增长
4. **资源释放**：watch_ticker 等资源正确释放
5. **去重机制**：相同 class 的 fetch/watch 不重复调用

## 问题描述

### 1. 缺乏 Mock Exchange 测试基础设施

- 当前测试依赖真实交易所，难以控制测试场景
- 无法模拟大规模交易对（50/200/1000/5000）
- 无法加速时间进行长时间测试

### 2. 复杂度验证不足

- **Strategy 复杂度**：应为 O(n)，但需验证
  - 全局数据获取（如 fetch_tickers）是 O(1) 次调用
  - 如果 requires 依赖 per-symbol indicator，应给出警告
- **Executor 复杂度**：必须为 O(1)
  - 只处理 target=true 的结果（已裁剪）
  - 不应随 markets 数量增长

### 3. 内存消耗未验证

- **HealthyDataArray**：应为 O(n*m)
  - n = markets 数量
  - m = 时间窗口长度
  - 不应随时间无限增长
- **资源泄漏**：watch_ticker 等资源是否正确释放

### 4. 网络请求去重未验证

- 相同 class 的 fetch_ticker/watch_ticker 是否去重
- 不同 class 的是否正确分别调用
- 请求次数是否趋近常数

## 解决方案

### 1. Mock Exchange 测试基础设施

创建 `hft/exchange/demo/mock_exchange.py`：

```python
class MockExchange(BaseExchange):
    """
    Mock 交易所，用于性能测试

    特点：
    - 返回大量模拟的 markets/tickers/orderbook 数据
    - 支持 watch_ticker/watch_orderbook 等异步方法
    - 记录所有 API 调用次数和参数
    - 支持 fake time 加速时间流逝
    """

    def __init__(self, num_markets: int = 100):
        self.num_markets = num_markets
        self.api_calls = []  # 记录所有 API 调用
        self.fake_time = 0.0

    def load_markets(self):
        """生成 n 个模拟交易对"""
        pass

    def fetch_ticker(self, symbol: str):
        """返回模拟 ticker 数据"""
        self.api_calls.append(('fetch_ticker', symbol))
        pass

    def watch_ticker(self, symbol: str):
        """模拟 watch ticker"""
        self.api_calls.append(('watch_ticker', symbol))
        pass
```

### 2. 性能测试套件

创建 `tests/test_performance_scaling.py`：

**测试 1：Strategy 复杂度测试**
```python
def test_strategy_complexity_is_linear():
    """验证 Strategy 处理复杂度为 O(n)"""
    # 测试不同规模的 markets
    for n in [50, 200, 1000]:
        mock_exchange = MockExchange(num_markets=n)
        strategy = MarketNeutralStrategy(...)

        # 运行一个 tick
        strategy.get_target_positions_usd()

        # 验证：fetch_tickers 只调用一次（O(1)）
        fetch_calls = count_api_calls(mock_exchange, 'fetch_tickers')
        assert fetch_calls == 1

        # 验证：处理时间与 n 线性相关（允许误差）
        assert processing_time < O(n) * tolerance
```

**测试 2：Executor 复杂度测试**
```python
def test_executor_complexity_is_constant():
    """验证 Executor 处理复杂度为 O(1)"""
    for n in [50, 200, 1000]:
        mock_exchange = MockExchange(num_markets=n)
        executor = LimitExecutor(...)

        # 只有 2 个 target=true 的交易对
        targets = {('okx', 'BTC/USDT'): (100, 1.0),
                   ('okx', 'ETH/USDT'): (50, 1.0)}

        # 运行一个 tick
        executor.execute(targets)

        # 验证：处理时间为常数，不随 n 增长
        assert processing_time < constant_threshold
```

**测试 3：内存消耗测试**
```python
def test_healthy_data_array_memory_bounded():
    """验证 HealthyDataArray 内存消耗为 O(n*m)，不随时间增长"""
    mock_exchange = MockExchange(num_markets=100)
    app = AppCore(...)

    # 使用 fake time 加速运行 1000 个 tick
    for i in range(1000):
        mock_exchange.fake_time += 1.0
        app.tick()

        # 每 100 个 tick 检查一次内存
        if i % 100 == 0:
            memory_usage = measure_memory()
            # 验证：内存不随时间无限增长
            assert memory_usage < n * m * size_per_item * tolerance
```

**测试 4：网络请求去重测试**
```python
def test_api_call_deduplication():
    """验证相同 class 的 API 调用去重"""
    mock_exchange = MockExchange(num_markets=100)
    app = AppCore(...)

    # 运行多个 tick
    for _ in range(10):
        app.tick()

    # 验证：相同 class 的 fetch_tickers 只调用一次
    fetch_tickers_calls = count_api_calls(mock_exchange, 'fetch_tickers')
    assert fetch_tickers_calls <= 1  # 应该去重

    # 验证：不同 class 的分别调用
    # （如果有多个 exchange_class）
```

**测试 5：资源释放测试**
```python
def test_watch_ticker_resource_cleanup():
    """验证 watch_ticker 资源正确释放"""
    mock_exchange = MockExchange(num_markets=100)
    app = AppCore(...)

    # 启动 app
    app.start()
    app.tick()

    # 记录 active watch 数量
    active_watches_before = count_active_watches(mock_exchange)

    # 停止 app
    app.stop()

    # 验证：所有 watch 资源已释放
    active_watches_after = count_active_watches(mock_exchange)
    assert active_watches_after == 0
```

### 3. Fake Time 支持

在 MockExchange 中实现时间加速：

```python
class MockExchange(BaseExchange):
    def __init__(self, num_markets: int = 100):
        self.fake_time = 0.0
        self.time_multiplier = 1.0  # 时间加速倍数

    def advance_time(self, seconds: float):
        """推进模拟时间"""
        self.fake_time += seconds * self.time_multiplier

    def get_current_time(self):
        """返回当前模拟时间"""
        return self.fake_time
```

### 4. 测试配置文件

创建测试配置 `conf/exchange/demo/mock.yaml`：

```yaml
class_name: MockExchange
params:
  num_markets: 100  # 可配置交易对数量
  password: null    # 测试时不需要密码
```

## 实现任务

### 阶段 1：Mock Exchange 基础设施

- [x] 创建 `hft/exchange/demo/mock_exchange.py`（已完成）
- [x] 实现 `load_markets()` 生成 n 个模拟交易对（已完成）
- [x] 实现 `fetch_ticker()` 返回模拟数据（已完成）
- [x] 实现 `watch_ticker()` 模拟异步订阅（已完成）
- [x] 实现 API 调用记录机制（已完成）
- [x] 实现 fake time 时间加速（已完成）

### 阶段 2：性能测试套件

- [x] 创建 `tests/test_performance_scaling.py`（已完成）
- [ ] 实现 Strategy 复杂度测试（O(n)）（待实现）
- [ ] 实现 Executor 复杂度测试（O(1)）（待实现）
- [ ] 实现内存消耗测试（HealthyDataArray）（待实现）
- [x] 实现网络请求去重测试（已完成）
- [ ] 实现资源释放测试（watch_ticker）（待实现）

### 阶段 3：测试策略配置

- [ ] 创建 Market Neutral 策略测试配置（待实现）
- [ ] 创建 Static Positions 策略测试配置（待实现）
- [ ] 创建测试用 App 配置（待实现）
- [x] 创建 `conf/exchange/demo/mock.yaml` 配置（已完成）

## 验收标准

### 1. Mock Exchange 功能完整

- MockExchange 能生成任意数量的模拟交易对
- 所有 API 调用被正确记录
- Fake time 能正确加速时间流逝

### 2. 复杂度测试通过

- Strategy 处理复杂度为 O(n)，fetch_tickers 只调用一次
- Executor 处理复杂度为 O(1)，不随 n 增长
- 测试覆盖 n = [50, 200, 1000, 5000]

### 3. 内存消耗测试通过

- HealthyDataArray 内存为 O(n*m)，不随时间增长
- 运行 1000+ ticks 后内存稳定
- watch_ticker 资源正确释放

### 4. 网络请求去重验证

- 相同 class 的 fetch_tickers 只调用一次
- 不同 class 的分别调用
- 请求次数趋近常数

## 相关 Issue

- Issue 0017: 性能回归 - per-symbol requires 警告与 watch 常数复杂度
  - 本 Issue 提供测试框架来验证 Issue 0017 的修复效果

## 注意事项

1. **测试隔离**：每个测试应该独立运行，避免相互影响
2. **性能基准**：建立性能基准线，用于回归测试
3. **Mock 真实性**：Mock Exchange 应尽可能模拟真实交易所行为
4. **时间控制**：使用 fake time 时注意与系统时间的隔离

