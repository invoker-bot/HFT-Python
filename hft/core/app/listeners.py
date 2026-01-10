"""
缓存监听器模块

提供应用状态的持久化功能：
- 定期将应用状态序列化到磁盘
- 支持从缓存文件恢复应用状态
"""
import asyncio
import pickle
from os import makedirs, path, replace
from collections import Counter
from functools import cached_property
from typing import TYPE_CHECKING, Optional
from rich.console import Console
from ..._version import __version__
from ..listener import Listener, ListenerState

if TYPE_CHECKING:
    from .base import AppCore


class CacheListener(Listener):
    """
    缓存监听器

    定期将整个应用状态（包括所有子监听器）保存到磁盘，
    支持在应用重启后从缓存恢复状态。

    缓存文件路径由 AppConfig.data_path 指定。
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "cache_file")

    def __init__(self, interval: float = 300.0):
        """
        初始化缓存监听器

        Args:
            interval: 保存间隔（秒），默认 5 分钟
        """
        super().__init__(interval=interval)

    @cached_property
    def cache_file(self) -> str:
        """获取缓存文件路径"""
        root: 'AppCore' = self.root
        return root.config.data_path

    async def on_start(self):
        """启动时调整间隔为配置值"""
        await super().on_start()
        root: 'AppCore' = self.root
        self.interval = root.config.cache_interval

    async def on_tick(self):
        """定时回调：保存缓存"""
        await self.save_cache_async()

    async def on_stop(self):
        await self.save_cache_async()
        await super().on_stop()

    async def save_cache_async(self):
        """
        异步保存当前状态到缓存文件

        优化策略：
        1. 在主线程中序列化为 bytes（CPU 密集但通常较快）
        2. 使用 asyncio.to_thread() 将文件 I/O 放到线程池
        3. 使用临时文件 + 原子重命名，防止写入中断导致文件损坏
        """
        try:
            # 序列化（CPU 密集型，但通常比 I/O 快）
            data = pickle.dumps(self.root, protocol=pickle.HIGHEST_PROTOCOL)

            # 异步写入文件
            await asyncio.to_thread(self._write_cache_file, data)
            self.logger.info("Cache saved to %s (%d bytes)", self.cache_file, len(data))
        except Exception as e:
            self.logger.error("Failed to save cache to %s: %s", self.cache_file, e, exc_info=True)

    def _write_cache_file(self, data: bytes):
        """
        写入缓存文件（在线程池中执行）

        使用临时文件 + 原子重命名，确保文件完整性。
        """
        cache_file = self.cache_file
        temp_file = cache_file + '.tmp'
        makedirs(path.dirname(cache_file), exist_ok=True)

        with open(temp_file, 'wb') as f:
            f.write(data)
            f.flush()

        # 原子重命名（Windows 上 replace 是原子的）
        replace(temp_file, cache_file)

    @classmethod
    def load_cache(cls, cache_file: str) -> "AppCore":
        """
        从缓存文件加载状态

        Args:
            cache_file: 缓存文件路径

        Returns:
            恢复的 AppCore 实例

        Raises:
            RuntimeError: 如果加载失败
        """
        try:
            with open(cache_file, 'rb') as f:
                obj = pickle.load(f)
            obj.logger.info("Cache loaded from %s", cache_file)
            return obj
        except Exception as e:
            raise RuntimeError(f"Failed to load cache from {cache_file}: {e}") from e


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

    def __init__(self, interval: float = 300, max_depth: int = 6, console: Optional[Console] = None):
        """
        Args:
            interval: 日志输出间隔（秒）
            max_depth: 最大显示深度
            console: Rich Console 实例
        """
        super().__init__(interval=interval)
        self._start = self.current_time
        self._console = console or Console(width=300)
        self._max_depth = max_depth

    def __setstate__(self, state):
        super().__setstate__(state)
        self._console = Console(width=300)

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
            connector = "[*] "
        else:
            connector = "`-- " if is_last else "|-- "

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
            child_prefix = prefix + ("    " if is_last else "|   ")

        # 输出子节点
        children_list = list(listener.children.values())
        for i, child in enumerate(children_list):
            is_last_child = (i == len(children_list) - 1)
            self._print_tree(child, child_prefix, is_last_child, depth + 1)

    async def on_start(self):
        await super().on_start()
        self._start = self.current_time
        app: 'AppCore' = self.root
        self.interval = app.config.log_interval

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

    def __init__(self, interval: float = 120.0, reconfirm=3):
        """
        初始化不健康重启监听器

        Args:
            interval: 健康检查间隔（秒），默认 2 分钟
        """
        super().__init__(interval=interval)
        self.reconfirm_cache = Counter()
        self.reconfirm = reconfirm

    async def on_start(self):
        await super().on_start()
        root: 'AppCore' = self.root
        self.interval = root.config.health_check_interval

    async def on_tick(self):
        """
        定时回调：执行健康检查并重启不健康的监听器

        遍历所有监听器，对已启用但不健康的监听器执行重启操作。
        """
        root = self.root
        assert root is not None, "UnhealthyRestartListener must be attached to a root Listener"
        await root.health_check(True)
        for listener in list(root):
            if listener.enabled and not listener.healthy:
                self.reconfirm_cache[listener.id] += 1
                if self.reconfirm_cache[listener.id] >= self.reconfirm:
                    self.logger.warning("Listener %s is unhealthy, restarting...", listener.name)
                    await listener.restart(False)
                    self.reconfirm_cache[listener.id] = 0
            else:
                self.reconfirm_cache[listener.id] = 0
