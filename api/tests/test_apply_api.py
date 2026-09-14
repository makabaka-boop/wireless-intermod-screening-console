"""/api/analyze 直接应用微调建议（apply）的接口测试。

验收对应项：
- 未编辑清单可应用返回的建议：顶层为替换后清单的完整分析，
  channels/conflicts/summary 与 applied.applied_text 重新解析的结果一致，
  冲突数按返回结果下降；响应只多出 applied 字段；
- 改名或改频后携带旧版本标识确定性失败（422，line=-1，提示「清单已变化」）；
- 仅调整注释或空白而频道序列未变时仍可应用，且注释/空白原样保留；
- 未被返回的频率（占用频点/原位/非网格/带外）遭拒绝；
- 非对象或缺字段等结构错误指向微调区（line=-1）；
- 清单非法仍按原行号整批拒绝；普通分析/候选试加/聚焦/仅查询微调建议
  的请求字段与响应语义不变（含 manifest_version 只出现在 retune 中、
  普通响应与失败响应不出现 applied 字段）。
"""

from fastapi.testclient import TestClient

from app.imd import parse_channels
from app.main import app

client = TestClient(app)

BOUNDARY_INPUT = "A 500.000\nB 500.100\nC 499.850\nD 500.250"


def get_version(text: str, name: str = "A") -> tuple[str, list[dict]]:
    resp = client.post("/api/analyze", json={"input": text, "retune": name})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["retune"]["manifest_version"], data["retune"]["suggestions"]


def apply_suggestion(text: str, name: str, freq_khz: int, version: str):
    return client.post(
        "/api/analyze",
        json={"input": text, "apply": {"name": name, "freq_khz": freq_khz, "version": version}},
    )


def test_retune_response_carries_manifest_version():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "retune": "A"})
    data = resp.json()
    version = data["retune"]["manifest_version"]
    assert isinstance(version, str) and version.startswith("mv1-")
    # 同清单重复查询版本稳定
    again = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "retune": "B"})
    assert again.json()["retune"]["manifest_version"] == version


def test_apply_suggestion_replaces_only_target_and_returns_full_analysis():
    version, suggestions = get_version(BOUNDARY_INPUT)
    first = suggestions[0]
    assert first["freq_khz"] == 499_875 and first["conflict_count"] == 0

    resp = apply_suggestion(BOUNDARY_INPUT, "A", 499_875, version)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # 只替换目标行频率，其余行原样；applied_text 可直接回填编辑区
    expected_text = "A 499.875\nB 500.100\nC 499.850\nD 500.250"
    assert data["applied"] == {
        "name": "A",
        "freq_khz": 499_875,
        "version": data["applied"]["version"],
        "applied_text": expected_text,
    }
    assert data["applied"]["version"].startswith("mv1-")
    assert data["channels"] == [
        {"name": "A", "freq_khz": 499_875, "line": 1},
        {"name": "B", "freq_khz": 500_100, "line": 2},
        {"name": "C", "freq_khz": 499_850, "line": 3},
        {"name": "D", "freq_khz": 500_250, "line": 4},
    ]
    # 冲突数按返回结果下降（4 -> 0），完整分组与摘要均针对替换后清单
    assert data["conflict_count"] == 0
    assert data["conflicts"] == []
    assert "冲突总数：0" in data["summary"] and "零项冲突" in data["summary"]
    # 顶层分析与 applied_text 重新解析 + 分析的结果自洽
    plain = client.post("/api/analyze", json={"input": expected_text}).json()
    assert data["channels"] == plain["channels"]
    assert data["conflicts"] == plain["conflicts"]
    assert data["summary"] == plain["summary"]
    # 应用成功后响应只多出 applied 字段，不夹带 retune/candidate/focus
    assert set(data.keys()) == {
        "channel_count",
        "channels",
        "conflict_count",
        "conflicts",
        "summary",
        "applied",
    }


def test_apply_each_returned_suggestion_succeeds_and_counts_match():
    version, suggestions = get_version(BOUNDARY_INPUT)
    assert suggestions
    for s in suggestions:
        resp = apply_suggestion(BOUNDARY_INPUT, "A", s["freq_khz"], version)
        assert resp.status_code == 200, s
        data = resp.json()
        assert data["conflict_count"] == s["conflict_count"]
        assert data["applied"]["freq_khz"] == s["freq_khz"]
        assert data["applied"]["applied_text"].splitlines()[0] == (
            f"A {s['freq_khz'] // 1000}.{s['freq_khz'] % 1000:03d}"
        )


def test_apply_stale_version_after_rename_or_freq_change_fails():
    version, _ = get_version(BOUNDARY_INPUT)
    for changed in [
        "A2 500.000\nB 500.100\nC 499.850\nD 500.250",  # 改名
        "A 500.001\nB 500.100\nC 499.850\nD 500.250",  # 改频
        "B 500.100\nA 500.000\nC 499.850\nD 500.250",  # 调换顺序
    ]:
        resp = apply_suggestion(changed, "A", 499_875, version)
        assert resp.status_code == 422, changed
        data = resp.json()
        assert "applied" not in data and "conflicts" not in data
        errors = data["errors"]
        assert [e["line"] for e in errors] == [-1]
        assert errors[0]["message"] == "清单已变化，请重新查找建议"


