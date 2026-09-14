"""微调频点（演出前微调已登记话筒）的单元测试。

覆盖：±500 kHz / 25 kHz 步长枚举（含边界候选）、合法且未占用过滤、
复用整数 kHz 冲突分析、按（冲突总数，移动距离，频率升序）稳定排名、
最多 5 条建议、无改善返回空、未知频道指向微调区（RETUNE_LINE=-1）。
"""

from app.imd import (
    MAX_KHZ,
    MIN_KHZ,
    RETUNE_LINE,
    RETUNE_MAX_SUGGESTIONS,
    RETUNE_RANGE_KHZ,
    RETUNE_STEP_KHZ,
    analyze,
    evaluate_retune,
    parse_channels,
    parse_retune_target,
)

BOUNDARY = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
SAFE = "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000"
# CH3 的 +500 kHz 边界候选（595.275）恰好是改善方案
EDGE = "CH0 593.975\nCH1 594.550\nCH2 594.675\nCH3 594.775\nCH4 594.800\nCH5 595.450"
# CH0 微调至带缘 470.000 是最佳方案
BAND_EDGE = "CH0 470.100\nCH1 470.400\nCH2 470.725\nCH3 471.300"


def make(text: str):
    channels, errors = parse_channels(text)
    assert errors == []
    return channels


def retune(channels, name):
    target, errors = parse_retune_target(name, channels)
    assert errors == [], f"微调目标不应有错误: {errors}"
    return evaluate_retune(channels, target)


# ---------- 微调目标解析：名称必须已在清单中登记 ----------


def test_retune_target_found_by_name():
    channels = make(BOUNDARY)
    target, errors = parse_retune_target("A", channels)
    assert errors == []
    assert target.name == "A" and target.freq_khz == 500_000


def test_retune_target_unknown_name_points_to_retune_area():
    channels = make(BOUNDARY)
    target, errors = parse_retune_target("ZZ", channels)
    assert target is None
    assert len(errors) == 1
    assert errors[0].line == RETUNE_LINE == -1
    assert "ZZ" in errors[0].message and "不在当前清单" in errors[0].message


def test_retune_target_missing_or_non_string():
    channels = make(BOUNDARY)
    for bad in [None, "", "   ", 123, ["A"], {"name": "A"}]:
        target, errors = parse_retune_target(bad, channels)
        assert target is None, bad
        assert [e.line for e in errors] == [RETUNE_LINE]
        assert "微调频道缺失" in errors[0].message


def test_retune_target_name_stripped_before_match():
    channels = make(BOUNDARY)
    target, errors = parse_retune_target("  A  ", channels)
    assert errors == []
    assert target.name == "A"


# ---------- 枚举与排名：复用整数 kHz 冲突分析 ----------


def test_retune_finds_multiple_improvements_sorted_stably():
    channels = make(BOUNDARY)
    ev = retune(channels, "A")
    assert ev.name == "A"
    assert ev.original_freq_khz == 500_000
    assert ev.baseline_conflict_count == 4
    got = [(s.freq_khz, s.move_khz, s.conflict_count, s.reduced_count) for s in ev.suggestions]
    assert got == [
        (499_875, 125, 0, 4),
        (500_125, 125, 0, 4),  # 移动距离相同，按频率升序打破平局
        (499_825, 175, 0, 4),
        (499_800, 200, 0, 4),
        (499_775, 225, 0, 4),
    ]
    # 排名键（冲突总数，移动距离，频率升序）单调不减
    keys = [(s.conflict_count, s.move_khz, s.freq_khz) for s in ev.suggestions]
    assert keys == sorted(keys)


