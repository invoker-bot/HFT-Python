# Example 004: MarketNeutralPositions 策略配置详解

## 概述

本文档详细介绍如何配置和使用 **MarketNeutralPositions** 策略。这是一个市场中性对冲策略，通过在不同交易所/交易对之间建立对冲仓位来捕获价差套利机会。

## 核心特性

1. **市场中性**：确保组内 `ratio` 总和为 0，降低市场风险
2. **自动分组**：按 `group_id` 自动分组交易对（如 ETH/USDT、WBETH/USDT → ETH 组）
3. **公平价格**：通过 `FairPriceIndicator` 计算标准价格
4. **智能方向**：自动计算开仓/平仓/持仓方向
5. **Ratio 平衡**：自动调整仓位比例，满足对冲条件

## 套利场景

### 场景 1：跨平台现货套利

```
低价平台买入现货 → 链上转账 → 高价平台卖出现货
同时：高价平台买入等值空合约（对冲）
```

**示例**：
- OKX ETH/USDT: 2000 USD（买入现货）
- Binance ETH/USDT: 2010 USD（卖出现货 + 做空合约）
- 价差收益：10 USD/ETH

### 场景 2：资费率套利

```
资费率为正：做空合约 + 买入现货（收取资费）
资费率为负：做多合约 + 卖出现货（收取资费）
```

### 场景 3：合约间套利

```
不同交易所的合约价差套利
```


## 配置示例

### 基础配置

```yaml
# conf/strategy/market_neutral_positions/eth_arbitrage.yaml
class_name: market_neutral_positions

# 交易对过滤
include_symbols: ['ETH/USDT', 'WBETH/USDT', 'BTC/USDT']
exclude_symbols: []

# 依赖的 Indicator
requires:
  - medal_amount  # 账户余额
  - ticker        # 行情数据
  - fair_price    # 公平价格

# Scope 链路
links:
  - id: main
    value: [g, exchange_class, exchange, trading_pair_class_group, trading_pair_class, trading_pair]

# 分组配置
default_trading_pair_group: symbol.split('/')[0]
trading_pair_group:
  WBETH/USDT: ETH  # WBETH 映射到 ETH 组
  STETH/USDT: ETH  # STETH 映射到 ETH 组

# 阈值配置
max_trading_pair_groups: 10        # 最大分组数量
entry_price_threshold: 0.001       # 0.1% 价差开仓
exit_price_threshold: 0.0005       # 0.05% 价差平仓
score_threshold: 0.001             # 最小 score 阈值

# 目标配置
targets:
  - exchange_id: "*"
    symbol: "*"
    condition: "ratio != 0"
    vars:
      - position_usd=ratio * max_position_usd
```


### App 配置（Scope 定义）

```yaml
# conf/app/eth_arbitrage.yaml
class_name: app

# 交易所配置
exchanges:
  - okx/main
  - binance/spot

# 策略配置
strategy: market_neutral_positions/eth_arbitrage

# Scope 配置
scopes:
  g:
    class: GlobalScope
    vars:
      - max_position_usd=2000
      - weights={"okx/main": 0.5, "binance/spot": 0.5}

  exchange_class:
    class: ExchangeClassScope

  exchange:
    class: ExchangeScope

  trading_pair_class_group:
    class: TradingPairClassGroupScope
    vars:
      - fair_price_min=min([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - fair_price_max=max([scope["trading_pair_std_price"] for scope in children.values() if scope["trading_pair_std_price"] is not None])
      - score=fair_price_max - fair_price_min
      - ratio_est=sum([scope["ratio_est"] for scope in children.values()])
    # 不再支持 group_condition；如需过滤 group，请在 vars 中计算如 group_enabled，并在 target condition 中引用

  trading_pair_class:
    class: TradingPairClassScope
    vars:
      - delta_min_price=trading_pair_std_price - parent["fair_price_min"]
      - delta_max_price=parent["fair_price_max"] - trading_pair_std_price
      - ratio_est=sum([scope["ratio_est_instance"] for scope in children.values()])

  trading_pair:
    class: TradingPairScope
    vars:
      - weight=weights.get(exchange_id, 1.0)
      - ratio_est_instance=weight * (parent.parent["fair_price_min"] * amount) / max_position_usd
```


## 配置参数说明

