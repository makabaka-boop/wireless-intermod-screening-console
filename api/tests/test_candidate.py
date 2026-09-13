"""候选试加（彩排临时借话筒）的单元测试。

覆盖：候选解析与复用整数 kHz 口径、重复/越界/格式错误指向候选区、
32 路合并上限、安全候选零增量、风险候选为自身与既有频道引入冲突、
以（目标，产物，来源）稳定键比较基线与合并结果（含新增来源组合）。
"""

from app.imd import (
    CANDIDATE_LINE,
    Channel,
    analyze,
    evaluate_candidate,
    parse_candidate,
    parse_channels,
)

BOUNDARY = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
SAFE = "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000"


def make(text: str):
    channels, errors = parse_channels(text)
    assert errors == []
    return channels


def candidate_ok(channels, name, freq):
    cand, errors = parse_candidate(name, freq, channels)
    assert errors == [], f"候选不应有错误: {errors}"
    return cand


def candidate_errs(channels, name, freq):
    cand, errors = parse_candidate(name, freq, channels)
    assert cand is None
    return errors


# ---------- 候选解析：复用格式/范围/整数 kHz 口径 ----------


def test_candidate_parsed_to_integer_khz():
    cand = candidate_ok(make(SAFE), "X", "470.1")
    assert cand == Channel(name="X", freq_khz=470_100, line=CANDIDATE_LINE)


def test_candidate_duplicate_name_rejected_at_candidate_area():
    errors = candidate_errs(make(BOUNDARY), "A", "520.000")
    assert len(errors) == 1
    assert errors[0].line == CANDIDATE_LINE
    assert "重复" in errors[0].message and "A" in errors[0].message


def test_candidate_duplicate_frequency_normalized_rejected():
    # 500.1 与清单中的 500.100 同频
    errors = candidate_errs(make(BOUNDARY), "X", "500.1")
    assert len(errors) == 1
    assert errors[0].line == CANDIDATE_LINE and "重复" in errors[0].message


def test_candidate_bad_format_and_out_of_range_rejected():
    errs = candidate_errs(make(SAFE), "X", "500.1234")
    assert errs[0].line == CANDIDATE_LINE and "三位小数" in errs[0].message

    errs = candidate_errs(make(SAFE), "X", "694.001")
    assert errs[0].line == CANDIDATE_LINE and "超出允许范围" in errs[0].message

    errs = candidate_errs(make(SAFE), "X", "469.999")
    assert errs[0].line == CANDIDATE_LINE and "超出允许范围" in errs[0].message


def test_candidate_band_edges_accepted():
    assert candidate_ok(make(SAFE), "LO", "470.000").freq_khz == 470_000
    assert candidate_ok(make(SAFE), "HI", "694.000").freq_khz == 694_000


def test_candidate_missing_name_or_freq():
    errs = candidate_errs(make(SAFE), "  ", "500.000")
    assert errs[0].line == CANDIDATE_LINE and "候选名称缺失" in errs[0].message

    errs = candidate_errs(make(SAFE), "X", "")
    assert errs[0].line == CANDIDATE_LINE and "候选频率缺失" in errs[0].message

    errs = candidate_errs(make(SAFE), None, None)
    assert {e.line for e in errs} == {CANDIDATE_LINE}
    assert len(errs) == 2


def test_candidate_name_with_separator_rejected():
    for bad in ["X 1", "X,Y", "X，Y"]:
        errs = candidate_errs(make(SAFE), bad, "500.000")
        assert errs and errs[0].line == CANDIDATE_LINE and "空白或逗号" in errs[0].message


def test_candidate_merge_over_32_rejected():
    channels = make("\n".join(f"CH{i} {500 + i * 5}.000" for i in range(32)))
    errs = candidate_errs(channels, "X", "690.000")
    assert len(errs) == 1
    assert errs[0].line == CANDIDATE_LINE and "上限 32" in errs[0].message


def test_candidate_31_plus_one_accepted():
    channels = make("\n".join(f"CH{i} {500 + i * 5}.000" for i in range(31)))
    assert candidate_ok(channels, "X", "690.000")


# ---------- 增量评估：稳定键比较基线与合并结果 ----------


def test_safe_candidate_has_no_increment():
    channels = make(SAFE)
    ev = evaluate_candidate(channels, candidate_ok(channels, "X", "470.100"))
    assert ev.status == "safe"
    assert ev.new_conflicts == []
    assert ev.affected_channel_names == []
    assert ev.baseline_conflict_count == 0
    assert len(ev.merged_conflicts) == 0
    assert ev.merged_channel_count == 6


