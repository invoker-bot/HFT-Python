# Issue: Executor 上下文变量名冲突（Indicator 覆盖保留变量）

## 背景 / 现象

Feature 0005 引入了 `collect_context_vars()`：先注入内置变量（`direction/buy/sell/speed/notional`），再 `context.update(indicator.calculate_vars())` 注入指标变量。

当前仓库存在以下**变量名冲突**与**语义覆盖**风险：

1. **Indicator 可以覆盖内置变量**  
   例如 `hft/indicator/computed/volume_indicator.py` 会返回 `notional`（成交额），这会覆盖内置 `notional=abs(delta_usd)`。

2. **不同 Executor 对同名变量的“后注入覆盖”不一致**  
   `BaseExecutor`/`LimitExecutor` 会在 collect 之后注入 `mid_price=current_price`，会覆盖同名 indicator 变量（例如 `MidPriceIndicator` 也产出 `mid_price`）。

3. **SmartExecutor 的 notional 语义二次重定义**  
   `hft/executor/smart_executor/executor.py` 的路由上下文会把：
   - `target_notional` 设为当前 `context["notional"]`
   - 再把 `context["notional"]` 覆盖为 `trades_notional`（成交额，方向相关）
   
   如果 `context["notional"]` 在这之前已被 `VolumeIndicator.notional` 覆盖，则 `target_notional` 会变成“成交额”而不是“目标差额”，导致路由规则使用 `target_notional` 时语义错误。

## 影响

- SmartExecutor 路由：使用 `target_notional`/`notional` 的规则可能在某些 requires 组合下出现**错误命中/错误跳过**。
- 动态参数：表达式若依赖 `notional`，可能把“目标差额”误当成“成交额”，导致下单金额/阈值异常。
- 文档/配置可维护性：用户难以判断 `notional/mid_price` 在不同执行链路中到底是哪一种语义。

## 复现思路（最小化）

- SmartExecutor 配置 `requires: [volume]`（或任意会输出 `notional` 的 indicator）
- routes 使用 `target_notional` 或在自动路由逻辑中依赖 `target_notional` 的语义
- 当 `VolumeIndicator` ready 时，`collect_context_vars` 会把 `notional` 覆盖为成交额，从而导致 `target_notional` 被错误赋值

## 期望行为

- 内置变量（至少 `direction/buy/sell/speed/notional/target_notional/mid_price`）具备**稳定且一致**的语义。
- Indicator 的变量命名不应隐式覆盖保留变量；如确需覆盖，应有显式机制（命名空间/前缀/allow_override 白名单）。

## 修复方向（可选方案）

1. **强制保留名不可覆盖（推荐）**  
   `BaseExecutor.collect_context_vars()` 在 `context.update(vars_dict)` 前做冲突检查：  
   - 冲突 key 直接跳过并 `logger.warning`  
   - 或者将冲突 key 重命名为 `indicator_<name>_<key>`（不推荐：会引入隐式改名）

2. **修正 VolumeIndicator / MidPriceIndicator 的输出 key**  
   - `VolumeIndicator.notional` 改为 `volume_notional`（或 `trades_notional_total`），避免覆盖内置 `notional`  
   - `MidPriceIndicator.mid_price` 改为 `orderbook_mid_price`，与执行器注入的 `mid_price=current_price` 区分

3. **统一“当前价”变量名**  
   执行器注入使用 `current_price`，保留 `mid_price` 给 orderbook mid（影响面较大，需要同步迁移配置/文档）

## TODO

- [ ] 明确保留变量名集合与覆盖策略（待实现）
- [ ] 修复 `VolumeIndicator`/`MidPriceIndicator` 的输出 key 或在 Executor 侧屏蔽覆盖（待实现）
- [ ] 补充单测：确保 `notional=abs(delta_usd)` 不会被 indicator 覆盖；SmartExecutor 的 `target_notional` 恒等于 `abs(delta_usd)`（待实现）
- [ ] 评估并更新路由表达式示例：明确使用 `target_notional`/`trades_notional`（待实现）

