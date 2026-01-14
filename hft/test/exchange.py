"""
交易所 API 测试模块
"""
import asyncio
import time
import traceback
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.console import Console
from ..exchange import BaseExchangeConfig

console = Console()

# 测试交易对
TEST_SYMBOLS = [
    ("BTC/USDT:USDT", "BTC/USDT"),   # (futures, spot)
    ("ETH/USDT:USDT", "ETH/USDT"),
]


async def test_exchange_async(path: str) -> None:
    """
    Test exchange connectivity and API latency.

    Args:
        path: Exchange config path (e.g., 'binance/main')
    """
    results: list[dict] = []

    try:
        # 加载交易所配置
        config = BaseExchangeConfig.load(path)
        exchange = config.instance
        exchange_name = config.class_name

        console.print(Panel(
            f"[bold cyan]Testing Exchange: {exchange_name}[/bold cyan]\n"
            f"Config: {path}",
            title="Exchange API Test",
        ))

        # 初始化
        console.print("\n[yellow]Initializing exchange...[/yellow]")
        start = time.perf_counter()
        await exchange.load_markets()
        init_time = (time.perf_counter() - start) * 1000
        results.append({
            "api": "load_markets",
            "symbol": "-",
            "status": "OK",
            "latency": init_time,
        })
        console.print(f"  [green]+[/green] load_markets: {init_time:.1f}ms")

        # 确定使用的交易对（根据交易所支持）
        markets = exchange._markets or {}
        test_pairs = []
        for futures_sym, spot_sym in TEST_SYMBOLS:
            if futures_sym in markets:
                test_pairs.append(futures_sym)
            elif spot_sym in markets:
                test_pairs.append(spot_sym)

        if not test_pairs:
            test_pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

        console.print(f"\n[yellow]Test symbols: {', '.join(test_pairs)}[/yellow]\n")

        # ========== REST API Tests ==========
        console.print("[bold]REST API Tests[/bold]")

        # 1. fetch_balance
        await _test_api(
            results, "fetch_balance", "-",
            exchange.fetch_balance
        )

        # 2. fetch_ticker
        for symbol in test_pairs:
            await _test_api(
                results, "fetch_ticker", symbol,
                lambda s=symbol: exchange.fetch_ticker(s)
            )

        # 3. fetch_order_book
        for symbol in test_pairs:
            await _test_api(
                results, "fetch_order_book", symbol,
                lambda s=symbol: exchange.fetch_order_book(s, limit=10)
            )

        # 4. fetch_trades
        for symbol in test_pairs:
            await _test_api(
                results, "fetch_trades", symbol,
                lambda s=symbol: exchange.fetch_trades(s, limit=10)
            )

        # 5. fetch_ohlcv
        for symbol in test_pairs:
            await _test_api(
                results, "fetch_ohlcv", symbol,
                lambda s=symbol: exchange.fetch_ohlcv(s, '1m', limit=10)
            )

        # 6. fetch_positions (futures only)
        await _test_api(
            results, "fetch_positions", "-",
            exchange.fetch_positions
        )

        # 7. fetch_open_orders
        for symbol in test_pairs[:1]:  # 只测试一个交易对
            await _test_api(
                results, "fetch_open_orders", symbol,
                lambda s=symbol: exchange.fetch_open_orders(s)
            )

        # 8. fetch_funding_rate (if available)
        for symbol in test_pairs:
            if ':' in symbol:  # futures symbol
                await _test_api(
                    results, "fetch_funding_rate", symbol,
                    lambda s=symbol: exchange.fetch_funding_rate(s)
                )

        # ========== WebSocket Tests ==========
        console.print("\n[bold]WebSocket Tests[/bold]")

        # 9. watch_ticker
        for symbol in test_pairs:
            await _test_ws_api(
                results, "watch_ticker", symbol,
                lambda s=symbol: exchange.watch_ticker(s),
                timeout=10.0
            )

        # 10. watch_order_book
        for symbol in test_pairs:
            await _test_ws_api(
                results, "watch_order_book", symbol,
                lambda s=symbol: exchange.watch_order_book(s, limit=10),
                timeout=10.0
            )

        # 11. watch_trades
        for symbol in test_pairs:
            await _test_ws_api(
                results, "watch_trades", symbol,
                lambda s=symbol: exchange.watch_trades(s),
                timeout=10.0
            )

        # ========== Print Report ==========
        _print_test_report(exchange_name, results)

    except FileNotFoundError:
        console.print(f"[red]Exchange config not found: {path}[/red]")
        console.print(f"[yellow]Make sure conf/exchange/{path}.yaml exists[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        console.print(traceback.format_exc())
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


async def _test_api(results: list, api_name: str, symbol: str, func) -> None:
    """Test a REST API call"""
    try:
        start = time.perf_counter()
        await func()
        latency = (time.perf_counter() - start) * 1000
        results.append({
            "api": api_name,
            "symbol": symbol,
            "status": "OK",
            "latency": latency,
        })
        console.print(f"  [green]+[/green] {api_name} [{symbol}]: {latency:.1f}ms")
    except Exception as e:
        results.append({
            "api": api_name,
            "symbol": symbol,
            "status": f"FAIL: {str(e)[:50]}",
            "latency": None,
        })
        console.print(f"  [red]X[/red] {api_name} [{symbol}]: {str(e)[:60]}")


async def _test_ws_api(
    results: list,
    api_name: str,
    symbol: str,
    func,
    timeout: float = 10.0
) -> None:
    """Test a WebSocket API call"""
    try:
        start = time.perf_counter()
        await asyncio.wait_for(func(), timeout=timeout)
        latency = (time.perf_counter() - start) * 1000
        results.append({
            "api": api_name,
            "symbol": symbol,
            "status": "OK",
            "latency": latency,
        })
        console.print(f"  [green]+[/green] {api_name} [{symbol}]: {latency:.1f}ms")
    except asyncio.TimeoutError:
        results.append({
            "api": api_name,
            "symbol": symbol,
            "status": f"TIMEOUT ({timeout}s)",
            "latency": None,
        })
        console.print(f"  [yellow]![/yellow] {api_name} [{symbol}]: Timeout ({timeout}s)")
    except Exception as e:
        results.append({
            "api": api_name,
            "symbol": symbol,
            "status": f"FAIL: {str(e)[:50]}",
            "latency": None,
        })
        console.print(f"  [red]X[/red] {api_name} [{symbol}]: {str(e)[:60]}")


def _print_test_report(exchange_name: str, results: list) -> None:
    """Print test results summary"""
    console.print("\n")

    # 统计
    total = len(results)
    ok_count = sum(1 for r in results if r["status"] == "OK")
    fail_count = total - ok_count
    latencies = [r["latency"] for r in results if r["latency"] is not None]

    # 结果表格
    table = Table(title=f"API Test Results - {exchange_name}")
    table.add_column("API", style="cyan")
    table.add_column("Symbol", style="white")
    table.add_column("Status", style="white")
    table.add_column("Latency", style="yellow", justify="right")

    for r in results:
        status_style = "green" if r["status"] == "OK" else "red"
        latency_str = f"{r['latency']:.1f}ms" if r["latency"] else "-"
        table.add_row(
            r["api"],
            r["symbol"],
            Text(r["status"], style=status_style),
            latency_str,
        )

    console.print(table)

    # 统计摘要
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)

        summary = Table(title="Summary", show_header=False, box=None)
        summary.add_column("Metric", style="cyan")
        summary.add_column("Value", style="white")

        success_rate = ok_count / total * 100
        success_color = "green" if success_rate >= 90 else "yellow" if success_rate >= 70 else "red"

        summary.add_row("Total Tests", str(total))
        summary.add_row("Passed", f"[green]{ok_count}[/green]")
        summary.add_row("Failed", f"[red]{fail_count}[/red]" if fail_count else "[green]0[/green]")
        summary.add_row("Success Rate", f"[{success_color}]{success_rate:.1f}%[/{success_color}]")
        summary.add_row("", "")
        summary.add_row("Avg Latency", f"{avg_latency:.1f}ms")
        summary.add_row("Min Latency", f"{min_latency:.1f}ms")
        summary.add_row("Max Latency", f"{max_latency:.1f}ms")

        # 延迟评级
        if avg_latency < 100:
            rating = "[green]Excellent[/green]"
        elif avg_latency < 300:
            rating = "[green]Good[/green]"
        elif avg_latency < 500:
            rating = "[yellow]Fair[/yellow]"
        else:
            rating = "[red]Poor[/red]"
        summary.add_row("Rating", rating)

        console.print(summary)