def test_safe_candidate_keeps_baseline_conflicts_unchanged():
    # 基线本身有冲突，但候选不引入任何增量：仍判 safe
    channels = make(BOUNDARY)
    # 在扫描结果中 470.100 对 BOUNDARY 是否无增量需断言确认
    ev = evaluate_candidate(channels, candidate_ok(channels, "X", "470.100"))
    assert ev.new_conflicts == []
    assert ev.status == "safe"
    assert len(ev.merged_conflicts) == ev.baseline_conflict_count == 4


def test_risky_candidate_hits_itself_and_existing_channels():
    channels = make(BOUNDARY)
    ev = evaluate_candidate(channels, candidate_ok(channels, "X", "499.900"))
    assert ev.status == "risky"
    # 候选自身被既有产物命中
    self_hits = [c for c in ev.new_conflicts if c.target_is_candidate]
    assert {c.target_name for c in self_hits} == {"X"}
    assert {(c.product_khz, c.diff_khz) for c in self_hits} == {
        (499_900, 0),  # 2A−B 恰好落在候选频率
        (499_950, 50),  # 2B−D 距候选 50 kHz
    }
    # 既有频道也被候选参与的新产物命中
    existing = [c for c in ev.new_conflicts if not c.target_is_candidate]
    assert {c.target_name for c in existing} == {"A", "B", "C", "D"}
    # 受影响频道同时包含候选自身与既有频道
    assert ev.affected_channel_names == ["A", "B", "C", "D", "X"]
    # 基线 4 项 → 合并 9 项，新增 6 项；基线分组原样保留
    assert ev.baseline_conflict_count == 4
    assert len(ev.merged_conflicts) == 9
    assert len(ev.new_conflicts) == 6
    # 新增冲突沿用 目标频率 → 产物频率 → 目标名称 排序
    keys = [(c.target_freq_khz, c.product_khz, c.target_name) for c in ev.new_conflicts]
    assert keys == sorted(keys)


def test_new_conflict_distinguishes_existing_and_new_source_pairs():
    # A 在 499950 上基线已有来源 [B,D]；候选加入后同一目标+产物新增来源 [C,X]
    channels = make(BOUNDARY)
    ev = evaluate_candidate(channels, candidate_ok(channels, "X", "499.900"))
    a_row = next(
        c
        for c in ev.new_conflicts
        if c.target_name == "A" and c.product_khz == 499_950
    )
    assert a_row.sources == [("B", "D"), ("C", "X")]
    assert a_row.new_sources == [("C", "X")]
    # 基线里完全不存在的目标+产物：全部来源都是新增
    c_row = next(
        c
        for c in ev.new_conflicts
        if c.target_name == "C" and c.product_khz == 499_800
    )
    assert c_row.sources == c_row.new_sources == [("A", "X")]
    # 新增来源组合：目标是既有频道时必然含候选；候选自身被命中时，
    # 产物来自基线频道对（候选不能是自己的产物来源）
    for nc in ev.new_conflicts:
        if nc.target_is_candidate:
            assert all("X" not in pair for pair in nc.new_sources)
        else:
            assert all("X" in pair for pair in nc.new_sources)


def test_baseline_conflicts_preserved_exactly():
    channels = make(BOUNDARY)
    cand = candidate_ok(channels, "X", "499.900")
    ev = evaluate_candidate(channels, cand)
    baseline = analyze(channels)
    assert [
        (c.target_name, c.product_khz, c.sources) for c in ev.baseline_conflicts
    ] == [(c.target_name, c.product_khz, c.sources) for c in baseline]


def test_merged_conflicts_reuse_sorting_and_dedup():
    channels = make(BOUNDARY)
    ev = evaluate_candidate(channels, candidate_ok(channels, "X", "499.900"))
    merged = ev.merged_conflicts
    keys = [(c.target_freq_khz, c.product_khz, c.target_name) for c in merged]
    assert keys == sorted(keys)
    # 合并结果等价于直接对基线+候选整体分析
    direct = analyze(channels + [Channel("X", 499_900, CANDIDATE_LINE)])
    assert [
        (c.target_name, c.product_khz, c.sources) for c in merged
    ] == [(c.target_name, c.product_khz, c.sources) for c in direct]
