import math


def sign(x: float) -> int:
    """返回数值的符号"""
    if x > 0:
        return 1
    elif x < 0:
        return -1
    return 0


def round_to_precision(value: float, precision: float) -> float:
    """将数值四舍五入到指定的小数位数，无法对整数以上精度进行处理 precision = 0.01 表示保留两位小数"""
    precision_decimals = round(-math.log10(precision)) if precision > 0 else 0
    aligned_value = round(value, precision_decimals)
    return aligned_value
