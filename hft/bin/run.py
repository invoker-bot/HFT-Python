import asyncio
import traceback
import typer
from rich.table import Table
from .config import console
from ..config import AppConfig
from ..exchange import (
    BaseExchangeConfig,
    BinanceExchangeConfig,  # noqa: F401 - 注册子类
    OKXExchangeConfig,      # noqa: F401 - 注册子类
)
from ..core.app import AppCore
from ..test.exchange import test_exchange_async


app = typer.Typer()
test_group = typer.Typer(help="Test commands")
app.add_typer(test_group, name="test")


@app.command()
def main(app_name: str):
    app_config: AppConfig = AppConfig.load_from_path(app_name)
    app_core = app_config.instance  # AppCore(app_config)
    app_core.loop()
    # trade_core: TradeCore = TradeCore(app_config)
    # trade_core.loop()


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

        if not app_config.exchanges:
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
        for exchange_name, exchange in app_config.exchange_instances.items():
            console.print(f"[cyan]Fetching balance from {exchange_name}...[/cyan]")
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
                                exchange_name,
                                currency,
                                f"{_free:.8f}",
                                f"{_used:.8f}",
                                f"{_total:.8f}"
                            )
                            free += _free
                            used += _used
                            total += _total
            except FileNotFoundError:
                console.print(f"[red]Exchange config not found: {exchange_name}[/red]")
            except Exception as e:
                console.print(f"[red]Error fetching balance from {exchange_name}: {str(e)}[/red]")
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
def test_exchange(path: str):
    """
    Test exchange API connectivity and latency.

    Args:
        path: Exchange config path (e.g., 'binance/main')
    """
    asyncio.run(test_exchange_async(path))
