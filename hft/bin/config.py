import typer
from rich.console import Console
from ..exchange import BaseExchangeConfig
from ..strategy.config import BaseStrategyConfig
from ..strategy.static_positions import StaticPositionsStrategyConfig  # noqa: F401 - 注册子类
from ..strategy.market_neutral_positions import MarketNeutralPositionsConfig  # noqa: F401 - 注册子类
from ..core.app.config import AppConfig

app = typer.Typer()
gen_group = typer.Typer()
show_group = typer.Typer()
app.add_typer(gen_group, name="gen")
app.add_typer(show_group, name="show")
password_option = typer.Option(..., "--password", "-p", help="Password to encrypt sensitive information", prompt=True, hide_input=True,
                               envvar="CROSSBOT_PASSWORD")
console = Console(width=300)


@gen_group.command()
def exchange():
    """
    Generate exchange configuration file.
    """
    typer.echo("Generating exchange configuration...")
    config_obj = BaseExchangeConfig.prompt_for_config()
    config_obj.save()
    typer.echo(f"Exchange configuration saved to {config_obj.get_abs_path()}")


@gen_group.command()
def strategy():
    """
    Generate strategy configuration file.
    """
    typer.echo("Generating strategy configuration...")
    config_obj = BaseStrategyConfig.prompt_for_config()
    config_obj.save()
    typer.echo(f"Strategy configuration saved to {config_obj.get_abs_path()}")


@gen_group.command(name="app")
def gen_application():
    """
    Generate application configuration file.
    """
    typer.echo("Generating application configuration...")
    config_obj = AppConfig.prompt_for_config()
    config_obj.save()
    typer.echo(f"Application configuration saved to {config_obj.get_abs_path()}")


@show_group.command(name="app")
def show_application(pathname: str):
    """
    Show application configuration file.

    Args:
        pathname: Name of the app config file (e.g., 'myapp')
    """
    typer.echo("Showing application configuration...")
    config_obj = AppConfig.load(pathname)
    typer.echo(f"config class: {config_obj.class_name}")
    console.print(config_obj.model_dump_json(indent=4))


@show_group.command(name="exchange")
def show_exchange(pathname: str):
    """
    Show exchange configuration file.

    Args:
        pathname: Exchange config path (e.g., 'binance/main')
    """
    typer.echo("Showing exchange configuration...")
    config_obj = BaseExchangeConfig.load(pathname)
    typer.echo(f"config class: {config_obj.class_name}")
    console.print(config_obj.model_dump_json(indent=4))


@show_group.command(name="strategy")
def show_strategy(pathname: str):
    """
    Show strategy configuration file.

    Args:
        pathname: Strategy config path (e.g., 'simple/btc')
    """
    typer.echo("Showing strategy configuration...")
    config_obj = BaseStrategyConfig.load(pathname)
    typer.echo(f"config class: {config_obj.class_name}")
    console.print(config_obj.model_dump_json(indent=4))


if __name__ == "__main__":
    app()
