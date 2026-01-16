# Feature: 移除 DataSourceGroup

## 背景

Feature 0006 实现了 `IndicatorGroup` 统一架构，但旧的 `DataSourceGroup` 仍在使用。本 Feature 完成最终清理工作。

## 目标

完全移除 `hft/datasource/group.py` 中的 `DataSourceGroup`、`TradingPairDataSource`、`DataType`、`DataArray`。

## 当前依赖分析

### 1. AppCore

```python
# hft/core/app/base.py
self.datasource_group = DataSourceGroup()
self.add_child(self.datasource_group)
```

### 2. GlobalFundingRateFetcher

```python
# hft/datasource/funding_rate_fetcher.py
@property
def datasource_group(self) -> "DataSourceGroup":
    return self.parent

# 依赖 datasource_group.children 获取 TradingPairDataSource
# 依赖 datasource_group.exchange_group 获取交易所
```

### 3. 回退路径

- `LazyIndicator.get_datasource()` 回退到 `TradingPairDataSource.query(DataType)`
- `avellaneda_stoikov_executor._get_datasource()` 回退到 `DataSourceGroup.query()`

## 迁移计划

### Phase 1：迁移 GlobalFundingRateFetcher

将 `GlobalFundingRateFetcher` 改为挂载在 `IndicatorGroup` 上。

#### 1.1 创建 GlobalFundingRateIndicator

```python
# hft/indicator/datasource/funding_rate.py
class GlobalFundingRateIndicator(GlobalIndicator[dict[str, FundingRate]]):
    """
    全局资金费率指标

    定时获取所有交易对的资金费率，通过事件分发到各个 FundingRateIndicator。
    """
    def __init__(self, exchange_class: str, interval: float = 3.0):
        super().__init__(interval=interval)
        self._exchange_class = exchange_class

    async def on_tick(self) -> bool:
        exchange = self._get_exchange()
        funding_rates = await exchange.medal_fetch_funding_rates()
        self._data.append(time.time(), funding_rates)
        self.emit("update", funding_rates)
        return False
```

#### 1.2 创建 FundingRateIndicator

```python
class FundingRateIndicator(BaseIndicator[FundingRate]):
    """
    交易对级资金费率指标

    监听 GlobalFundingRateIndicator 的 update 事件，提取本交易对的数据。
    """
    def __init__(self, exchange_class: str, symbol: str):
        super().__init__(interval=None)  # 事件驱动
        self._exchange_class = exchange_class
        self._symbol = symbol

    async def on_start(self) -> None:
        global_fr = self.root.indicator_group.get_indicator(
            f"global_funding_rate:{self._exchange_class}", None, None
        )
        if global_fr:
            global_fr.on("update", self._on_global_update)

    def _on_global_update(self, funding_rates: dict) -> None:
        fr = funding_rates.get(self._symbol)
        if fr:
            self._data.append(fr.timestamp, fr)
            self._emit_update(fr.timestamp, fr)
```

### Phase 2：移除 AppCore.datasource_group

修改 `AppCore`，移除 `datasource_group` 属性。

```python
# hft/core/app/base.py
# 删除以下代码：
# self.datasource_group = DataSourceGroup()
# self.add_child(self.datasource_group)
```

### Phase 3：移除回退路径

#### 3.1 LazyIndicator

```python
# hft/indicator/lazy_indicator.py
def get_datasource(self, data_type: str) -> Optional[BaseIndicator]:
    # 移除旧架构回退，只使用 IndicatorGroup
    indicator_group = self._get_indicator_group()
    if indicator_group is None:
        return None
    exchange_class, symbol = self._get_exchange_info()
    if not exchange_class or not symbol:
        return None
    return indicator_group.get_indicator(data_type, exchange_class, symbol)
```

#### 3.2 avellaneda_stoikov_executor

```python
# hft/executor/avellaneda_stoikov_executor/executor.py
def _get_datasource(self, data_type: str, exchange_class: str, symbol: str):
    # 移除旧架构回退，只使用 IndicatorGroup
    ig = self.indicator_group
    if ig is None:
        return None
    return ig.get_indicator(data_type, exchange_class, symbol)
```

### Phase 4：删除旧模块

1. 删除 `hft/datasource/group.py` 中的：
   - `DataSourceGroup` 类
   - `TradingPairDataSource` 类
   - `DataType` 枚举
   - `DataArray` 类

2. 更新 `hft/datasource/__init__.py`：
   - 移除已删除类的导出

3. 删除或迁移测试：
   - `tests/test_datasource_group.py` 中的 `DataArray` 测试迁移到 `HealthyDataArray`
   - 删除 `DataSourceGroup` 相关测试

## TODO

> Phase 1：迁移 GlobalFundingRateFetcher

- [ ] 创建 `GlobalFundingRateIndicator`（待审核）
- [ ] 创建 `FundingRateIndicator`（待审核）
- [ ] 注册 factory 到 `IndicatorGroup`（待审核）
- [ ] 迁移 `FundingRatePersistListener`（待审核）
- [ ] 单元测试（待审核）

> Phase 2：移除 AppCore.datasource_group

- [ ] 修改 `AppCore.__init__()`（待审核）
- [ ] 更新相关文档（待审核）

> Phase 3：移除回退路径

- [ ] 修改 `LazyIndicator.get_datasource()`（待审核）
- [ ] 修改 `avellaneda_stoikov_executor._get_datasource()`（待审核）
- [ ] 移除 `DataType` 导入（待审核）

> Phase 4：删除旧模块

- [ ] 在 `hft/datasource/group.py` 添加废弃标记（待审核）
- [ ] 更新 `__init__.py` 导出（待审核）
- [ ] 删除旧测试文件（待审核）
- [ ] 更新文档（待审核）
