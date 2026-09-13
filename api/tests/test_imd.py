"""imd 核心逻辑单元测试：边界、排序、去重、解析与校验。"""

from app.imd import (
    GUARD_KHZ,
    analyze,
    build_summary,
    format_mhz,
    parse_channels,
    parse_freq_khz,
)


def make(text: str):
    """解析并断言无错误，返回频道列表。"""
    channels, errors = parse_channels(text)
    assert errors == [], f"不应有解析错误: {errors}"
    return channels


def conflict_tuples(conflicts):
    return [
        (c.target_name, c.product_khz, c.diff_khz, [tuple(s) for s in c.sources])
        for c in conflicts
    ]


# ---------- 频率换算 ----------


def test_freq_parse_exact_integer_khz():
    assert parse_freq_khz("500") == 500_000
    assert parse_freq_khz("500.1") == 500_100
    assert parse_freq_khz("500.12") == 500_120
    assert parse_freq_khz("500.123") == 500_123
    assert parse_freq_khz("470.000") == 470_000
    assert parse_freq_khz("694.000") == 694_000


def test_freq_parse_rejects_bad_format():
    for bad in ["500.1234", "500.", ".5", "abc", "500 MHz", "1.2.3", "-500", ""]:
        assert parse_freq_khz(bad) is None, bad


def test_format_mhz():
    assert format_mhz(499_900) == "499.900"
    assert format_mhz(694_000) == "694.000"


# ---------- 保护带边界：恰好 50 kHz 命中，51 kHz 安全 ----------


def test_guard_band_exact_50khz_hit():
    channels = make("A 500.000\nB 500.100\nC 499.850\nD 500.250")
    conflicts = analyze(channels)
    assert conflict_tuples(conflicts) == [
        ("C", 499_900, 50, [("A", "B")]),  # 2A−B = 499.900，距 C 恰好 50 kHz
        ("A", 499_950, 50, [("B", "D")]),  # 2B−D = 499.950，距 A 恰好 50 kHz
        ("B", 500_150, 50, [("A", "C")]),  # 2A−C = 500.150，距 B 恰好 50 kHz
        ("D", 500_200, 50, [("A", "B")]),  # 2B−A = 500.200，距 D 恰好 50 kHz
    ]
    assert all(c.diff_khz == GUARD_KHZ for c in conflicts)


def test_guard_band_49khz_hit_51khz_safe():
    hit = analyze(make("A 500.000\nB 500.100\nC 499.851"))
    assert any(c.target_name == "C" and c.diff_khz == 49 for c in hit)

    safe = analyze(make("A 500.000\nB 500.100\nC 499.849\nD 500.251"))
    assert safe == [], "差值 51 kHz 超出保护带，不应记为冲突"


# ---------- 频段边界：带外产物剔除，带缘产物保留 ----------


def test_product_below_band_edge_filtered():
    # 2A−B = 469.980 MHz，距 C(470.000) 仅 20 kHz，但产物在频段外，必须剔除
    channels = make("A 470.020\nB 470.060\nC 470.000")
    conflicts = analyze(channels)
    assert conflict_tuples(conflicts) == [("B", 470_040, 20, [("A", "C")])]
    assert all(c.product_khz >= 470_000 for c in conflicts)


def test_product_at_band_edges_kept():
    low = analyze(make("A 470.100\nB 470.200\nC 470.050"))
    assert ("C", 470_000, 50, [("A", "B")]) in conflict_tuples(low)

    high = analyze(make("A 693.800\nB 693.900\nC 693.950"))
    assert ("C", 694_000, 50, [("A", "B")]) in conflict_tuples(high)


# ---------- 去重、来源合并与排序 ----------


def test_dedup_merges_sources_and_sorts_results():
    channels = make(
        "T 500.000\nA 500.100\nB 500.150\nC 500.200\nD 500.350"
    )
    conflicts = analyze(channels)
    assert conflict_tuples(conflicts) == [
        ("T", 499_950, 50, [("B", "D")]),
        ("T", 500_000, 0, [("A", "C")]),
        ("T", 500_050, 50, [("A", "B"), ("C", "D")]),  # 同一产物+目标只展示一次，列全来源
        ("A", 500_050, 50, [("C", "D")]),
        ("A", 500_100, 0, [("B", "C")]),
        ("B", 500_200, 50, [("A", "T")]),
        ("C", 500_200, 0, [("A", "B"), ("A", "T")]),
        ("D", 500_300, 50, [("A", "C"), ("B", "T")]),
        ("D", 500_400, 50, [("C", "T")]),
    ]


