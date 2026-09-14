"""应用微调频点（直接采用一条建议）的单元测试。

覆盖：
- manifest_version 由频道名称、整数 kHz 与顺序确定；仅改注释/空白版本不变，
  改名、改频、调序版本必变；
- parse_retune_apply 对非对象/缺字段/类型错误一律按微调区（RETUNE_LINE=-1）拒绝；
- validate_retune_apply 先比对版本，再确认目标频道，最后按现有规则重算建议集；
- apply_retune 只替换目标行频率（分隔符/缩进/注释/空白原样保留），
  重算冲突与摘要，行号不变。
"""

from app.imd import (
    RETUNE_LINE,
    analyze,
    apply_retune,
    apply_retune_text,
    evaluate_retune,
    manifest_version,
    parse_channels,
    parse_retune_apply,
    parse_retune_target,
    validate_retune_apply,
)

BOUNDARY = "A 500.000\nB 500.100\nC 499.850\nD 500.250"


def make(text: str):
    channels, errors = parse_channels(text)
    assert errors == []
    return channels


def suggestion_for(channels, name, freq_khz):
    target, errors = parse_retune_target(name, channels)
    assert errors == []
    evaluation = evaluate_retune(channels, target)
    hits = [s for s in evaluation.suggestions if s.freq_khz == freq_khz]
    assert len(hits) == 1, f"{freq_khz} 应恰为一条建议"
    return target, hits[0], evaluation.version


# ---------- 清单版本标识 ----------


def test_manifest_version_determined_by_names_freqs_and_order():
    channels = make(BOUNDARY)
    v = manifest_version(channels)
    assert v.startswith("mv1-") and len(v) == len("mv1-") + 64
    # 确定性：同序列重复计算一致
    assert manifest_version(make(BOUNDARY)) == v

    # 改名 / 改频 / 调序都会改变版本
    assert manifest_version(make("A2 500.000\nB 500.100\nC 499.850\nD 500.250")) != v
    assert manifest_version(make("A 500.001\nB 500.100\nC 499.850\nD 500.250")) != v
    assert manifest_version(make("B 500.100\nA 500.000\nC 499.850\nD 500.250")) != v
    # 500.1 与 500.100 为同一整数 kHz：版本相同
    assert (
        manifest_version(make("A 500.000\nB 500.1\nC 499.850\nD 500.250"))
        == manifest_version(make("A 500.000\nB 500.100\nC 499.850\nD 500.250"))
    )


def test_manifest_version_ignores_comments_and_whitespace():
    base = make(BOUNDARY)
    edited = make(
        "# 演出前排频清单\n"
        "  A 500.000  \n"
        "\n"
        "B\t500.100\n"
        "# 主持人话筒：\n"
        "C 499.850\n"
        "D 500.250\n"
    )
    assert manifest_version(edited) == manifest_version(base)


def test_evaluate_retune_carries_manifest_version():
    channels = make(BOUNDARY)
    evaluation = evaluate_retune(channels, channels[0])
    assert evaluation.version == manifest_version(channels)


# ---------- 应用请求结构校验 ----------


def test_parse_retune_apply_accepts_well_formed_request():
    channels = make(BOUNDARY)
    name, freq_khz, version, errors = parse_retune_apply(
        {"name": "A", "freq_khz": 499_875, "version": manifest_version(channels)},
        channels,
    )
    assert errors == []
    assert (name, freq_khz) == ("A", 499_875)
    assert version == manifest_version(channels)


def test_parse_retune_apply_rejects_non_object_and_missing_fields():
    channels = make(BOUNDARY)
    version = manifest_version(channels)
    bad_values = [
        "x",
        123,
        [],
        {},
        {"name": "A"},
        {"name": "A", "freq_khz": 499_875},
        {"freq_khz": 499_875, "version": version},
        {"name": "", "freq_khz": 499_875, "version": version},
        {"name": "  ", "freq_khz": 499_875, "version": version},
        {"name": 9, "freq_khz": 499_875, "version": version},
    ]
    for value in bad_values:
        _, _, _, errors = parse_retune_apply(value, channels)
        assert errors and all(e.line == RETUNE_LINE == -1 for e in errors), value


def test_parse_retune_apply_rejects_bad_freq_type():
    channels = make(BOUNDARY)
    version = manifest_version(channels)
    for bad_freq in ["499875", 499_875.0, True, None, [499_875]]:
        _, _, _, errors = parse_retune_apply(
            {"name": "A", "freq_khz": bad_freq, "version": version}, channels
        )
        assert errors and errors[0].line == RETUNE_LINE, bad_freq
        assert "频率" in errors[0].message


def test_parse_retune_apply_rejects_bad_version_shape():
    channels = make(BOUNDARY)
    for bad_version in ["", "garbage", "mv1-xyz", "mv0-" + "a" * 64, 123, None, []]:
        _, _, _, errors = parse_retune_apply(
            {"name": "A", "freq_khz": 499_875, "version": bad_version}, channels
        )
        assert errors and errors[0].line == RETUNE_LINE, bad_version
        assert "版本" in errors[0].message


# ---------- 版本比对与建议集重算 ----------


def test_validate_retune_apply_accepts_returned_suggestion():
    channels = make(BOUNDARY)
    version = manifest_version(channels)
    target, suggestion, errors = validate_retune_apply(channels, "A", 499_875, version)
    assert errors == []
    assert target.name == "A" and suggestion.freq_khz == 499_875
    assert suggestion.conflict_count == 0


