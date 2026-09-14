"""/api/analyze 聚焦排查的接口测试。

验收对应项：
- 携带 focus 时返回聚焦评估对象：直接影响（目标即重点频道）整体排在
  来源相关（仅来源组合含重点频道）之前，两类内部保持原有冲突顺序；
- 同一条目含多组来源时不拆分、不重复，条目保留原目标、产物与全部来源；
- 无相关冲突时返回空聚焦结果；完整冲突分组与摘要不因聚焦而改写；
- 名称不存在、重复、超过三个或非列表值返回 422 且错误指向聚焦区（line=-2）；
- 清单非法仍按原行号整批拒绝；未携带聚焦参数的旧请求维持原字段语义。
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BOUNDARY_INPUT = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
SAFE_INPUT = "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000"
ORDERING_INPUT = "T 500.000\nA 500.100\nB 500.150\nC 500.200\nD 500.350"


def test_focus_direct_before_source_and_top_level_unchanged():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "focus": ["A"]})
    assert resp.status_code == 200
    data = resp.json()
    # 完整冲突分组与摘要不因聚焦而改写
    assert data["conflict_count"] == 4
    assert [c["target_name"] for c in data["conflicts"]] == ["C", "A", "B", "D"]
    plain = client.post("/api/analyze", json={"input": BOUNDARY_INPUT}).json()
    assert data["conflicts"] == plain["conflicts"]
    assert data["summary"] == plain["summary"]

    focus = data["focus"]
    assert focus["names"] == ["A"]
    assert focus["direct_count"] == 1
    assert focus["source_count"] == 3
    got = [(e["relation"], e["target_name"], e["product_khz"]) for e in focus["entries"]]
    assert got == [
        ("direct", "A", 499_950),  # 目标即重点频道，整体在前
        ("source", "C", 499_900),  # 仅来源含 A，保持原有冲突顺序
        ("source", "B", 500_150),
        ("source", "D", 500_200),
    ]
    # 每个聚焦条目保留原目标、产物与全部来源
    entry = focus["entries"][0]
    assert entry["target_freq_khz"] == 500_000
    assert entry["diff_khz"] == 50
    assert entry["sources"] == [["B", "D"]]


def test_focus_multi_source_entry_not_split_or_duplicated():
    resp = client.post("/api/analyze", json={"input": ORDERING_INPUT, "focus": ["T"]})
    assert resp.status_code == 200
    focus = resp.json()["focus"]
    assert focus["direct_count"] == 3
    assert focus["source_count"] == 4
    got = [(e["relation"], e["target_name"], e["product_khz"]) for e in focus["entries"]]
    assert got == [
        ("direct", "T", 499_950),
        ("direct", "T", 500_000),
        ("direct", "T", 500_050),
        ("source", "B", 500_200),
        ("source", "C", 500_200),
        ("source", "D", 500_300),
        ("source", "D", 500_400),
    ]
    # C 500200 含两组来源：只出现一次且列全来源
    hits = [
        e
        for e in focus["entries"]
        if (e["target_name"], e["product_khz"]) == ("C", 500_200)
    ]
    assert len(hits) == 1
    assert hits[0]["sources"] == [["A", "B"], ["A", "T"]]


def test_focus_multiple_channels_and_direct_takes_precedence():
    resp = client.post(
        "/api/analyze", json={"input": ORDERING_INPUT, "focus": ["A", "C"]}
    )
    assert resp.status_code == 200
    focus = resp.json()["focus"]
    assert focus["names"] == ["A", "C"]
    # A 500100 目标为 A、来源含 C：只按直接影响计一次
    hits = [
        e
        for e in focus["entries"]
        if (e["target_name"], e["product_khz"]) == ("A", 500_100)
    ]
    assert len(hits) == 1
    assert hits[0]["relation"] == "direct"
    relations = [e["relation"] for e in focus["entries"]]
    assert relations == sorted(relations, key=lambda r: 0 if r == "direct" else 1)


def test_focus_no_related_conflicts_returns_empty_entries():
    resp = client.post(
        "/api/analyze", json={"input": BOUNDARY_INPUT + "\nE 600.000", "focus": ["E"]}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["conflict_count"] == 4  # 完整结果保持原样
    focus = data["focus"]
    assert focus["names"] == ["E"]
    assert focus["entries"] == []
    assert focus["direct_count"] == 0
    assert focus["source_count"] == 0

    # 安全清单聚焦同样为空
    resp = client.post("/api/analyze", json={"input": SAFE_INPUT, "focus": ["C1"]})
    assert resp.status_code == 200
    assert resp.json()["focus"]["entries"] == []


def test_focus_unknown_name_points_to_focus_area():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "focus": ["ZZ"]})
    assert resp.status_code == 422
    data = resp.json()
    assert "conflicts" not in data, "聚焦选择非法时不得给出风险结果"
    errors = data["errors"]
    assert errors and all(e["line"] == -2 for e in errors)
    assert "ZZ" in errors[0]["message"] and "不在当前清单" in errors[0]["message"]


def test_focus_duplicate_and_over_three_rejected():
    resp = client.post(
        "/api/analyze", json={"input": BOUNDARY_INPUT, "focus": ["A", "C", "A"]}
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert all(e["line"] == -2 for e in errors)
    assert "重复" in errors[0]["message"]

    resp = client.post(
        "/api/analyze",
        json={"input": BOUNDARY_INPUT, "focus": ["A", "B", "C", "D"]},
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert all(e["line"] == -2 for e in errors)
    assert "最多选择 3 个" in errors[0]["message"]


def test_focus_non_list_and_empty_rejected():
    for bad in ["A", 123, {"name": "A"}, []]:
        resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "focus": bad})
        assert resp.status_code == 422, f"focus={bad!r} 应返回 422"
        errors = resp.json()["errors"]
        assert all(e["line"] == -2 for e in errors), bad


def test_focus_invalid_list_rejected_with_original_line_numbers():
    # 清单非法时仍按原行号整批拒绝，不评估聚焦、不给部分结果
    resp = client.post(
        "/api/analyze",
        json={"input": "CH1 500.0005\nCH2 469.000", "focus": ["CH1"]},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "conflicts" not in data
    assert [e["line"] for e in data["errors"]] == [1, 2]


def test_request_without_focus_keeps_legacy_schema():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT})
    assert resp.status_code == 200
    data = resp.json()
    assert "focus" not in data, "未传聚焦参数时响应不应出现 focus 字段"
    assert set(data.keys()) == {
        "channel_count",
        "channels",
        "conflict_count",
        "conflicts",
        "summary",
    }


def test_explicit_null_focus_treated_as_absent():
    resp = client.post("/api/analyze", json={"input": SAFE_INPUT, "focus": None})
    assert resp.status_code == 200
    assert "focus" not in resp.json()


def test_focus_composes_with_candidate_and_retune():
    # 聚焦与候选试加、微调频点互不干扰：同一请求可分别评估
    resp = client.post(
        "/api/analyze",
        json={
            "input": BOUNDARY_INPUT,
            "candidate": {"name": "X", "freq": "470.100"},
            "retune": "A",
            "focus": ["A"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["candidate"]["status"] == "safe"
    assert data["retune"]["name"] == "A"
    assert data["focus"]["direct_count"] == 1
    assert data["conflict_count"] == 4
