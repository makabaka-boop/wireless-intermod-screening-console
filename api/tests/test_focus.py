"""聚焦排查（围绕重点频道优先处理冲突）的单元测试。

覆盖：重点频道名称解析（一至三个、必须已登记、不得重复、聚焦区行号
FOCUS_LINE=-2）、直接影响（目标即重点频道）整体排在来源相关（仅来源
组合含重点频道）之前且两类内部保持原有冲突顺序、同一条目含多组来源
不拆分不重复、目标与来源同时命中时只按直接影响计一次、无相关冲突返回空。
"""

from app.imd import (
    FOCUS_LINE,
    FOCUS_MAX_CHANNELS,
    FOCUS_RELATION_DIRECT,
    FOCUS_RELATION_SOURCE,
    analyze,
    evaluate_focus,
    parse_channels,
    parse_focus_channels,
)

BOUNDARY = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
SAFE = "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000"
# 含多来源组合与多个受影响频道的清单（与去重/排序口径测试一致）
ORDERING = "T 500.000\nA 500.100\nB 500.150\nC 500.200\nD 500.350"


def make(text: str):
    channels, errors = parse_channels(text)
    assert errors == []
    return channels


def focus_ok(channels, value):
    names, errors = parse_focus_channels(value, channels)
    assert errors == [], f"重点频道不应有错误: {errors}"
    return names


def focus_errs(channels, value):
    names, errors = parse_focus_channels(value, channels)
    assert names is None
    return errors


# ---------- 重点频道解析：一至三个已登记频道，不得重复 ----------


def test_focus_names_accepted_one_to_three():
    channels = make(BOUNDARY)
    assert focus_ok(channels, ["A"]) == ["A"]
    assert focus_ok(channels, ["A", "C"]) == ["A", "C"]
    assert focus_ok(channels, ["A", "C", "D"]) == ["A", "C", "D"]


def test_focus_names_keep_request_order():
    channels = make(BOUNDARY)
    assert focus_ok(channels, ["D", "A"]) == ["D", "A"]


def test_focus_value_must_be_a_list():
    for bad in ["A", 123, {"name": "A"}, True]:
        errors = focus_errs(make(BOUNDARY), bad)
        assert len(errors) == 1
        assert errors[0].line == FOCUS_LINE == -2
        assert "列表" in errors[0].message


def test_focus_empty_list_rejected():
    errors = focus_errs(make(BOUNDARY), [])
    assert len(errors) == 1
    assert errors[0].line == FOCUS_LINE
    assert "缺失" in errors[0].message


def test_focus_more_than_max_rejected():
    errors = focus_errs(make(BOUNDARY), ["A", "B", "C", "D"])
    assert len(errors) == 1
    assert errors[0].line == FOCUS_LINE
    assert str(FOCUS_MAX_CHANNELS) in errors[0].message
    assert FOCUS_MAX_CHANNELS == 3


def test_focus_duplicate_names_rejected():
    errors = focus_errs(make(BOUNDARY), ["A", "C", "A"])
    assert len(errors) == 1
    assert errors[0].line == FOCUS_LINE
    assert "重复" in errors[0].message and "A" in errors[0].message
    # 首尾空白归一后仍算重复
    errors = focus_errs(make(BOUNDARY), ["A", " A "])
    assert errors and errors[0].line == FOCUS_LINE


def test_focus_unknown_name_points_to_focus_area():
    errors = focus_errs(make(BOUNDARY), ["ZZ"])
    assert len(errors) == 1
    assert errors[0].line == FOCUS_LINE
    assert "ZZ" in errors[0].message and "不在当前清单" in errors[0].message


def test_focus_non_string_or_blank_name_rejected():
    for bad in [[""], ["  "], [123], [None], [["A"]]]:
        errors = focus_errs(make(BOUNDARY), bad)
        assert errors and all(e.line == FOCUS_LINE for e in errors), bad


def test_focus_collects_multiple_errors():
    errors = focus_errs(make(BOUNDARY), ["ZZ", "A", "ZZ"])
    assert len(errors) >= 2
    assert all(e.line == FOCUS_LINE for e in errors)


# ---------- 聚焦分级与排序：直接影响在前，两类内部保持原有顺序 ----------