### 策略参数（代码引用的特殊字段）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_trading_pair_groups` | int | 10 | 最大交易对分组数量（代码引用） |
| `entry_price_threshold` | float | 0.001 | 开仓价差阈值（0.1%） |
| `exit_price_threshold` | float | 0.0005 | 平仓价差阈值（0.05%） |
| `score_threshold` | float | 0.001 | 最小 score 阈值 |
| `default_trading_pair_group` | str | `symbol.split('/')[0]` | 默认分组表达式 |
| `trading_pair_group` | dict | {} | 自定义分组映射 |

### Scope 变量说明（用户定义的通用值）

#### GlobalScope (g)

- `max_position_usd`: 每个分组的最大仓位（用户可自定义名称）
- `weights`: 交易所权重配置（用户可自定义名称）

#### TradingPairClassGroupScope

- `fair_price_min`: 组内最低公平价格
- `fair_price_max`: 组内最高公平价格
- `score`: 价差分数（max - min）
- `ratio_est`: 组内 ratio 估算总和

#### TradingPairClassScope

- `delta_min_price`: 相对最低价的价差
- `delta_max_price`: 相对最高价的价差
- `ratio_est`: 该交易对的 ratio 估算

#### TradingPairScope

- `weight`: 交易所权重
- `ratio_est_instance`: 该实例的 ratio 估算


## 工作原理

### 1. Trading Pair 分组

策略首先将交易对按 `group_id` 分组：

```python
# 默认分组：ETH/USDT → ETH
# 自定义分组：WBETH/USDT → ETH（通过 trading_pair_group 配置）

{
    "ETH": [
        ("okx/main", "ETH/USDT"),
        ("okx/main", "WBETH/USDT"),
        ("binance/spot", "ETH/USDT"),
    ],
    "BTC": [
        ("okx/main", "BTC/USDT"),
        ("binance/spot", "BTC/USDT"),
    ],
}
```

### 2. Fair Price 计算

通过 `FairPriceIndicator` 获取每个交易对的公平价格（mid_price）：

```python
# FairPriceIndicator 返回的原始价格
ETH/USDT (okx): 2000 USD
ETH/USDT (binance): 2010 USD
WBETH/USDT (okx): 1990 USD

# Strategy 层使用这些原始价格进行比较
# 组内最小价格：1990 USD (WBETH/USDT)
# 组内最大价格：2010 USD (ETH/USDT binance)
```

### 3. Direction 计算

根据价差计算每个交易对的方向：

| 价差 | delta_min_direction | delta_max_direction |
|------|---------------------|---------------------|
| > entry_price_threshold | -1 (Entry Short) | 1 (Entry Long) |
| > exit_price_threshold | 0 (Exit) | 0 (Exit) |
| else | null (Hold) | null (Hold) |

**示例**：
```python
WBETH/USDT (okx):   delta_min=0, delta_max=0.010 → (-1, 1)  # 最低价，做多
ETH/USDT (okx):     delta_min=0.005, delta_max=0.005 → (0, 0)  # 中间价，平仓
ETH/USDT (binance): delta_min=0.010, delta_max=0 → (1, -1)  # 最高价，做空
```


### 4. Ratio 计算与平衡

#### 步骤 1：初始 Ratio
```python
ratio = clip(ratio_est, -1, 1)
```

#### 步骤 2：根据 Direction 调整
根据 `(delta_min_direction, delta_max_direction)` 调整 ratio。

#### 步骤 3：Ratio 归零（市场中性）
```python
ratio_sum = sum([child["ratio"] for child in children.values()])
if ratio_sum > 0:
    max_price_pair["ratio"] -= ratio_sum
elif ratio_sum < 0:
    min_price_pair["ratio"] -= ratio_sum
```

#### 步骤 4：对冲调整
```python
delta_ratio = (min_price_pair["ratio"] - max_price_pair["ratio"]) / 2 - 1
min_price_pair["ratio"] -= delta_ratio
max_price_pair["ratio"] += delta_ratio
```

**最终结果**：
```python
WBETH/USDT (okx):   ratio =  1.0  # 做多
ETH/USDT (okx):     ratio =  0.0  # 不持仓
ETH/USDT (binance): ratio = -1.0  # 做空

# 验证：1.0 + 0.0 - 1.0 = 0 ✓（市场中性）
# 验证：1.0 - (-1.0) = 2 ✓（对冲条件）
```


## 实际案例

### 案例 1：ETH 跨平台套利

**市场情况**：
- OKX ETH/USDT: 2000 USD
- Binance ETH/USDT: 2010 USD
- 价差：0.5%（超过 entry_price_threshold）

