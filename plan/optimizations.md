# 性能优化点

## 已识别的优化机会

### 1. Executor 订单管理 (P2)

**位置**: `hft/executor/base.py`

**问题**:
- `manage_limit_orders` 方法较复杂，有重复逻辑
- 订单状态追踪分散在多处

**优化方案**:
- 提取 `OrderTracker` 类，统一管理订单状态
- 使用状态机模式管理订单生命周期

### 2. 类索引清理 (P3)

**位置**: `hft/core/listener.py`

**问题**:
- 弱引用清理只在查找时触发
- 大量失效引用可能积累

**优化方案**:
- 添加定期清理任务
- 使用 `weakref.finalize` 回调自动清理

### 3. DataSource 数据存储 (P2) ✓ 已完成

**位置**: `hft/datasource/group.py`

**已完成**:
- ✓ `DataArray` 已改用 `collections.deque` 存储
- ✓ 添加了健康检查 (`check_healthy`, `is_fresh`, `coverage_duration`)
- ✓ 支持 `get_since(timestamp)` 查询

**后续优化**:
- 考虑使用 numpy 数组存储数值数据（如需要）

### 4. 健康检查重试 (P3)

**位置**: `hft/core/listener.py`

**问题**:
- 每次健康检查都创建新的 `AsyncRetrying` 实例
- 重试等待时间是固定的

**优化方案**:
- 缓存 `AsyncRetrying` 实例
- 支持指数退避重试策略

### 5. Exchange 缓存策略 (P2)

**位置**: `hft/exchange/base.py`

**问题**:
- `HealthyData` 缓存时间固定
- 不同数据类型应有不同的过期时间

**优化方案**:
- 按数据类型配置过期时间
- 添加主动刷新机制

### 6. 日志性能 (P3)

**位置**: 全局

**问题**:
- 大量 DEBUG 日志可能影响性能
- 日志字符串格式化在日志级别禁用时仍会执行

**优化方案**:
- 使用 `logger.isEnabledFor()` 检查
- 使用 `%s` 格式化而非 f-string

```python
# 优化前
self.logger.debug(f"Order {order_id} created at {price}")

# 优化后
if self.logger.isEnabledFor(logging.DEBUG):
    self.logger.debug("Order %s created at %s", order_id, price)
```

### 7. asyncio 锁竞争 (P2)

**位置**: `hft/core/listener.py`

**问题**:
- `_alock` 在每次 tick 时都会获取
- 高频场景下可能成为瓶颈

**优化方案**:
- 评估锁的必要性
- 考虑使用无锁数据结构

### 8. pickle 序列化 (P3)

**位置**: `hft/core/listener.py`

**问题**:
- `__getstate__` 每次都复制整个 dict
- 大型对象序列化可能较慢

**优化方案**:
- 使用 `__reduce__` 自定义序列化
- 只序列化必要字段

---

## 代码质量改进

### 1. 类型注解完善

**位置**: 多处

**问题**: 部分方法缺少返回类型注解

**改进**: 添加完整类型注解，启用 mypy 检查

### 2. 文档字符串

**位置**: 多处

**问题**: 部分公共方法缺少文档

**改进**: 为所有公共 API 添加 Google 风格文档

### 3. 异常处理

**位置**: 多处

**问题**: 部分异常处理过于宽泛

**改进**: 捕获具体异常类型，添加上下文信息
