from typing import Optional
from rich.table import Table
from rich.console import Console
from .listener import Listener
from .._version import __version__


class StateLogger(Listener):

    def __init__(self, recursive: bool = True, interval: float = 300, console: Optional[Console] = None):
        super().__init__("state_logger", interval)
        self._recursive = recursive
        self._console = console or Console(width=300)

    async def tick_callback(self):
        start_str = self.to_date_string(self.start_time)
        current_str = self.to_date_string(self.current_time)
        duration_str = self.to_duration_string(self.current_time - self.start_time)
        self._console.print(f"\n[bold cyan][HFT][/bold cyan] [yellow][v{__version__}][/yellow]")
        self._console.print(f"start: {start_str}  current: {current_str}  duration: {duration_str}")
        table = Table(title="Listener Status", show_header=True, header_style="bold magenta")
        table.add_column("Listener", style="cyan", no_wrap=True)
        table.add_column("Ready", justify="center")
        table.add_column("Enabled", justify="center")
        table.add_column("Healthy", justify="center")
        table.add_column("State", justify="center")
        table.add_column("Uptime", justify="right")
        table.add_column("Tasks", justify="right")
        for listener in self.root:
            state = listener.state_dict
            enable_str = "✅" if state["enabled"] else "❌"
            ready_str = "✅" if state["ready"] else "❌"
            healthy_str = "✅" if state["healthy"] else "❌"
            state_str = str(state['state'])
            uptime_str = self.to_duration_string(state["uptime"])
            task_count_str = str(state["task_count"])
            table.add_row(listener.logger_name, ready_str, enable_str,
                          healthy_str, state_str, uptime_str, task_count_str)
        self._console.print(table)
        if self._recursive:
            self.root.log_state(self._console, self._recursive)