def test_validate_retune_apply_stale_version_fails_deterministically():
    channels = make(BOUNDARY)
    # 改名与改频两种清单变化都必须用旧版本标识被拒
    for changed_text in [
        "A2 500.000\nB 500.100\nC 499.850\nD 500.250",
        "A 500.001\nB 500.100\nC 499.850\nD 500.250",
        "B 500.100\nA 500.000\nC 499.850\nD 500.250",
    ]:
        changed = make(changed_text)
        _, _, errors = validate_retune_apply(changed, "A", 499_875, manifest_version(channels))
        assert len(errors) == 1 and errors[0].line == RETUNE_LINE
        assert errors[0].message == "清单已变化，请重新查找建议"


def test_validate_retune_apply_comment_only_edit_still_applies():
    base = make(BOUNDARY)
    version = manifest_version(base)
    edited_text = "# 调整了注释\nA 500.000\nB 500.100\nC 499.850\nD 500.250\n"
    edited = make(edited_text)
    target, suggestion, errors = validate_retune_apply(edited, "A", 499_875, version)
    assert errors == []
    assert target is not None and suggestion is not None


def test_validate_retune_apply_unknown_target_after_version_match():
    channels = make(BOUNDARY)
    version = manifest_version(channels)
    _, _, errors = validate_retune_apply(channels, "ZZ", 499_875, version)
    assert [e.line for e in errors] == [RETUNE_LINE]
    assert "ZZ" in errors[0].message and "不在当前清单" in errors[0].message


def test_validate_retune_apply_rejects_frequency_not_in_suggestion_set():
    channels = make(BOUNDARY)
    version = manifest_version(channels)
    # 其他频道占用频点、原位频率、非 25 kHz 网格、带外频点均不在建议集中
    for bad_freq in [500_100, 500_000, 499_999, 469_975]:
        _, _, errors = validate_retune_apply(channels, "A", bad_freq, version)
        assert len(errors) == 1 and errors[0].line == RETUNE_LINE, bad_freq
        assert "建议不可用" in errors[0].message, bad_freq


def test_validate_retune_apply_recomputes_against_current_rules():
    # 清单变化后用伪造的「当前版本」也不能让旧频点蒙混过关：
    # 499.875 相对新原位 500.001 偏离 126 kHz，不在 25 kHz 网格上，
    # 按现有规则重算的建议集中不可能包含它
    changed = make("A 500.001\nB 500.100\nC 499.850\nD 500.250")
    current_version = manifest_version(changed)
    _, suggestion, errors = validate_retune_apply(changed, "A", 499_875, current_version)
    assert suggestion is None
    assert len(errors) == 1 and "建议不可用" in errors[0].message


# ---------- 应用：只替换目标行并重算 ----------


def test_apply_retune_replaces_only_target_line():
    channels = make(BOUNDARY)
    target, suggestion, _ = suggestion_for(channels, "A", 499_875)
    result = apply_retune(BOUNDARY, channels, target, suggestion)
    assert result.applied_text == "A 499.875\nB 500.100\nC 499.850\nD 500.250"
    assert result.name == "A" and result.freq_khz == 499_875
    assert [(c.name, c.freq_khz, c.line) for c in result.applied_channels] == [
        ("A", 499_875, 1),
        ("B", 500_100, 2),
        ("C", 499_850, 3),
        ("D", 500_250, 4),
    ]
    # 冲突数按返回结果下降到 0，且与直接重算一致
    assert len(result.conflicts) == 0
    assert analyze(result.applied_channels) == result.conflicts
    # 新版本标识与替换后清单一致，旧版本必然不同
    assert result.version == manifest_version(result.applied_channels)
    assert result.version != manifest_version(channels)


def test_apply_retune_preserves_separators_indentation_comments_and_blank_lines():
    text = (
        "# 标题注释\n"
        "\n"
        "  A,500.000\n"
        "B\t500.100\n"
        "C，499.850\n"
        "D 500.250   \n"
    )
    channels = make(text)
    target, suggestion, _ = suggestion_for(channels, "A", 499_875)
    result = apply_retune(text, channels, target, suggestion)
    lines = result.applied_text.splitlines()
    assert lines[0] == "# 标题注释"
    assert lines[1] == ""
    assert lines[2] == "  A,499.875"  # 缩进与逗号分隔符保留
    assert lines[3] == "B\t500.100"  # 其余行原样
    assert lines[4] == "C，499.850"
    assert lines[5] == "D 500.250   "  # 行尾空白保留


def test_apply_retune_text_preserves_trailing_newline():
    channels = make(BOUNDARY + "\n")
    target = channels[0]
    out = apply_retune_text(BOUNDARY + "\n", target, 499_875)
    assert out.endswith("\n")
    assert out == "A 499.875\nB 500.100\nC 499.850\nD 500.250\n"


def test_apply_retune_does_not_mutate_inputs():
    channels = make(BOUNDARY)
    before = [(c.name, c.freq_khz, c.line) for c in channels]
    target, suggestion, _ = suggestion_for(channels, "A", 499_875)
    apply_retune(BOUNDARY, channels, target, suggestion)
    assert [(c.name, c.freq_khz, c.line) for c in channels] == before