def test_focus_direct_entries_come_before_source_entries():
    conflicts = analyze(make(BOUNDARY))
    evaluation = evaluate_focus(conflicts, ["A"])
    got = [(e.relation, e.target_name, e.product_khz) for e in evaluation.entries]
    assert got == [
        (FOCUS_RELATION_DIRECT, "A", 499_950),  # 目标即 A
        (FOCUS_RELATION_SOURCE, "C", 499_900),  # 来源含 A，保持原有顺序
        (FOCUS_RELATION_SOURCE, "B", 500_150),
        (FOCUS_RELATION_SOURCE, "D", 500_200),
    ]
    assert evaluation.direct_count == 1
    assert evaluation.source_count == 3
    assert evaluation.names == ["A"]


def test_focus_multi_source_entry_not_split_or_duplicated():
    conflicts = analyze(make(ORDERING))
    evaluation = evaluate_focus(conflicts, ["T"])
    # C 500200 含两组来源 [A,B] 与 [A,T]：只出现一次且列全来源
    hits = [e for e in evaluation.entries if (e.target_name, e.product_khz) == ("C", 500_200)]
    assert len(hits) == 1
    assert hits[0].relation == FOCUS_RELATION_SOURCE
    assert hits[0].sources == [("A", "B"), ("A", "T")]
    # 直接影响（目标为 T）整体在前，来源相关在后，各自保持原有冲突顺序
    got = [(e.relation, e.target_name, e.product_khz) for e in evaluation.entries]
    assert got == [
        (FOCUS_RELATION_DIRECT, "T", 499_950),
        (FOCUS_RELATION_DIRECT, "T", 500_000),
        (FOCUS_RELATION_DIRECT, "T", 500_050),
        (FOCUS_RELATION_SOURCE, "B", 500_200),
        (FOCUS_RELATION_SOURCE, "C", 500_200),
        (FOCUS_RELATION_SOURCE, "D", 500_300),
        (FOCUS_RELATION_SOURCE, "D", 500_400),
    ]
    assert evaluation.direct_count == 3
    assert evaluation.source_count == 4


def test_focus_target_and_source_hit_counts_once_as_direct():
    # A 500100 的来源含 C：同时勾选 A、C 时该条目只按直接影响出现一次
    conflicts = analyze(make(ORDERING))
    evaluation = evaluate_focus(conflicts, ["A", "C"])
    hits = [e for e in evaluation.entries if (e.target_name, e.product_khz) == ("A", 500_100)]
    assert len(hits) == 1
    assert hits[0].relation == FOCUS_RELATION_DIRECT
    assert hits[0].sources == [("B", "C")]
    # 与重点频道无关的条目不入选（T 499950 的来源为 B、D）
    assert all(e.target_name != "T" or e.product_khz != 499_950 for e in evaluation.entries)


def test_focus_entries_keep_full_target_product_and_sources():
    conflicts = analyze(make(ORDERING))
    evaluation = evaluate_focus(conflicts, ["A"])
    by_key = {(e.target_name, e.product_khz): e for e in evaluation.entries}
    entry = by_key[("T", 500_050)]
    assert entry.relation == FOCUS_RELATION_SOURCE
    assert entry.target_freq_khz == 500_000
    assert entry.diff_khz == 50
    assert entry.sources == [("A", "B"), ("C", "D")]


def test_focus_no_related_conflicts_returns_empty():
    channels = make(BOUNDARY + "\nE 600.000")
    conflicts = analyze(channels)
    assert len(conflicts) == 4  # 完整结果不因聚焦而改写
    evaluation = evaluate_focus(conflicts, ["E"])
    assert evaluation.entries == []
    assert evaluation.direct_count == 0
    assert evaluation.source_count == 0


def test_focus_safe_list_returns_empty():
    conflicts = analyze(make(SAFE))
    evaluation = evaluate_focus(conflicts, ["C1", "C3"])
    assert evaluation.entries == []


def test_focus_does_not_mutate_conflicts():
    channels = make(ORDERING)
    conflicts = analyze(channels)
    snapshot = [
        (c.target_name, c.product_khz, tuple(c.sources)) for c in conflicts
    ]
    evaluate_focus(conflicts, ["T", "A"])
    assert [
        (c.target_name, c.product_khz, tuple(c.sources)) for c in conflicts
    ] == snapshot
