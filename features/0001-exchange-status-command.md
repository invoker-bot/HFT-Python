# Feature: Exchange Status 命令

> **状态**: ✅ 已完成，审核通过
> **提交**: 51a5afc, 27add73

## 背景与目标

提供 `hft run exchange <exchange_name>` 命令，快速查看指定交易所的账户状态，包括：
- 合约持仓（Positions）
- 账户余额（Balance）
- 总价值估算（USD）

## 命令格式

```bash
hft run exchange <exchange_config_path>
# 示例：
hft run exchange okx/main
hft run exchange binance/futures
```

## 账户模型

不同交易所有不同的账户结构：

| 类型 | 交易所示例 | 特点 |
|------|-----------|------|
| Unified Account | OKX | 现货和合约共用一个账户，资金互通 |
| Separate Accounts | Binance | 现货账户和合约账户分离，需分别查询 |

### 平台特性标识

在 `BaseExchange` 类中添加类属性（这是平台固有特性，不是可配置项）：

```python
# hft/exchange/base.py
class BaseExchange(Listener):
    # 类属性：是否为统一账户模式（子类覆盖）
    unified_account: ClassVar[bool] = False
```

交易所子类按平台实际情况覆盖：
```python
# hft/exchange/okx.py
class OKXExchange(BaseExchange):
    unified_account: ClassVar[bool] = True

# hft/exchange/binance.py
class BinanceExchange(BaseExchange):
    unified_account: ClassVar[bool] = False  # 默认值，可省略
```

## 输出格式

### Unified Account（统一账户，如 OKX）

```
Exchange: okx/main (Unified Account)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Positions (Contract)
┌──────────────────┬───────┬──────────┬────────────┬────────────┬──────────┐
│ Symbol           │ Side  │ Amount   │ Entry      │ Value (USD)│ PnL      │
├──────────────────┼───────┼──────────┼────────────┼────────────┼──────────┤
│ BTC/USDT:USDT    │ LONG  │ 0.5      │ 48,000.00  │ 25,000.00  │ +500.00  │
│ ETH/USDT:USDT    │ SHORT │ 2.0      │ 3,100.00   │ 6,000.00   │ -120.00  │
└──────────────────┴───────┴──────────┴────────────┴────────────┴──────────┘

💰 Balance
┌──────────────────┬──────────┬──────────────┐
│ Currency         │ Amount   │ Value (USD)  │
├──────────────────┼──────────┼──────────────┤
│ USDT             │ 10,000   │ 10,000.00    │
│ BTC              │ 0.1      │ 5,000.00     │
└──────────────────┴──────────┴──────────────┘

📈 Total Value: $34,000.00
```

### Separate Accounts（分离账户，如 Binance）

```
Exchange: binance/main (Separate Accounts)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Swap Account
┌─ Positions ──────────────────────────────────────────────────────────────┐
│ Symbol           │ Side  │ Amount   │ Entry      │ Value (USD)│ PnL      │
├──────────────────┼───────┼──────────┼────────────┼────────────┼──────────┤
│ BTC/USDT:USDT    │ LONG  │ 0.5      │ 48,000.00  │ 25,000.00  │ +500.00  │
│ ETH/USDT:USDT    │ SHORT │ 2.0      │ 3,100.00   │ 6,000.00   │ -120.00  │
└──────────────────┴───────┴──────────┴────────────┴────────────┴──────────┘
┌─ Balance ───────────────────────────────┐
│ Currency         │ Amount   │ Value (USD)│
├──────────────────┼──────────┼────────────┤
│ USDT             │ 5,000    │ 5,000.00   │
└──────────────────┴──────────┴────────────┘
Subtotal: $24,000.00

💰 Spot Account
┌─ Balance ───────────────────────────────┐
│ Currency         │ Amount   │ Value (USD)│
├──────────────────┼──────────┼────────────┤
│ USDT             │ 8,000    │ 8,000.00   │
│ BTC              │ 0.1      │ 5,000.00   │
│ ETH              │ 1.5      │ 4,500.00   │
└──────────────────┴──────────┴────────────┘
Subtotal: $17,500.00

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 Total Value: $41,500.00
```

## 实现方案

### 1. Exchange 类修改

**文件**: `hft/exchange/base.py`

```python
class BaseExchange(Listener):
    unified_account: ClassVar[bool] = False  # 子类覆盖
```

### 2. CLI 命令入口

**文件**: `hft/cli/commands/run.py`（或新建 `exchange.py`）

```python
async def run_exchange_status(exchange_path: str, password: str):
    """查询并展示交易所账户状态"""
    # 1. 加载配置
    config = BaseExchangeConfig.load(exchange_path)
    exchange = config.instance

    # 2. 初始化交易所连接
    await exchange.initialize()

    # 3. 查询数据
    positions = await fetch_positions(exchange)
    balances = await fetch_balances(exchange)
    prices = await fetch_prices(exchange, balances)
    total_usd = await exchange.medal_fetch_total_balance_usd()

    # 4. 渲染输出
    render_exchange_status(exchange, positions, balances, prices, total_usd)

    # 5. 清理
    await exchange.close()
```

### 3. 数据查询逻辑

