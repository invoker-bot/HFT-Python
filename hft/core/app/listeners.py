"""
辅助监听器模块

提供应用状态监控和健康检查功能：
- StateLogListener: 定期打印 Listener 树的状态
- UnhealthyRestartListener: 自动重启不健康的监听器
"""
from functools import cached_property
from collections import Counter
from typing import TYPE_CHECKING

from rich.console import Console

from ..._version import __version__
from ..listener import Listener, ListenerState

if TYPE_CHECKING:
    from .base import AppCore


class StateLogListener(Listener):
    """
    状态日志监听器

    定期打印 Listener 树的状态，使用树形目录格式显示：
    📦 AppCore [running] ♥ ✓
    ├── StateLogListener [running] ♥ ✓
    ├── UnhealthyRestartListener [running] ♥ ✓
    └── Strategy [running] ♥ ✓
        ├── Controller [running] ♥ ✓
        └── Executor [running] ♥ ✓

    图标说明：
    - 状态: R(运行) S(停止) E(错误) >(启动中) <(停止中)
    - 健康: OK(健康) !!!(不健康)
    - 就绪: ✓(就绪) ✗(未就绪)
    """
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "_console"}

    @property
    def max_depth(self) -> int:
        """获取最大显示深度"""
        app_core: 'AppCore' = self.root
        return app_core.config.log_max_depth

    @property
    def interval(self) -> float:
        """获取日志输出间隔"""
        app_core: 'AppCore' = self.root
        return app_core.config.log_interval

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._console = kwargs.get("console", None) or Console(width=300)
        self._start = self.current_time

    def _get_state_icon(self, listener: Listener) -> str:
        """获取状态图标"""
        state = listener._state
        match state:
            case ListenerState.RUNNING:
                return "[green]R[/green]"
            case ListenerState.STOPPED:
                return "[dim]S[/dim]"
            case ListenerState.ERROR:
                return "[red]E[/red]"
            case ListenerState.STARTING:
                return "[yellow]>[/yellow]"
            case ListenerState.STOPPING:
                return "[yellow]<[/yellow]"
        return "[dim]?[/dim]"

    def _get_health_icon(self, listener: Listener) -> str:
        """获取健康状态图标"""
        return "[green]OK[/green]" if listener.healthy else "[red]!!![/red]"

    def _get_ready_icon(self, listener: Listener) -> str:
        """获取就绪状态图标"""
        return "[green]✓[/green]" if listener.ready else "[dim]✗[/dim]"

    def _print_tree(self, listener: Listener, prefix: str = "", is_last: bool = True, depth: int = 0) -> None:
        """
        递归打印 Listener 树

        Args:
            listener: 当前 Listener
            prefix: 行前缀
            is_last: 是否是最后一个子节点
            depth: 当前深度
        """
        if depth > self.max_depth:
            return

        # 确定连接符
        if depth == 0:
            connector = "[*] "
        else:
            connector = "`-- " if is_last else "|-- "

        # 状态图标
        state_icon = self._get_state_icon(listener)
        health_icon = self._get_health_icon(listener)
        ready_icon = self._get_ready_icon(listener)

        # 输出当前节点
        uptime_str = f"[dim]{self.to_duration_string(listener.uptime)}[/dim]" if listener.uptime > 0 else ""
        self._console.print(f"{prefix}{connector}{state_icon} {listener.name} {health_icon} {ready_icon} {uptime_str}")

        # 准备子节点前缀
        if depth == 0:
            child_prefix = ""
        else:
            child_prefix = prefix + ("    " if is_last else "|   ")

        # 输出子节点
        children_list = list(listener.children.values())
        for i, child in enumerate(children_list):
            is_last_child = (i == len(children_list) - 1)
            self._print_tree(child, child_prefix, is_last_child, depth + 1)

    async def on_tick(self) -> None:
        """输出状态日志"""
        if self.current_time - self._start < self.interval:  # initial delay
            return
        root = self.root
        start_str = self.to_date_string(root.start_time)
        current_str = self.to_date_string(self.current_time)
        duration_str = self.to_duration_string(self.current_time - root.start_time)

        self._console.print(f"\n[bold cyan]=== HFT ===[/bold cyan] [yellow]v{__version__}[/yellow]")
        self._console.print(f"[dim]Start:[/dim] {start_str}  [dim]Current:[/dim] {current_str}  [dim]Uptime:[/dim] {duration_str}")
        self._console.print()

        # 打印 Listener 树
        self._print_tree(root)
        self._console.print()

        root.log_state(self._console, recursive=True)


class UnhealthyRestartListener(Listener):
    """
    不健康重启监听器

    定期检查所有监听器的健康状态，自动重启不健康的监听器。
    健康检查会触发每个监听器的 on_health_check() 回调。
    """
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "reconfirm_cache"}

    @cached_property
    def reconfirm_cache(self) -> Counter:
        return Counter()

    @property
    def reconfirm(self) -> int:
        """获取重启确认次数"""
        app_core: 'AppCore' = self.root
        return app_core.config.health_check_restart_reconfirm

    @property
    def interval(self) -> float:
        """获取日志输出间隔"""
        app_core: 'AppCore' = self.root
        return app_core.config.log_interval

    async def on_start(self):
        await super().on_start()
        self.start_time = self.current_time

    async def on_tick(self):
        """
        定时回调：执行健康检查并重启不健康的监听器

        遍历所有监听器，对已启用但不健康的监听器执行重启操作。
        """
        root = self.root
        assert root is not None, "UnhealthyRestartListener must be attached to a root Listener"
        await root.health_check(True)
        if self.reconfirm <= 0:  # 不重启
            return
        for listener in list(root):
            if listener.enabled and not listener.healthy:
                # TODO: 这里需要移除日志
                self.logger.info("Listener %s is unhealthy: %d", listener.name, self.reconfirm_cache[listener.id])
                self.reconfirm_cache[listener.id] += 1
                if self.reconfirm_cache[listener.id] >= self.reconfirm:
                    self.logger.warning("Listener %s is unhealthy, restarting...", listener.name)
                    await listener.restart(False)
                    self.reconfirm_cache[listener.id] = 0
            else:
                # self.logger.info("Listener %s is healthy", listener.name)
                self.reconfirm_cache[listener.id] = 0
