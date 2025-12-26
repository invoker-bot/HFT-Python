"""
State Logger Listener

定期输出 Listener 树状态，使用树形目录格式显示
"""
from typing import Optional
from rich.console import Console
from .listener import Listener, ListenerState
from .._version import __version__


class StateLogListener(Listener):
    """
    状态日志监听器

    定期打印 Listener 树的状态，使用树形目录格式显示：
    📦 AppCore [running] ♥
    ├── StateLogListener [running] ♥
    ├── UnhealthyRestartListener [running] ♥
    └── Strategy [running] ♥
        ├── Controller [running] ♥
        └── Executor [running] ♥
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_console")

    def __init__(self, interval: float = 300, max_depth: int = 5, console: Optional[Console] = None):
        """
        Args:
            interval: 日志输出间隔（秒）
            max_depth: 最大显示深度
            console: Rich Console 实例
        """
        super().__init__(interval=interval)
        self._console = console or Console(width=300)
        self._max_depth = max_depth

    def __setstate__(self, state):
        super().__setstate__(state)
        self._console = Console(width=300)

    def _get_state_icon(self, listener: Listener) -> str:
        """获取状态图标"""
        state = listener._state
        if state == ListenerState.RUNNING:
            return "[green]●[/green]"
        elif state == ListenerState.STOPPED:
            return "[dim]○[/dim]"
        elif state == ListenerState.ERROR:
            return "[red]✗[/red]"
        elif state == ListenerState.STARTING:
            return "[yellow]◐[/yellow]"
        elif state == ListenerState.STOPPING:
            return "[yellow]◑[/yellow]"
        elif state == ListenerState.FINISHED:
            return "[blue]◉[/blue]"
        return "[dim]?[/dim]"

    def _get_health_icon(self, listener: Listener) -> str:
        """获取健康状态图标"""
        return "[green]♥[/green]" if listener.healthy else "[red]♡[/red]"

    def _print_tree(self, listener: Listener, prefix: str = "", is_last: bool = True, depth: int = 0) -> None:
        """
        递归打印 Listener 树

        Args:
            listener: 当前 Listener
            prefix: 行前缀
            is_last: 是否是最后一个子节点
            depth: 当前深度
        """
        if depth > self._max_depth:
            return

        # 确定连接符
        if depth == 0:
            connector = "📦 "
        else:
            connector = "└── " if is_last else "├── "

        # 状态图标
        state_icon = self._get_state_icon(listener)
        health_icon = self._get_health_icon(listener)

        # 输出当前节点
        uptime_str = f"[dim]{self.to_duration_string(listener.uptime)}[/dim]" if listener.uptime > 0 else ""
        self._console.print(f"{prefix}{connector}{state_icon} {listener.name} {health_icon} {uptime_str}")

        # 准备子节点前缀
        if depth == 0:
            child_prefix = ""
        else:
            child_prefix = prefix + ("    " if is_last else "│   ")

        # 输出子节点
        children_list = list(listener.children.values())
        for i, child in enumerate(children_list):
            is_last_child = (i == len(children_list) - 1)
            self._print_tree(child, child_prefix, is_last_child, depth + 1)

    async def on_tick(self) -> None:
        """输出状态日志"""
        root = self.root
        start_str = self.to_date_string(root.start_time)
        current_str = self.to_date_string(self.current_time)
        duration_str = self.to_duration_string(self.current_time - root.start_time)

        self._console.print(f"\n[bold cyan]━━━ HFT ━━━[/bold cyan] [yellow]v{__version__}[/yellow]")
        self._console.print(f"[dim]Start:[/dim] {start_str}  [dim]Current:[/dim] {current_str}  [dim]Uptime:[/dim] {duration_str}")
        self._console.print()

        # 打印 Listener 树
        self._print_tree(root)
        self._console.print()

        root.log_state(self._console, recursive=True)
