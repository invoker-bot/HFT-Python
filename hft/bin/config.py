import typer
from rich.console import Console
# from ..exchanges.extend import BaseExchangeConfig
# from ..strategy.extend import BaseStrategyConfig
from ..config.app import AppConfig

app = typer.Typer()
gen_group = typer.Typer()
show_group = typer.Typer()
app.add_typer(gen_group, name="gen")
app.add_typer(show_group, name="show")
password_option = typer.Option(..., "--password", "-p", help="Password to encrypt sensitive information", prompt=True, hide_input=True,
                               envvar="CROSSBOT_PASSWORD")
console = Console(width=300)


# @app.command()
# def exchange():  # password: str = password_option):
#     """
#     Generate exchange configuration file.
#     """
#     config_obj = BaseExchangeConfig.prompt_for_config()
#     config_obj.save()
#     typer.echo(f"Exchange configuration saved to {config_obj.config_path()}")
# 
# 
# @app.command()
# def strategy():
#     """
#     Generate strategy configuration file.
#     """
#     config_obj = BaseStrategyConfig.prompt_for_config()
#     print(config_obj.class_name)
#     config_obj.save()
#     typer.echo(f"Strategy configuration saved to {config_obj.config_path()}")


@gen_group.command(name="app")
def gen_application():
    """
    Generate application configuration file.
    """
    typer.echo("Generating application configuration...")
    config_obj = AppConfig.prompt_for_config()
    config_obj.save()
    typer.echo(f"Application configuration saved to {config_obj.abs_path}")


@show_group.command(name="app")
def show_application(pathname: str):
    """
    Show application configuration file.
    """
    typer.echo("Showing application configuration...")
    config_obj = AppConfig.load(pathname)
    typer.echo(f"config class: {config_obj.class_name}")
    console.print(config_obj.model_dump_json(indent=4))


if __name__ == "__main__":
    app()
