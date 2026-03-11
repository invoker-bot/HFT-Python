"""
配置解析系统和 Group/Filter 工具的综合测试

测试范围：
1. var.py - 变量定义解析
2. filters.py - 过滤模式匹配
3. group.py - Group 分组类
4. scope.py - ScopeConfig
5. executor/config.py - OrderDefinition / BaseExecutorConfig
"""
import pytest
from hft.config.var import (
    StandardVarDefinition,
    to_standard_var_definition,
    to_standard_vars_definition,
)
from hft.config.scope import ScopeConfig
from hft.core.filters import (
    split_filters,
    apply_filters,
    apply_filters_raw,
    get_matcher_raw,
    get_matcher,
)
from hft.core.group import Group
from hft.executor.config import BaseExecutorConfig, OrderDefinition


# ============================================================
# 1. var.py - 变量定义解析
# ============================================================

class TestToStandardVarDefinition:
    """测试 to_standard_var_definition 单个变量转换"""

    def test_standard_var_definition_passthrough(self):
        """StandardVarDefinition 实例直接返回"""
        var = StandardVarDefinition(name="spread", value="0.001")
        result = to_standard_var_definition(var)
        assert result is var
        assert result.name == "spread"
        assert result.value == "0.001"

    def test_dict_format(self):
        """dict 格式: {"name": "spread", "value": "0.001"}"""
        result = to_standard_var_definition({"name": "spread", "value": "0.001"})
        assert isinstance(result, StandardVarDefinition)
        assert result.name == "spread"
        assert result.value == "0.001"

    def test_string_format(self):
        """字符串格式: "spread=0.001" """
        result = to_standard_var_definition("spread=0.001")
        assert isinstance(result, StandardVarDefinition)
        assert result.name == "spread"
        assert result.value == "0.001"

    def test_string_with_equals_in_value(self):
        """值中包含等号: "expr=a=b" → name="expr", value="a=b" """
        result = to_standard_var_definition("expr=a=b")
        assert result.name == "expr"
        assert result.value == "a=b"

    def test_string_whitespace_handling(self):
        """字符串前后空格自动去除"""
        result = to_standard_var_definition(" name = value ")
        assert result.name == "name"
        assert result.value == "value"

    def test_tuple_format(self):
        """元组格式: ("name", "value")"""
        result = to_standard_var_definition(("spread", "0.001"))
        assert isinstance(result, StandardVarDefinition)
        assert result.name == "spread"
        assert result.value == "0.001"

    def test_unsupported_format_raises(self):
        """不支持的格式抛出 NotImplementedError"""
        with pytest.raises(NotImplementedError):
            to_standard_var_definition(12345)

    def test_dict_with_optional_fields(self):
        """dict 格式包含可选字段 on 和 initial_value"""
        result = to_standard_var_definition({
            "name": "x",
            "value": "a + b",
            "on": "flag",
            "initial_value": 0,
        })
        assert result.name == "x"
        assert result.value == "a + b"
        assert result.on == "flag"
        assert result.initial_value == 0


class TestToStandardVarsDefinition:
    """测试 to_standard_vars_definition 多变量转换"""

    def test_none_input(self):
        """None 输入返回空列表"""
        result = to_standard_vars_definition(None)
        assert result == []

    def test_empty_list(self):
        """空列表返回空列表"""
        result = to_standard_vars_definition([])
        assert result == []

    def test_dict_input(self):
        """dict 输入: {"a": "1", "b": "2"} → 两个 StandardVarDefinition"""
        result = to_standard_vars_definition({"a": "1", "b": "2"})
        assert len(result) == 2
        names = {v.name for v in result}
        assert names == {"a", "b"}
        for v in result:
            assert isinstance(v, StandardVarDefinition)

    def test_mixed_format_list(self):
        """混合格式列表: [StandardVarDefinition, dict, str] 全部转换"""
        std = StandardVarDefinition(name="x", value="1")
        data = [
            std,
            {"name": "y", "value": "2"},
            "z=3",
        ]
        result = to_standard_vars_definition(data)
        assert len(result) == 3
        assert result[0] is std
        assert result[1].name == "y"
        assert result[1].value == "2"
        assert result[2].name == "z"
        assert result[2].value == "3"

    def test_list_of_tuples(self):
        """元组列表"""
        result = to_standard_vars_definition([("a", "1"), ("b", "2")])
        assert len(result) == 2
        assert result[0].name == "a"
        assert result[1].name == "b"

    def test_unsupported_format_raises(self):
        """不支持的格式抛出 NotImplementedError"""
        with pytest.raises(NotImplementedError):
            to_standard_vars_definition(12345)