def test_apply_succeeds_after_comment_or_whitespace_only_edit():
    version, _ = get_version(BOUNDARY_INPUT)
    edited = (
        "# 演出前排频表\n"
        "  A 500.000\n"
        "\n"
        "B   500.100\n"
        "C 499.850\n"
        "D 500.250\n"  # 仅新增尾换行
    )
    resp = apply_suggestion(edited, "A", 499_875, version)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 注释与空白原样保留，只有目标行频率被替换
    assert data["applied"]["applied_text"] == (
        "# 演出前排频表\n"
        "  A 499.875\n"
        "\n"
        "B   500.100\n"
        "C 499.850\n"
        "D 500.250\n"
    )
    # 行号按编辑后的文本报告（A 位于第 2 行）
    assert data["channels"][0] == {"name": "A", "freq_khz": 499_875, "line": 2}
    assert data["conflict_count"] == 0


def test_apply_rejects_frequency_not_in_suggestion_set():
    version, suggestions = get_version(BOUNDARY_INPUT)
    returned = {s["freq_khz"] for s in suggestions}
    for bad_freq in [500_100, 500_000, 499_999, 469_975, 500_500]:
        assert bad_freq not in returned
        resp = apply_suggestion(BOUNDARY_INPUT, "A", bad_freq, version)
        assert resp.status_code == 422, bad_freq
        data = resp.json()
        assert "applied" not in data and "conflicts" not in data
        errors = data["errors"]
        assert [e["line"] for e in errors] == [-1]
        assert "建议不可用" in errors[0]["message"]


def test_apply_unknown_channel_points_to_retune_area():
    version, _ = get_version(BOUNDARY_INPUT)
    resp = apply_suggestion(BOUNDARY_INPUT, "ZZ", 499_875, version)
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert [e["line"] for e in errors] == [-1]
    assert "ZZ" in errors[0]["message"]


def test_apply_malformed_requests_point_to_retune_area():
    cases = [
        "oops",
        123,
        [],
        {},
        {"name": "A", "freq_khz": 499_875},  # 缺 version
        {"name": "A", "version": "v"},  # 缺 freq_khz
        {"freq_khz": 499_875, "version": "v"},  # 缺 name
        {"name": "A", "freq_khz": "499875", "version": "v"},  # 频率非整数 kHz
        {"name": "A", "freq_khz": True, "version": "v"},
        {"name": "A", "freq_khz": 499_875, "version": "garbage"},
    ]
    for apply_value in cases:
        resp = client.post(
            "/api/analyze", json={"input": BOUNDARY_INPUT, "apply": apply_value}
        )
        assert resp.status_code == 422, apply_value
        data = resp.json()
        assert "applied" not in data and "conflicts" not in data
        errors = data["errors"]
        assert errors and all(e["line"] == -1 for e in errors), (apply_value, errors)


def test_apply_explicit_null_treated_as_absent():
    resp = client.post(
        "/api/analyze", json={"input": BOUNDARY_INPUT, "apply": None}
    )
    assert resp.status_code == 200
    assert "applied" not in resp.json()


def test_apply_with_invalid_list_keeps_original_line_numbers():
    version, _ = get_version(BOUNDARY_INPUT)
    resp = client.post(
        "/api/analyze",
        json={
            "input": "CH1 500.0005\nCH2 469.000",
            "apply": {"name": "A", "freq_khz": 499_875, "version": version},
        },
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "applied" not in data and "conflicts" not in data
    assert [e["line"] for e in data["errors"]] == [1, 2]


def test_apply_branch_ignores_other_optional_fields():
    version, _ = get_version(BOUNDARY_INPUT)
    resp = client.post(
        "/api/analyze",
        json={
            "input": BOUNDARY_INPUT,
            "apply": {"name": "A", "freq_khz": 499_875, "version": version},
            "retune": "A",
            "candidate": {"name": "X", "freq": "470.100"},
            "focus": ["A"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "applied" in data
    assert "retune" not in data and "candidate" not in data and "focus" not in data


def test_legacy_responses_never_contain_applied_field():
    # 普通分析
    plain = client.post("/api/analyze", json={"input": BOUNDARY_INPUT}).json()
    assert "applied" not in plain
    assert set(plain.keys()) == {
        "channel_count",
        "channels",
        "conflict_count",
        "conflicts",
        "summary",
    }
    # 候选试加 / 微调建议查询 / 聚焦排查响应同样不含 applied
    for payload in [
        {"input": BOUNDARY_INPUT, "candidate": {"name": "X", "freq": "470.100"}},
        {"input": BOUNDARY_INPUT, "retune": "A"},
        {"input": BOUNDARY_INPUT, "focus": ["A"]},
    ]:
        data = client.post("/api/analyze", json=payload).json()
        assert "applied" not in data, payload


def test_apply_response_version_matches_reparsed_manifest():
    # 应用后返回的新版本可直接用于下一轮查询/应用
    version, _ = get_version(BOUNDARY_INPUT)
    resp = apply_suggestion(BOUNDARY_INPUT, "A", 499_875, version)
    data = resp.json()
    new_text = data["applied"]["applied_text"]
    channels, errors = parse_channels(new_text)
    assert errors == []
    again = client.post("/api/analyze", json={"input": new_text, "retune": "B"}).json()
    assert again["retune"]["manifest_version"] == data["applied"]["version"]
    assert again["retune"]["baseline_conflict_count"] == 0
