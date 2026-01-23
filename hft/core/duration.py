"""
Duration 字符串解析工具

支持将 duration 字符串（如 60s, 1m, 5m, 1h, 1d）解析为秒数。

Issue 0015: window 支持 duration 字符串
"""
import re
from typing import Union

from humanfriendly import parse_timespan


def parse_duration(value: Union[str, int, float, None]) -> float:
    """
    解析 duration 字符串或数值为秒数。

    Args:
        value: duration 值，可以是：
            - None: 返回 0.0（等价于无窗口）
            - int/float: 直接返回（单位为秒）
            - str: 解析 duration 字符串（如 "60s", "1m", "5m", "1h", "1d"）

    Returns:
        float: 秒数

    Raises:
        ValueError: 当字符串格式非法或单位不支持时
    """
    # None 等价于 0
    if value is None:
        return 0.0

    # 数值直接返回
    if isinstance(value, (int, float)):
        return float(value)

    # 字符串解析
    if isinstance(value, str):
        value = value.strip()
        return parse_timespan(value)
    raise TypeError(
        f"Unsupported duration type: {type(value).__name__}. "
        f"Expected None, int, float, or str."
    )


# 支持的单位与对应的秒数
DURATION_UNITS = {
    'ms': 0.001,      # 毫秒
    's': 1.0,         # 秒
    'm': 60.0,        # 分钟
    'h': 3600.0,      # 小时
    'd': 86400.0,     # 天
}


def _parse_duration(value: Union[str, int, float, None]) -> float:
    """
    解析 duration 字符串或数值为秒数。

    Args:
        value: duration 值，可以是：
            - None: 返回 0.0（等价于无窗口）
            - int/float: 直接返回（单位为秒）
            - str: 解析 duration 字符串（如 "60s", "1m", "5m", "1h", "1d"）

    Returns:
        float: 秒数

    Raises:
        ValueError: 当字符串格式非法或单位不支持时

    Examples:
        >>> parse_duration(None)
        0.0
        >>> parse_duration(60)
        60.0
        >>> parse_duration(60.5)
        60.5
        >>> parse_duration("60s")
        60.0
        >>> parse_duration("1m")
        60.0
        >>> parse_duration("5m")
        300.0
        >>> parse_duration("1h")
        3600.0
        >>> parse_duration("1d")
        86400.0
        >>> parse_duration("500ms")
        0.5
    """
    # None 等价于 0
    if value is None:
        return 0.0

    # 数值直接返回
    if isinstance(value, (int, float)):
        return float(value)

    # 字符串解析
    if isinstance(value, str):
        value = value.strip()

        # 匹配 duration 格式：数字 + 单位
        match = re.match(r'^(\d+(?:\.\d+)?)(ms|s|m|h|d)$', value)
        if not match:
            units_list = list(DURATION_UNITS.keys())
            raise ValueError(
                f"Invalid duration format: '{value}'. "
                f"Expected format: <number><unit>, where unit is one of {units_list}. "
                f"Examples: 60s, 1m, 5m, 1h, 1d, 500ms"
            )

        number_str, unit = match.groups()
        number = float(number_str)

        if unit not in DURATION_UNITS:
            raise ValueError(
                f"Unsupported duration unit: '{unit}'. "
                f"Supported units: {list(DURATION_UNITS.keys())}"
            )

        return number * DURATION_UNITS[unit]

    # 其他类型不支持
    raise TypeError(
        f"Unsupported duration type: {type(value).__name__}. "
        f"Expected None, int, float, or str."
    )