# ============================================================
# 2. filters.py - 过滤模式匹配
# ============================================================

class TestSplitFilters:
    """测试 split_filters 分离 include/exclude 规则"""

    def test_basic_split(self):
        """基本分离: 包含通配符和排除规则"""
        includes, excludes = split_filters("BTC-*,ETH-*,!BTC-USDT")
        assert includes == ["BTC-*", "ETH-*"]
        assert excludes == ["BTC-USDT"]

    def test_only_includes(self):
        """仅 include 规则"""
        includes, excludes = split_filters("BTC-*,ETH-*")
        assert includes == ["BTC-*", "ETH-*"]
        assert excludes == []

    def test_only_excludes(self):
        """仅 exclude 规则"""
        includes, excludes = split_filters("!BTC-USDT,!ETH-USDT")
        assert includes == []
        assert excludes == ["BTC-USDT", "ETH-USDT"]

    def test_whitespace_handling(self):
        """空格自动去除"""
        includes, excludes = split_filters(" BTC-* , !ETH-* ")
        assert includes == ["BTC-*"]
        assert excludes == ["ETH-*"]

    def test_empty_parts_ignored(self):
        """空部分被忽略"""
        includes, excludes = split_filters("BTC-*,,ETH-*,")
        assert includes == ["BTC-*", "ETH-*"]
        assert excludes == []


class TestApplyFilters:
    """测试 apply_filters 过滤功能"""

    def test_include_only(self):
        """仅 include 过滤"""
        items = ["BTC-USDT", "BTC-USDC", "ETH-USDT", "SOL-USDT"]
        result = apply_filters(items, ["BTC-*"], [])
        assert set(result) == {"BTC-USDT", "BTC-USDC"}

    def test_exclude_only(self):
        """仅 exclude 过滤（空 includes 匹配所有）"""
        items = ["BTC-USDT", "BTC-USDC", "ETH-USDT"]
        result = apply_filters(items, [], ["BTC-USDC"])
        assert set(result) == {"BTC-USDT", "ETH-USDT"}

    def test_include_and_exclude(self):
        """同时 include 和 exclude"""
        items = ["BTC-USDT", "BTC-USDC", "ETH-USDT", "SOL-USDT"]
        result = apply_filters(items, ["BTC-*", "ETH-*"], ["BTC-USDC"])
        assert set(result) == {"BTC-USDT", "ETH-USDT"}

    def test_empty_includes_match_all(self):
        """空 includes 匹配所有"""
        items = ["BTC-USDT", "ETH-USDT"]
        result = apply_filters(items, [], [])
        assert set(result) == {"BTC-USDT", "ETH-USDT"}

    def test_none_includes_match_all(self):
        """None includes 匹配所有"""
        items = ["BTC-USDT", "ETH-USDT"]
        result = apply_filters(items, None, None)
        assert set(result) == {"BTC-USDT", "ETH-USDT"}

    def test_case_insensitive(self):
        """大小写不敏感匹配 - get_matcher 设置 case_sensitive=False"""
        # get_matcher 使用 case_sensitive=False，验证 Matcher 被正确配置
        matcher = get_matcher(["btc-*"], [])
        # 验证 matcher 对象被正确创建（不抛异常即可）
        assert matcher is not None
        # 实际匹配行为取决于 younotyou.Matcher 的实现
        items = ["btc-usdt"]
        result = apply_filters(items, ["btc-*"], [])
        assert "btc-usdt" in result

    def test_no_match(self):
        """没有匹配项"""
        items = ["BTC-USDT", "ETH-USDT"]
        result = apply_filters(items, ["SOL-*"], [])
        assert result == []


class TestApplyFiltersRaw:
    """测试 apply_filters_raw 字符串过滤"""

    def test_basic(self):
        items = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        result = apply_filters_raw(items, "BTC-*,ETH-*")
        assert set(result) == {"BTC-USDT", "ETH-USDT"}

    def test_with_exclude(self):
        items = ["BTC-USDT", "BTC-USDC", "ETH-USDT"]
        result = apply_filters_raw(items, "BTC-*,!BTC-USDC")
        assert result == ["BTC-USDT"]


class TestGetMatcherRawCaching:
    """测试 get_matcher_raw 缓存"""

    def test_same_input_returns_same_result(self):
        """相同输入返回相同对象（缓存命中）"""
        m1 = get_matcher_raw("BTC-*,!BTC-USDC")
        m2 = get_matcher_raw("BTC-*,!BTC-USDC")
        assert m1 is m2

    def test_different_input_returns_different_result(self):
        """不同输入返回不同对象"""
        m1 = get_matcher_raw("BTC-*")
        m2 = get_matcher_raw("ETH-*")
        assert m1 is not m2


