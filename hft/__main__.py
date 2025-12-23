from rich.console import Console
from rich.logging import RichHandler
import typer
import pandas as pd
from os import makedirs
import logging
import warnings
from .config.crypto import init_fernet
from .bin.run import app as run_app
from .bin.config import app as config_app, password_option
# from .bin.history import app as history_app
from ._version import __appname__

# Suppress Pydantic warnings about field name shadowing
warnings.filterwarnings('ignore', message='.*shadows an attribute.*', category=UserWarning)


app = typer.Typer(name=__appname__, help="High-Frequency Trading Bot CLI")
app.add_typer(config_app, name="config")
app.add_typer(run_app, name="run")
# app.add_typer(history_app, name="history")


@app.callback()
def app_callback(password: str = password_option, debug: bool = False):
    """
    CrossBot Command Line Interface
    """
    for dir_path in ["logs", "data", "conf/exchanges"]:
        makedirs(dir_path, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO if not debug else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[RichHandler(console=Console(width=200, force_terminal=False), show_path=False)]
    )
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_colwidth", None)
    pd.set_option("display.expand_frame_repr", False)
    init_fernet(password)


def main():
    app()


if __name__ == "__main__":
    main()