**策略输出**：
```python
{
    ("okx/main", "ETH/USDT"): {
        "position_usd": 2000.0,   # 做多 2000 USD
        "ratio": 1.0,
    },
    ("binance/spot", "ETH/USDT"): {
        "position_usd": -2000.0,  # 做空 2000 USD
        "ratio": -1.0,
    },
}
```

**执行结果**：
- OKX 买入 1 ETH（2000 USD）
- Binance 卖出 1 ETH（2010 USD）+ 做空 1 ETH 合约
- 净收益：10 USD（0.5%）


### 案例 2：WBETH/ETH 组内套利

**市场情况**：
- OKX WBETH/USDT: 1990 USD
- OKX ETH/USDT: 2000 USD
- Binance ETH/USDT: 2010 USD

**策略输出**：
```python
{
    ("okx/main", "WBETH/USDT"): {
        "position_usd": 2000.0,   # 做多 WBETH
        "ratio": 1.0,
    },
    ("okx/main", "ETH/USDT"): {
        "position_usd": 0.0,      # 不持仓
        "ratio": 0.0,
    },
    ("binance/spot", "ETH/USDT"): {
        "position_usd": -2000.0,  # 做空 ETH
        "ratio": -1.0,
    },
}
```

**执行结果**：
- OKX 买入 WBETH（最低价）
- Binance 做空 ETH（最高价）
- 捕获 1% 价差（20 USD）


## 注意事项

### 1. 风险控制

- **价差阈值**：合理设置 `entry_price_threshold` 和 `exit_price_threshold`，避免频繁交易
- **仓位限制**：通过 `max_position_usd` 控制单个分组的最大仓位
- **分组数量**：通过 `max_trading_pair_groups` 限制同时持有的分组数量

### 2. 交易成本

- **手续费**：考虑交易手续费对收益的影响
- **滑点**：大额订单可能产生滑点
- **资金费率**：合约持仓需要支付资金费率

### 3. 市场风险

- **价格波动**：套利过程中价格可能快速变化
- **流动性**：确保交易对有足够的流动性
- **转账时间**：跨平台套利需要考虑链上转账时间


## 最佳实践

### 1. 阈值设置

```yaml
# 保守策略（低频交易）
entry_price_threshold: 0.002   # 0.2%
exit_price_threshold: 0.001    # 0.1%

# 激进策略（高频交易）
entry_price_threshold: 0.0005  # 0.05%
exit_price_threshold: 0.0002   # 0.02%
```

### 2. 分组配置

```yaml
# 将相关资产映射到同一组
trading_pair_group:
  WBETH/USDT: ETH
  STETH/USDT: ETH
  CBETH/USDT: ETH
  WBTC/USDT: BTC
  BTCB/USDT: BTC
```

### 3. 权重配置

```yaml
# 根据交易所流动性和手续费设置权重
scopes:
  g:
    vars:
      - weights={"okx/main": 0.6, "binance/spot": 0.4}
```


## 运行示例

### 启动策略

```bash
# 使用配置文件启动
hft -p <password> run main eth_arbitrage
```

### 监控输出

策略会输出目标仓位：

```
[INFO] MarketNeutralPositions: Selected 2 groups
[INFO] Group ETH: score=0.010, pairs=3
[INFO]   - okx/main WBETH/USDT: ratio=1.0, position_usd=2000.0
[INFO]   - okx/main ETH/USDT: ratio=0.0, position_usd=0.0
[INFO]   - binance/spot ETH/USDT: ratio=-1.0, position_usd=-2000.0
```


## 故障排查

### 问题 1：没有输出目标仓位

**可能原因**：
- 价差未达到 `entry_price_threshold`
- `score < score_threshold`
- 交易对数量 < 2（无法套利）

**解决方案**：
- 降低阈值
- 检查行情数据是否正常
- 增加交易对数量

### 问题 2：Ratio 总和不为 0

**可能原因**：
- 代码 bug（应该不会出现）

**解决方案**：
- 检查日志，报告问题

### 问题 3：FairPriceIndicator 返回 None

**可能原因**：
- TickerDataSource 未就绪
- 行情数据过期

**解决方案**：
- 检查 TickerDataSource 配置
- 检查网络连接

## 相关文档

- [Feature 0013: MarketNeutralPositions 策略](../features/0013-market-neutral-positions-strategy.md)
- [Feature 0012: Scope 系统](../features/0012-scope-system.md)
- [docs/strategy.md](../docs/strategy.md)
- [docs/scope.md](../docs/scope.md)