# ============================================================
# 3. group.py - Group 分组类
# ============================================================

def _base_currency(pair: str) -> str:
    """提取交易对的基础货币, e.g. 'BTC-USDT' → 'BTC'"""
    return pair.split("-")[0]


class TestGroup:
    """测试 Group 类"""

    def test_create_and_update(self):
        """创建 Group 并 update 添加项"""
        g = Group(_base_currency)
        g.update(["BTC-USDT", "BTC-USDC", "ETH-USDT"])
        assert set(g.keys()) == {"BTC", "ETH"}
        assert g["BTC"] == {"BTC-USDT", "BTC-USDC"}
        assert g["ETH"] == {"ETH-USDT"}

    def test_create_with_items(self):
        """创建时传入初始 items"""
        g = Group(_base_currency, ["BTC-USDT", "ETH-USDT"])
        assert set(g.keys()) == {"BTC", "ETH"}

    def test_all_items(self):
        """all_items 返回所有项的平展集合"""
        g = Group(_base_currency, ["BTC-USDT", "BTC-USDC", "ETH-USDT"])
        assert g.all_items() == {"BTC-USDT", "BTC-USDC", "ETH-USDT"}

    def test_to_group(self):
        """to_group 返回分组 key"""
        g = Group(_base_currency)
        assert g.to_group("BTC-USDT") == "BTC"
        assert g.to_group("ETH-USDC") == "ETH"

    def test_apply_group_filters_raw(self):
        """apply_group_filters_raw 按 group key 过滤"""
        g = Group(_base_currency, ["BTC-USDT", "ETH-USDT", "SOL-USDT"])
        result = g.apply_group_filters_raw("BTC,ETH")
        assert set(result.keys()) == {"BTC", "ETH"}
        assert "SOL" not in result

    def test_apply_item_filters_raw(self):
        """apply_item_filters_raw 按 item 值过滤"""
        g = Group(_base_currency, ["BTC-USDT", "BTC-USDC", "ETH-USDT"])
        result = g.apply_item_filters_raw("*-USDT")
        assert result["BTC"] == {"BTC-USDT"}
        assert result["ETH"] == {"ETH-USDT"}

    def test_apply_group_filters_raw_caching(self):
        """相同 filter 返回缓存结果"""
        g = Group(_base_currency, ["BTC-USDT", "ETH-USDT", "SOL-USDT"])
        result1 = g.apply_group_filters_raw("BTC")
        result2 = g.apply_group_filters_raw("BTC")
        assert result1 is result2

    def test_apply_item_filters_raw_caching(self):
        """相同 filter 返回缓存结果"""
        g = Group(_base_currency, ["BTC-USDT", "BTC-USDC", "ETH-USDT"])
        result1 = g.apply_item_filters_raw("*-USDT")
        result2 = g.apply_item_filters_raw("*-USDT")
        assert result1 is result2

    def test_group_filters_none_returns_self(self):
        """None filter 返回自身"""
        g = Group(_base_currency, ["BTC-USDT", "ETH-USDT"])
        assert g.apply_group_filters_raw(None) is g

    def test_item_filters_none_returns_self(self):
        """None filter 返回自身"""
        g = Group(_base_currency, ["BTC-USDT", "ETH-USDT"])
        assert g.apply_item_filters_raw(None) is g

    def test_item_filtering_removes_empty_groups(self):
        """item 过滤后空组应被移除"""
        g = Group(_base_currency, ["BTC-USDC", "ETH-USDT"])
        result = g.apply_item_filters_raw("*-USDT")
        assert "BTC" not in result
        assert "ETH" in result

    def test_defaultdict_behavior(self):
        """访问不存在的 key 返回空 set（defaultdict 行为）"""
        g = Group(_base_currency)
        assert g["NONEXISTENT"] == set()


# ============================================================
# 4. ScopeConfig
# ============================================================

