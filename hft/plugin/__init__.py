"""
HFT 插件系统

Usage:
    from hft.plugin import pm, hookimpl, PluginBase

    class MyPlugin(PluginBase):
        name = "my_plugin"

        @hookimpl
        def on_order_created(self, exchange, order):
            print(f"Order: {order}")

    # 注册插件
    pm.register(MyPlugin())

详见 docs/plugin.md
"""
from .base import pm, hookspec, hookimpl, PluginBase, HookSpec

__all__ = ["pm", "hookspec", "hookimpl", "PluginBase", "HookSpec"]
