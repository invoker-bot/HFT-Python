import asyncio
import traceback

import typer
from rich.table import Table

from ..core.app.config import AppConfig
from ..exchange import BaseExchangeConfig
from ..test.exchange import test_exchange_async
from .config import console

app = typer.Typer()
test_group = typer.Typer(help="Test commands")
app.add_typer(test_group, name="test")

# 稳定币列表，价格按 1:1 计算
STABLE_COINS = {'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'USD', 'FDUSD'}


@app.command()
def main(app_name: str):
    app_config: AppConfig = AppConfig.load_from_path(app_name)
    app_core = app_config.instance  # AppCore(app_config)
    app_core.loop()


@app.command()
def balance(app_name: str):
    """
    Fetch and display account balances from all configured exchanges.

    Args:
        app_name: Name of the application config file (without .yaml extension)
    """
    asyncio.run(balance_async(app_name))


async def balance_async(app_name: str):
    """Async implementation of balance command"""
    try:
        # Load application config
        app_config = AppConfig.load(app_name)
        console.print(f"[green]Loaded app config: {app_config.class_name}[/green]")

        # 获取 exchange 配置映射
        exchange_id_map = app_config.exchanges.get_id_map()

        if not exchange_id_map:
            console.print("[yellow]No exchanges configured in app config[/yellow]")
            return

        # Create table for displaying balances
        table = Table(title=f"Account Balances - {app_config.class_name}")
        table.add_column("Exchange", style="cyan", no_wrap=True)
        table.add_column("Currency", style="magenta")
        table.add_column("Free", style="green", justify="right")
        table.add_column("Used", style="yellow", justify="right")
        table.add_column("Total", style="blue", justify="right")
        used = 0.0
        free = 0.0
        total = 0.0
        # Load each exchange and fetch balances
        for exchange_id, exchange_path in exchange_id_map.items():
            exchange_config = exchange_path.instance
            exchange = exchange_config.instance
            console.print(f"[cyan]Fetching balance from {exchange_id}...[/cyan]")
            try:
                balance_data = await exchange.fetch_balance()
                # Display balances for currencies with non-zero total
                for currency, amounts in balance_data.items():
                    if currency == 'USDT' and isinstance(amounts, dict) and 'total' in amounts:
                        _total = amounts.get('total', 0)
                        if _total > 1:
                            _free = amounts.get('free', 0)
                            _used = amounts.get('used', 0)
                            table.add_row(
                                exchange_id,
                                currency,
                                f"{_free:.8f}",
                                f"{_used:.8f}",
                                f"{_total:.8f}"
                            )
                            free += _free
                            used += _used
                            total += _total
            except FileNotFoundError:
                console.print(f"[red]Exchange config not found: {exchange_id}[/red]")
            except Exception as e:
                console.print(f"[red]Error fetching balance from {exchange_id}: {str(e)}[/red]")
            finally:
                # Always close the exchange connection
                await exchange.close()
        table.add_row(
            "[bold]Total[/bold]",
            "[bold]USDT[/bold]",
            f"[bold]{free:.8f}[/bold]",
            f"[bold]{used:.8f}[/bold]",
            f"[bold]{total:.8f}[/bold]"
        )
        # Display the table
        console.print(table)

    except FileNotFoundError:
        console.print(f"[red]App config not found: {app_name}[/red]")
        console.print(f"[yellow]Make sure conf/{app_name}.yaml exists[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        if app_config and app_config.debug:
            console.print(traceback.format_exc())


# ========== Test Commands ==========

@test_group.command(name="exchange")
def test_exchange(
    path: str,
    allow_orders: bool = typer.Option(
        False,
        "--allow-orders",
        help="Allow placing test market orders (spot and swap). Use with caution.",
    ),
):
    """
    Test exchange API connectivity and latency.

    Args:
        path: Exchange config path (e.g., 'binance/main')
    """
    asyncio.run(test_exchange_async(path, allow_orders=allow_orders))


# ========== Exchange Status Command ==========

@app.command()
def exchange(path: str):
    """
    Display exchange account status including positions and balances.

    Args:
        path: Exchange config path (e.g., 'okx/main', 'binance/futures')
    """
    asyncio.run(exchange_status_async(path))


async def exchange_status_async(path: str):
    """Async implementation of exchange status command"""
    exchange = None
    try:
        # 1. 加载配置并创建实例
        config = BaseExchangeConfig.load(path)
        exchange = config.instance

        # 显示标题
        account_type = "Unified Account" if exchange.unified_account else "Separate Accounts"
        console.print()
        console.print(f"[bold blue]Exchange:[/bold blue] [cyan]{path}[/cyan] ({account_type})")
        console.print("[dim]" + "-" * 50 + "[/dim]")

        # 2. 加载市场数据
        await exchange.load_markets()

        # 3. 查询数据
        positions = await exchange.fetch_positions()
        balances = await _fetch_balances(exchange)
        prices = await _fetch_prices(exchange, balances)
        total_usd = await exchange.medal_fetch_total_balance_usd()

        # 4. 渲染输出
        console.print()
        if exchange.unified_account:
            _render_unified_account(positions, balances.get('unified', {}), prices)
        else:
            _render_separate_accounts(positions, balances, prices)

        # 5. 总价值
        console.print()
        console.print(f"[bold green]Total Value: ${total_usd:,.2f}[/bold green]")
        console.print()

    except FileNotFoundError:
        console.print(f"[red]Exchange config not found: {path}[/red]")
        console.print(f"[yellow]Make sure conf/exchange/{path}.yaml exists[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        console.print(traceback.format_exc())
    finally:
        if exchange:
            await exchange.close()


async def _fetch_balances(exchange) -> dict[str, dict]:
    """获取账户余额"""
    result = {}

    if exchange.unified_account:
        # 统一账户：只查一次（使用任意一个 ccxt 实例）
        ccxt_instance = exchange.config.ccxt_instance
        balance = await ccxt_instance.fetch_balance()
        result['unified'] = balance
    else:
        # 分离账户：分别查询
        if 'swap' in exchange.exchanges:
            result['swap'] = await exchange.exchanges['swap'].fetch_balance()
        if 'spot' in exchange.exchanges:
            result['spot'] = await exchange.exchanges['spot'].fetch_balance()

    return result


async def _fetch_prices(exchange, balances: dict) -> dict[str, float]:
    """批量获取币种价格（用于 USD 估值）"""
    # 收集所有需要查价的币种
    currencies = set()
    for balance in balances.values():
        for currency, amounts in balance.items():
            if isinstance(amounts, dict) and amounts.get('total', 0) > 0:
                if currency not in STABLE_COINS:
                    currencies.add(currency)

    if not currencies:
        return {}

    prices = {}

    # 获取可用的交易所实例
    swap_instance = exchange.exchanges.get('swap')
    spot_instance = exchange.exchanges.get('spot')
    default_instance = exchange.config.ccxt_instance

    # 逐个获取价格（更可靠，避免批量请求失败）
    for currency in currencies:
        # 优先尝试 swap 市场（如果有 swap 实例）
        if swap_instance:
            try:
                symbol = f"{currency}/USDT:USDT"
                ticker = await swap_instance.fetch_ticker(symbol)
                if ticker and ticker.get('last'):
                    prices[currency] = ticker['last']
                    continue
            except Exception:
                pass

        # 回退到现货市场（优先用 spot 实例，其次用默认实例）
        spot_ccxt = spot_instance or default_instance
        if spot_ccxt:
            try:
                symbol = f"{currency}/USDT"
                ticker = await spot_ccxt.fetch_ticker(symbol)
                if ticker and ticker.get('last'):
                    prices[currency] = ticker['last']
            except Exception:
                pass

    return prices


def _render_positions_table(positions: list, title: str = "Positions") -> None:
    """渲染持仓表格"""
    # 过滤空仓位
    active_positions = [p for p in positions if float(p.get('contracts', 0)) != 0]

    if not active_positions:
        console.print(f"[dim]{title}: No open positions[/dim]")
        return

    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Side", style="bold", justify="center")
    table.add_column("Amount", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Value (USD)", justify="right")
    table.add_column("PnL", justify="right")

    total_pnl = 0.0
    for pos in active_positions:
        symbol = pos.get('symbol', 'N/A')
        side = pos.get('side', 'N/A').upper()
        contracts = float(pos.get('contracts', 0))
        entry_price = float(pos.get('entryPrice', 0) or 0)
        notional = abs(float(pos.get('notional', 0) or 0))
        pnl = float(pos.get('unrealizedPnl', 0) or 0)
        total_pnl += pnl

        # 格式化
        side_color = "green" if side == "LONG" else "red"
        pnl_color = "green" if pnl >= 0 else "red"
        pnl_str = f"+{pnl:,.2f}" if pnl >= 0 else f"{pnl:,.2f}"

        table.add_row(
            symbol,
            f"[{side_color}]{side}[/{side_color}]",
            f"{contracts:,.4f}",
            f"{entry_price:,.2f}",
            f"{notional:,.2f}",
            f"[{pnl_color}]{pnl_str}[/{pnl_color}]"
        )

    # 添加 PnL 汇总行
    pnl_color = "green" if total_pnl >= 0 else "red"
    pnl_str = f"+{total_pnl:,.2f}" if total_pnl >= 0 else f"{total_pnl:,.2f}"
    table.add_row(
        "[bold]Total PnL[/bold]", "", "", "", "",
        f"[bold {pnl_color}]{pnl_str}[/bold {pnl_color}]"
    )

    console.print(table)


def _render_balance_table(balance: dict, prices: dict, title: str = "Balance") -> float:
    """渲染余额表格，返回小计"""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Currency", style="cyan", no_wrap=True)
    table.add_column("Amount", justify="right")
    table.add_column("Value (USD)", justify="right")

    subtotal = 0.0
    rows = []

    for currency, amounts in balance.items():
        if not isinstance(amounts, dict):
            continue

        total_amount = amounts.get('total', 0) or 0
        if total_amount <= 0:
            continue

        # 计算 USD 价值
        if currency in STABLE_COINS:
            usd_value = total_amount
        elif currency in prices:
            usd_value = total_amount * prices[currency]
        else:
            usd_value = 0  # 无法估值

        # 过滤小额（< $1）
        if usd_value < 1:
            continue

        subtotal += usd_value
        rows.append((currency, total_amount, usd_value))

    # 按 USD 价值排序
    rows.sort(key=lambda x: x[2], reverse=True)

    for currency, amount, usd_value in rows:
        # 格式化数量：最多5位小数，去除末尾0
        if amount >= 1:
            amount_str = f"{amount:,.5f}".rstrip('0').rstrip('.')
        else:
            amount_str = f"{amount:.5g}"  # 小数用科学计数法或有效数字
        table.add_row(
            currency,
            amount_str,
            f"{usd_value:,.2f}"
        )

    if rows:
        table.add_row(
            "[bold]Subtotal[/bold]", "",
            f"[bold]{subtotal:,.2f}[/bold]"
        )
        console.print(table)
    else:
        console.print(f"[dim]{title}: No significant balance[/dim]")

    return subtotal


def _render_unified_account(positions: list, balance: dict, prices: dict) -> None:
    """渲染统一账户"""
    _render_positions_table(positions, "Positions (Contract)")
    console.print()
    _render_balance_table(balance, prices, "Balance")


def _render_separate_accounts(positions: list, balances: dict, prices: dict) -> None:
    """渲染分离账户"""
    # Swap 账户
    if 'swap' in balances:
        console.print("[bold blue]== Swap Account ==[/bold blue]")
        console.print()
        _render_positions_table(positions, "Positions")
        console.print()
        subtotal = _render_balance_table(balances['swap'], prices, "Balance")
        console.print(f"[dim]Subtotal: ${subtotal:,.2f}[/dim]")
        console.print()

    # Spot 账户
    if 'spot' in balances:
        console.print("[bold blue]== Spot Account ==[/bold blue]")
        console.print()
        subtotal = _render_balance_table(balances['spot'], prices, "Balance")
        console.print(f"[dim]Subtotal: ${subtotal:,.2f}[/dim]")