```python
async def fetch_positions(exchange: BaseExchange) -> list[Position]:
    """获取合约持仓原始数据（包含 side, entryPrice, unrealizedPnl 等）"""
    return await exchange.fetch_positions()

async def fetch_balances(exchange: BaseExchange) -> dict[str, dict]:
    """获取账户余额"""
    result = {}

    if exchange.unified_account:
        # 统一账户：只查一次
        balance = await exchange.fetch_balance()
        result['unified'] = balance
    else:
        # 分离账户：分别查询
        if 'swap' in exchange.exchanges:
            result['swap'] = await exchange.exchanges['swap'].fetch_balance()
        if 'spot' in exchange.exchanges:
            result['spot'] = await exchange.exchanges['spot'].fetch_balance()

    return result

async def fetch_prices(exchange: BaseExchange, balances: dict) -> dict[str, float]:
    """批量获取币种价格（用于 USD 估值）"""
    # 收集所有需要查价的币种
    currencies = set()
    for balance in balances.values():
        for currency in balance.get('total', {}).keys():
            if currency not in STABLE_COINS:
                currencies.add(currency)

    # 构造交易对并批量查询
    symbols = [f"{c}/USDT" for c in currencies]
    tickers = await exchange.fetch_tickers(symbols)

    return {symbol.split('/')[0]: ticker['last'] for symbol, ticker in tickers.items()}
```

### 4. 渲染层

**文件**: `hft/cli/render/exchange_status.py`

使用 `rich` 库渲染表格：

```python
from rich.console import Console
from rich.table import Table

def render_exchange_status(exchange, positions, balances, prices, total_usd):
    console = Console()

    # 标题
    account_type = "Unified Account" if exchange.unified_account else "Separate Accounts"
    console.print(f"\nExchange: {exchange.config.path} ({account_type})")
    console.print("━" * 50)

    if exchange.unified_account:
        render_unified_account(console, positions, balances['unified'], prices)
    else:
        render_separate_accounts(console, positions, balances, prices)

    # 总价值（使用 medal_fetch_total_balance_usd 获取）
    console.print("━" * 50)
    console.print(f"📈 Total Value: ${total_usd:,.2f}")
```

### 5. 数据字段说明

**Positions 表格字段**（来自 `fetch_positions()` 原始数据）：
| 字段 | 来源 | 说明 |
|------|------|------|
| Symbol | `position['symbol']` | 交易对 |
| Side | `position['side']` | 多空方向：LONG/SHORT |
| Amount | `position['contracts'] * contract_size` | 持仓数量（基础货币） |
| Entry | `position['entryPrice']` | 开仓均价 |
| Value | `amount * current_price` | 当前市值（USD） |
| PnL | `position['unrealizedPnl']` | 未实现盈亏 |

### 6. 过滤规则

- **小额过滤**：余额 USD 价值 < $1 的币种不显示
- **稳定币价格**：USDT, USDC, BUSD 等直接按 1:1 计算
- **空仓位过滤**：`contracts == 0` 的持仓不显示

## 文件结构

```
hft/
├── cli/
│   ├── commands/
│   │   └── run.py          # 添加 exchange 子命令
│   └── render/
│       └── exchange_status.py  # 新增：状态渲染
├── exchange/
│   ├── base.py             # 添加 unified_account 类属性
│   └── okx.py              # 覆盖 unified_account = True
```

---

## 修复报告（2026-01-14）

### 已完成工作

#### 1. 修复异常提示未格式化问题 ✅
- **文件**: `hft/bin/run.py:169`
- **问题**: 第 169 行缺少 f-string 前缀，`{path}` 占位符不会被替换
- **修复**:
  ```python
  # 修复前
  console.print("[yellow]Make sure conf/exchange/{path}.yaml exists[/yellow]")

  # 修复后
  console.print(f"[yellow]Make sure conf/exchange/{path}.yaml exists[/yellow]")
  ```

#### 2. 修复现货估值 spot-only 币种缺失问题 ✅
- **文件**: `hft/bin/run.py:197-241`
- **问题**:
  - 原代码统一使用 `exchange.exchanges.get('swap', exchange.config.ccxt_instance)` 查询所有币种价格
  - 对于只在 spot 账户存在的币种（如某些 spot-only 代币），用 swap 实例查询现货市场可能失败

- **修复逻辑**:
  ```python
  # 获取可用的交易所实例
  swap_instance = exchange.exchanges.get('swap')
  spot_instance = exchange.exchanges.get('spot')
  default_instance = exchange.config.ccxt_instance

  for currency in currencies:
      # 1. 优先尝试 swap 市场（如果有 swap 实例）
      if swap_instance:
          try swap market with f"{currency}/USDT:USDT"

      # 2. 回退到现货市场（优先用 spot 实例，其次用默认实例）
      spot_ccxt = spot_instance or default_instance
      if spot_ccxt:
          try spot market with f"{currency}/USDT"
  ```

- **改进点**:
  - 查询现货市场价格时优先使用 `spot_instance`（如果存在）
  - 确保 spot-only 币种能正确获取价格
  - 保持对 unified account 的兼容性（使用 default_instance）

### 影响文件
- `hft/bin/run.py` (行 169, 197-241)

### 审核结论
- f-string 修复正确，路径占位符可正常渲染。
- 价格查询优先 swap 实例，失败回落到 spot/默认实例，可覆盖 spot-only 币种。
- 未新增日志，保持与原实现一致，可视需求后续补充。

---

## 审核结果

- ✅ 通过审核：现货估值与提示格式问题已修复，功能项与测试项均已完成。

## 注意事项

1. **API 调用优化**：使用 `fetch_tickers()` 批量查价，避免逐个查询
2. **错误处理**：网络错误、API 限速等需要友好提示
3. **连接清理**：确保 WebSocket 连接正确关闭
