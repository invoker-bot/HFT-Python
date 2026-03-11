"""
分组，是一个dict[str, list[str]] 的映射

"""
from typing import Callable, Iterable, Optional
from collections import defaultdict
from .filters import apply_filters_raw, join_filters


class Group(defaultdict[str, set[str]]):

    def __init__(self, group_func: Callable[[str], str], items: Optional[Iterable[str]] = None):
        super().__init__(set)
        self.group_func = group_func
        self._group_filter_cache: dict[Optional[str], "Group"] = {}
        self._item_filter_cache: dict[Optional[str], "Group"] = {}
        if items is not None:
            self.update(items)

    def update(self, items: Iterable[str]):
        for item in items:
            key = self.group_func(item)
            self[key].add(item)

    def all_items(self) -> set[str]:
        """获取所有分组中的所有项"""
        result = set()
        for item_set in self.values():
            result.update(item_set)
        return result

    def to_group(self, item: str) -> str:
        return self.group_func(item)

    def apply_group_filters_raw(self, filters: Optional[str] = None):  # 对key进行filter
        if filters is None:
            return self
        cached = self._group_filter_cache.get(filters)
        if cached is not None:
            return cached
        new_group = self.__class__(self.group_func)
        filtered_keys = apply_filters_raw(self.keys(), filters)
        for key in filtered_keys:
            new_group[key] = self[key]
        self._group_filter_cache[filters] = new_group
        return new_group

    def apply_group_filters(self, includes: Optional[list[str]] = None, excludes: Optional[list[str]] = None):
        if includes is None and excludes is None:
            return self
        filters = join_filters(includes, excludes)
        return self.apply_group_filters_raw(filters)

    def apply_item_filters_raw(self, filters: Optional[str] = None):  # 对value进行filter
        if filters is None:
            return self
        cached = self._item_filter_cache.get(filters)
        if cached is not None:
            return cached
        new_group = self.__class__(self.group_func)
        for key, item_set in self.items():
            filtered_items = apply_filters_raw(item_set, filters)
            if len(filtered_items) > 0:
                new_group[key] = set(filtered_items)
        self._item_filter_cache[filters] = new_group
        return new_group

    def apply_item_filters(self, includes: Optional[list[str]] = None, excludes: Optional[list[str]] = None):
        if includes is None and excludes is None:
            return self
        filters = join_filters(includes, excludes)
        return self.apply_item_filters_raw(filters)