def test_sort_by_target_freq_then_product_then_name():
    # 目标名 Zed 频率最低，应排在 Beta 之前（按频率而非名称）
    channels = make("Zed 500.000\nAlpha 500.100\nBeta 500.150")
    conflicts = analyze(channels)
    assert [c.target_name for c in conflicts] == ["Zed", "Beta"]
    assert conflict_tuples(conflicts) == [
        ("Zed", 500_050, 50, [("Alpha", "Beta")]),
        ("Beta", 500_200, 50, [("Alpha", "Zed")]),
    ]


def test_safe_list_zero_conflicts():
    channels = make("C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000")
    assert analyze(channels) == []
    assert "零项冲突" in build_summary(channels, [])


# ---------- 输入解析与校验（错误必须带行号） ----------


def test_parse_skips_blank_and_comment_lines_keeping_line_numbers():
    channels = make("# 彩排清单\n\nCH1 500.000\nCH2 510.000\n")
    assert [c.line for c in channels] == [3, 4]


def test_parse_supports_comma_and_chinese_names():
    channels = make("话筒一,500.000\n话筒二，510.000")
    assert [c.name for c in channels] == ["话筒一", "话筒二"]


def test_names_with_dots_and_long_names_accepted():
    long_name = "无线话筒-东区." + "甲" * 40
    channels = make(f"Mic.01 500.000\n{long_name} 510.000")
    assert [c.name for c in channels] == ["Mic.01", long_name]


def test_duplicate_dotted_name_rejected():
    _, errors = parse_channels("Mic.1 500.000\nMic.1 510.000")
    assert len(errors) == 1
    assert errors[0].line == 2 and "唯一" in errors[0].message


def test_error_more_than_three_decimals():
    _, errors = parse_channels("CH1 500.0005\nCH2 510.000")
    assert [(e.line, "三位小数" in e.message) for e in errors] == [(1, True)]


def test_error_out_of_range_both_ends():
    _, errors = parse_channels("CH1 469.999\nCH2 694.001")
    assert [e.line for e in errors] == [1, 2]
    assert all("超出允许范围" in e.message for e in errors)


def test_error_duplicate_frequency_normalized():
    # 500.1 与 500.100 是同一频率
    _, errors = parse_channels("CH1 500.1\nCH2 510.000\nCH3 500.100")
    assert len(errors) == 1
    assert errors[0].line == 3 and "第 1 行重复" in errors[0].message


def test_error_duplicate_name():
    _, errors = parse_channels("CH1 500.000\nCH2 510.000\nCH1 520.000")
    assert len(errors) == 1
    assert errors[0].line == 3 and "第 1 行重复" in errors[0].message


def test_error_malformed_line():
    _, errors = parse_channels("CH1 500.000\n只写了名字\nCH2 510.000")
    assert [e.line for e in errors] == [2]
    assert "格式错误" in errors[0].message


def test_error_channel_count_limits():
    _, errors = parse_channels("CH1 500.000")
    assert len(errors) == 1 and errors[0].line == 1 and "至少需要 2 个" in errors[0].message

    many = "\n".join(f"CH{i} {470 + i}.000" for i in range(33))
    _, errors = parse_channels(many)
    assert len(errors) == 1 and errors[0].line == 33 and "上限 32" in errors[0].message


def test_error_empty_input():
    _, errors = parse_channels("   \n# 只有注释\n")
    assert len(errors) == 1 and errors[0].line == 1


def test_errors_aggregated_with_line_numbers():
    _, errors = parse_channels(
        "CH1 500.0005\nCH1 510.000\nCH2 469.000\nCH3 510.000"
    )
    lines = sorted(e.line for e in errors)
    assert lines == [1, 2, 3, 4], "每一行的错误都必须收集并带行号"


def test_boundary_frequencies_accepted():
    channels = make("LO 470.000\nHI 694.000")
    assert [c.freq_khz for c in channels] == [470_000, 694_000]
