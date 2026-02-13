from typing import Optional, Iterable
from functools import lru_cache
from younotyou import Matcher

def split_filters(filters: str) -> tuple[list[str], list[str]]:
    """
    分离 include 和 exclude 规则
    Args:
        filters: 逗号分隔的 filter 字符串
    Returns:
        (includes, excludes)
    """
    includes = []
    excludes = []
    for filter_ in filters.split(","):
        filter_ = filter_.strip()
        if not filter_:
            continue
        if filter_.startswith("!"):
            excludes.append(filter_[1:])
        else:
            includes.append(filter_)
    return includes, excludes


def join_filters(includes: Optional[list[str]] = None, excludes: Optional[list[str]] = None) -> str:
    if includes is None or len(includes) == 0:
        includes = ["*"]
    if excludes is None:
        excludes = []
    parts = []
    for filter_ in includes:
        parts.append(filter_.strip())
    for filter_ in excludes:
        parts.append(f"!{filter_.strip()}")
    return ",".join(parts)


def get_matcher_quick(filters) -> Matcher:
    """支持 str（逗号分隔）或 list 输入"""
    if isinstance(filters, (list, tuple)):
        return get_matcher_raw(",".join(filters))
    return get_matcher_raw(filters)


@lru_cache(maxsize=1024)
def get_matcher_raw(filters: str) -> Matcher:
    includes, excludes = split_filters(filters)
    return get_matcher(includes, excludes)


def get_matcher(includes: Optional[list[str]] = None, excludes: Optional[list[str]] = None) -> Matcher:
    if includes is None or len(includes) == 0:
        includes = ["*"]
    if excludes is None:
        excludes = []
    return Matcher(include_patterns=includes, exclude_patterns=excludes, case_sensitive=False)


def apply_filters_raw(items: Iterable[str], filters: str) -> list[str]:
    matcher = get_matcher_raw(filters)
    return [item for item in items if matcher.matches(item)]


def apply_filters(items: Iterable[str], includes: list[str], excludes: list[str]) -> list[str]:
    """
    根据 include 和 exclude 规则过滤 items 列表
    Args:
        items: 待过滤的字符串列表
        includes: include 规则列表
        excludes: exclude 规则列表
    Returns:
        过滤后的字符串列表
    """
    matcher = get_matcher(includes, excludes)
    return [item for item in items if matcher.matches(item)]