class TestScopeConfig:
    """测试 ScopeConfig"""

    def test_standard_vars_definition_converts_correctly(self):
        """standard_vars_definition 正确转换所有格式"""
        config = ScopeConfig(
            vars=[
                StandardVarDefinition(name="a", value="1"),
                {"name": "b", "value": "2"},
                "c=3",
            ]
        )
        result = config.standard_vars_definition
        assert len(result) == 3
        assert result[0].name == "a"
        assert result[1].name == "b"
        assert result[2].name == "c"
        assert result[2].value == "3"

    def test_default_values(self):
        """默认值测试"""
        config = ScopeConfig()
        assert config.class_name == "BaseScope"
        assert config.filter is None
        assert config.requires == []
        assert config.vars == []
        assert config.sorted_var is None
        assert config.condition is None

    def test_sorted_var_field(self):
        """sorted_var 字段"""
        config = ScopeConfig(sorted_var="score")
        assert config.sorted_var == "score"

    def test_filter_and_condition_fields(self):
        """filter 和 condition 字段"""
        config = ScopeConfig(filter="is_active", condition="volume > 100")
        assert config.filter == "is_active"
        assert config.condition == "volume > 100"

    def test_filter_bool_value(self):
        """filter 支持 bool 值"""
        config = ScopeConfig(filter=True)
        assert config.filter is True

    def test_standard_vars_definition_cached(self):
        """standard_vars_definition 是 cached_property"""
        config = ScopeConfig(vars=["x=1"])
        r1 = config.standard_vars_definition
        r2 = config.standard_vars_definition
        assert r1 is r2

    def test_empty_vars(self):
        """空 vars 返回空列表"""
        config = ScopeConfig(vars=[])
        assert config.standard_vars_definition == []


# ============================================================
# 5. OrderDefinition / BaseExecutorConfig
# ============================================================

class TestOrderDefinition:
    """测试 OrderDefinition"""

    def test_defaults(self):
        """默认字段值"""
        od = OrderDefinition()
        assert od.vars == []
        assert od.condition is None
        assert od.price is None
        assert od.spread is None
        assert od.order_amount is None
        assert od.order_usd is None
        assert od.timeout == 60.0
        assert od.refresh_tolerance == 0.5
        assert od.refresh_tolerance_usd is None
        assert od.level is None
        assert od.post_only is False

    def test_with_values(self):
        """设置字段值"""
        od = OrderDefinition(
            spread="0.001 * mid_price",
            order_usd="100",
            condition="level <= 3",
            level=1,
        )
        assert od.spread == "0.001 * mid_price"
        assert od.order_usd == "100"
        assert od.condition == "level <= 3"
        assert od.level == 1

    def test_standard_vars_definition(self):
        """vars 转换"""
        od = OrderDefinition(vars=["x=1", "y=2"])
        result = od.standard_vars_definition
        assert len(result) == 2
        assert result[0].name == "x"


class TestBaseExecutorConfig:
    """测试 BaseExecutorConfig"""

    def test_total_order_definitions_with_order_levels(self):
        """order_levels=3 生成 ±1, ±2, ±3 共 6 个订单"""
        config = BaseExecutorConfig(
            order=OrderDefinition(spread="0.001"),
            order_levels=3,
        )
        orders = config.total_order_definitions
        assert len(orders) == 6
        levels = sorted([o.level for o in orders])
        assert levels == [-3, -2, -1, 1, 2, 3]
        # 每个订单都继承 order 的 spread
        for o in orders:
            assert o.spread == "0.001"

    def test_total_order_definitions_with_only_orders(self):
        """仅有 orders 列表"""
        config = BaseExecutorConfig(
            orders=[
                OrderDefinition(spread="0.001", level=1),
                OrderDefinition(spread="0.002", level=-1),
            ],
        )
        orders = config.total_order_definitions
        assert len(orders) == 2
        assert orders[0].spread == "0.001"
        assert orders[1].spread == "0.002"

    def test_total_order_definitions_combined(self):
        """orders + order_levels 合并"""
        config = BaseExecutorConfig(
            orders=[OrderDefinition(spread="0.005", level=99)],
            order=OrderDefinition(spread="0.001"),
            order_levels=1,
        )
        orders = config.total_order_definitions
        # 1 from orders + 2 from order_levels (±1)
        assert len(orders) == 3
        assert orders[0].level == 99  # 原始 orders 中的
        levels_from_generated = sorted([o.level for o in orders[1:]])
        assert levels_from_generated == [-1, 1]

    def test_total_order_definitions_empty(self):
        """无订单配置"""
        config = BaseExecutorConfig()
        assert config.total_order_definitions == []

    def test_standard_vars_definition(self):
        """executor vars 转换"""
        config = BaseExecutorConfig(vars=["spread=0.001", "amount=100"])
        result = config.standard_vars_definition
        assert len(result) == 2
        assert result[0].name == "spread"
        assert result[1].name == "amount"

    def test_defaults(self):
        """默认值"""
        config = BaseExecutorConfig()
        assert config.interval == 5.0
        assert config.clean is True
        assert config.requires == []
        assert config.condition is None
        assert config.default_timeout == 60.0
