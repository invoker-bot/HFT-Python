"""
IndicatorGroup - 指标管理器

Feature 0006: Indicator 与 DataSource 统一架构
Feature 0008: Strategy 数据驱动增强 - Indicator 层级体系

"""
from ..core.listener import Listener


class IndicatorGroup(Listener):
    """
    指标管理器 - 顶层
    """
    lazy_start = True
    disable_tick = True  # 没有on tick 方法

    async def on_tick(self):
        return False
