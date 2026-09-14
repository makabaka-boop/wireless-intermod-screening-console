"""/api/analyze 微调频点的接口测试。

验收对应项：
- 携带 retune 时返回微调建议（替换频率、移动量、替换后冲突数、减少数量），
  顶层仍是当前清单的完整冲突分组；
- 多个改善频点按（冲突总数，移动距离，频率升序）稳定排名，最多 5 条；
- ±500 kHz 边界候选参与计算；无改善时 suggestions 为空；
- 未知/非法微调频道返回 422 且错误指向微调区（line=-1）；
- 清单非法仍按原行号整批拒绝；未携带微调参数的旧请求维持原字段语义。
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BOUNDARY_INPUT = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
SAFE_INPUT = "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000"
EDGE_INPUT = "CH0 593.975\nCH1 594.550\nCH2 594.675\nCH3 594.775\nCH4 594.800\nCH5 595.450"


def test_retune_returns_ranked_suggestions_and_keeps_baseline_conflicts():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "retune": "A"})
    assert resp.status_code == 200
    data = resp.json()
    # 顶层仍是当前清单的完整冲突分组，建议不写回清单
    assert data["conflict_count"] == 4
    assert [c["target_name"] for c in data["conflicts"]] == ["C", "A", "B", "D"]
    assert [c["freq_khz"] for c in data["channels"]] == [500_000, 500_100, 499_850, 500_250]

    retune = data["retune"]
    assert retune["name"] == "A"
    assert retune["original_freq_khz"] == 500_000
    assert retune["baseline_conflict_count"] == 4
    got = [
        (s["freq_khz"], s["move_khz"], s["conflict_count"], s["reduced_count"])
        for s in retune["suggestions"]
    ]
    assert got == [
        (499_875, 125, 0, 4),
        (500_125, 125, 0, 4),
        (499_825, 175, 0, 4),
        (499_800, 200, 0, 4),
        (499_775, 225, 0, 4),
    ]
    keys = [(s["conflict_count"], s["move_khz"], s["freq_khz"]) for s in retune["suggestions"]]
    assert keys == sorted(keys), "建议应按 冲突总数→移动距离→频率升序 稳定排名"


def test_retune_suggestions_cross_checked_by_reanalysis():
    # 用接口独立复算每条建议：替换后的清单冲突数必须与建议值一致
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "retune": "A"})
    suggestions = resp.json()["retune"]["suggestions"]
    assert suggestions
    for s in suggestions:
        replaced = "\n".join(
            f"A {s['freq_khz'] // 1000}.{s['freq_khz'] % 1000:03d}"
            if line.startswith("A ")
            else line
            for line in BOUNDARY_INPUT.splitlines()
        )
        again = client.post("/api/analyze", json={"input": replaced})
        assert again.json()["conflict_count"] == s["conflict_count"]


def test_retune_plus_500khz_edge_candidate_participates():
    resp = client.post("/api/analyze", json={"input": EDGE_INPUT, "retune": "CH3"})
    assert resp.status_code == 200
    suggestions = resp.json()["retune"]["suggestions"]
    assert [(s["freq_khz"], s["move_khz"]) for s in suggestions] == [
        (595_200, 425),
        (595_225, 450),
        (595_250, 475),
        (595_275, 500),  # 恰好 +500 kHz 的边界候选仍在建议中
    ]


def test_retune_no_improvement_returns_empty_suggestions():
    resp = client.post(
        "/api/analyze", json={"input": BOUNDARY_INPUT + "\nE 600.000", "retune": "E"}
    )
    assert resp.status_code == 200
    retune = resp.json()["retune"]
    assert retune["baseline_conflict_count"] == 4
    assert retune["suggestions"] == []

    # 安全清单同样不可能再改善
    resp = client.post("/api/analyze", json={"input": SAFE_INPUT, "retune": "C1"})
    assert resp.status_code == 200
    assert resp.json()["retune"]["suggestions"] == []


def test_retune_unknown_channel_points_to_retune_area():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "retune": "ZZ"})
    assert resp.status_code == 422
    data = resp.json()
    assert "conflicts" not in data
    errors = data["errors"]
    assert [e["line"] for e in errors] == [-1]
    assert "ZZ" in errors[0]["message"] and "不在当前清单" in errors[0]["message"]


def test_retune_missing_or_non_string_points_to_retune_area():
    for bad in ["", "   ", 123, ["A"], {"name": "A"}, True]:
        resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT, "retune": bad})
        assert resp.status_code == 422, f"retune={bad!r} 应返回 422"
        errors = resp.json()["errors"]
        assert [e["line"] for e in errors] == [-1]
        assert "微调" in errors[0]["message"]


def test_retune_invalid_list_rejected_with_original_line_numbers():
    # 清单非法时仍按原行号整批拒绝，不评估微调、不给部分结果
    resp = client.post(
        "/api/analyze",
        json={"input": "CH1 500.0005\nCH2 469.000", "retune": "CH1"},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "conflicts" not in data
    assert [e["line"] for e in data["errors"]] == [1, 2]


def test_request_without_retune_keeps_legacy_schema():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT})
    assert resp.status_code == 200
    data = resp.json()
    assert "retune" not in data, "未传微调参数时响应不应出现 retune 字段"
    assert set(data.keys()) == {
        "channel_count",
        "channels",
        "conflict_count",
        "conflicts",
        "summary",
    }


def test_explicit_null_retune_treated_as_absent():
    resp = client.post("/api/analyze", json={"input": SAFE_INPUT, "retune": None})
    assert resp.status_code == 200
    assert "retune" not in resp.json()


def test_retune_composes_with_candidate_request():
    # 候选与微调参数互不干扰：同一请求可分别评估
    resp = client.post(
        "/api/analyze",
        json={
            "input": BOUNDARY_INPUT,
            "candidate": {"name": "X", "freq": "470.100"},
            "retune": "A",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["candidate"]["status"] == "safe"
    assert data["retune"]["name"] == "A"
    assert data["conflict_count"] == 4