def test_retune_suggestions_match_direct_reanalysis():
    # 每条建议的冲突数与「替换后整表重算」一致，且严格优于当前清单
    channels = make(BOUNDARY)
    ev = retune(channels, "A")
    assert ev.suggestions, "应找到改善频点"
    for s in ev.suggestions:
        replaced = [
            c if c.name != "A" else type(c)("A", s.freq_khz, c.line) for c in channels
        ]
        assert len(analyze(replaced)) == s.conflict_count
        assert s.conflict_count < ev.baseline_conflict_count
        assert s.reduced_count == ev.baseline_conflict_count - s.conflict_count
        assert s.move_khz == abs(s.freq_khz - ev.original_freq_khz)


def test_retune_candidates_legal_unoccupied_and_on_grid():
    channels = make(BOUNDARY)
    ev = retune(channels, "A")
    occupied = {c.freq_khz for c in channels}
    for s in ev.suggestions:
        assert MIN_KHZ <= s.freq_khz <= MAX_KHZ, "替换频点必须合法（频段内）"
        assert s.freq_khz not in occupied, "替换频点不得占用其他频道"
        assert s.freq_khz != ev.original_freq_khz, "原位不算微调"
        assert (s.freq_khz - ev.original_freq_khz) % RETUNE_STEP_KHZ == 0
        assert 0 < s.move_khz <= RETUNE_RANGE_KHZ


def test_retune_limited_to_five_suggestions():
    # C 的改善频点共 20 个，只返回排名前 5
    channels = make(BOUNDARY)
    ev = retune(channels, "C")
    assert len(ev.suggestions) == RETUNE_MAX_SUGGESTIONS == 5
    got = [(s.freq_khz, s.move_khz, s.conflict_count) for s in ev.suggestions]
    assert got == [
        (499_825, 25, 2),
        (499_675, 175, 2),
        (499_650, 200, 2),
        (499_625, 225, 2),
        (499_600, 250, 2),
    ]
    assert all(s.reduced_count == 2 for s in ev.suggestions)


def test_retune_plus_500khz_edge_candidate_participates():
    # CH3 +500 kHz（595.275）恰为搜索边界：少枚举一步就会漏掉这条建议
    channels = make(EDGE)
    ev = retune(channels, "CH3")
    assert ev.baseline_conflict_count == 4
    got = [(s.freq_khz, s.move_khz, s.conflict_count, s.reduced_count) for s in ev.suggestions]
    assert got == [
        (595_200, 425, 2, 2),
        (595_225, 450, 2, 2),
        (595_250, 475, 2, 2),
        (595_275, 500, 2, 2),  # 恰好 +500 kHz 的边界候选
    ]


def test_retune_band_edge_candidate_participates():
    # 470.000 MHz 带缘本身可作为替换频点，且排在首位
    channels = make(BAND_EDGE)
    ev = retune(channels, "CH0")
    assert ev.baseline_conflict_count == 4
    first = ev.suggestions[0]
    assert (first.freq_khz, first.move_khz, first.conflict_count) == (470_000, 100, 0)
    assert all(s.freq_khz >= MIN_KHZ for s in ev.suggestions)


def test_retune_no_improvement_returns_empty_suggestions():
    # E 与任何冲突无关：±500 kHz 内没有更优方案
    channels = make(BOUNDARY + "\nE 600.000")
    ev = retune(channels, "E")
    assert ev.baseline_conflict_count == 4
    assert ev.suggestions == []


def test_retune_safe_list_cannot_improve():
    channels = make(SAFE)
    ev = retune(channels, "C1")
    assert ev.baseline_conflict_count == 0
    assert ev.suggestions == []


def test_retune_does_not_mutate_input_channels():
    channels = make(BOUNDARY)
    before = [(c.name, c.freq_khz, c.line) for c in channels]
    retune(channels, "A")
    assert [(c.name, c.freq_khz, c.line) for c in channels] == before


def test_retune_baseline_conflicts_preserved():
    channels = make(BOUNDARY)
    ev = retune(channels, "A")
    baseline = analyze(channels)
    assert [
        (c.target_name, c.product_khz, c.sources) for c in ev.baseline_conflicts
    ] == [(c.target_name, c.product_khz, c.sources) for c in baseline]
