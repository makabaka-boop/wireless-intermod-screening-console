"""/api/analyze 候选试加的接口测试。

验收对应项：
- 安全候选无增量；风险候选为自身及既有频道引入冲突；
- 候选重复/格式越界返回 422 且错误指向候选输入区（line=0）；
- 清单非法仍按原行号整批拒绝；合并后超过 32 频道直接提示；
- 不带候选的旧请求维持原字段语义（响应中无 candidate 字段）。
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BOUNDARY_INPUT = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
SAFE_INPUT = "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000"


def test_legacy_request_without_candidate_keeps_semantics():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT})
    assert resp.status_code == 200
    data = resp.json()
    assert "candidate" not in data, "未传候选时响应不应出现 candidate 字段"
    assert data["conflict_count"] == 4
    assert set(data.keys()) == {
        "channel_count",
        "channels",
        "conflict_count",
        "conflicts",
        "summary",
    }


def test_explicit_null_candidate_treated_as_absent():
    resp = client.post("/api/analyze", json={"input": SAFE_INPUT, "candidate": None})
    assert resp.status_code == 200
    assert "candidate" not in resp.json()


def test_safe_candidate_no_increment():
    resp = client.post(
        "/api/analyze",
        json={"input": SAFE_INPUT, "candidate": {"name": "X", "freq": "470.100"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    # 顶层仍是基线完整结果
    assert data["conflict_count"] == 0
    cand = data["candidate"]
    assert cand["name"] == "X"
    assert cand["freq_khz"] == 470_100
    assert cand["status"] == "safe"
    assert cand["new_conflict_count"] == 0
    assert cand["new_conflicts"] == []
    assert cand["affected_channel_names"] == []
    assert cand["merged_channel_count"] == 6
    assert cand["baseline_conflict_count"] == 0
    assert cand["merged_conflict_count"] == 0


def test_risky_candidate_introduces_conflicts_to_itself_and_existing():
    resp = client.post(
        "/api/analyze",
        json={"input": BOUNDARY_INPUT, "candidate": {"name": "X", "freq": "499.900"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    # 顶层保留原有完整冲突分组（基线 4 项，不被合并结果覆盖）
    assert data["conflict_count"] == 4
    assert [c["target_name"] for c in data["conflicts"]] == ["C", "A", "B", "D"]

    cand = data["candidate"]
    assert cand["status"] == "risky"
    assert cand["baseline_conflict_count"] == 4
    assert cand["merged_conflict_count"] == 9
    assert cand["new_conflict_count"] == 6
    assert cand["merged_channel_count"] == 5
    assert cand["affected_channel_names"] == ["A", "B", "C", "D", "X"]

    new = cand["new_conflicts"]
    # 候选自身被既有产物命中（0 kHz 与 50 kHz 各一）
    self_hits = [c for c in new if c["target_is_candidate"]]
    assert {c["target_name"] for c in self_hits} == {"X"}
    assert {(c["product_khz"], c["diff_khz"]) for c in self_hits} == {
        (499_900, 0),
        (499_950, 50),
    }
    # 既有频道被候选参与的新产物命中
    existing_targets = {c["target_name"] for c in new if not c["target_is_candidate"]}
    assert existing_targets == {"A", "B", "C", "D"}

    # 既有目标+产物（A 499950，基线来源 [B,D]）新增含候选的来源 [C,X]
    a_row = next(c for c in new if c["target_name"] == "A" and c["product_khz"] == 499_950)
    assert a_row["sources"] == [["B", "D"], ["C", "X"]]
    assert a_row["new_sources"] == [["C", "X"]]

    # 新增冲突同样按 目标频率 → 产物频率 → 目标名称 排序
    keys = [(c["target_freq_khz"], c["product_khz"], c["target_name"]) for c in new]
    assert keys == sorted(keys)


def test_duplicate_candidate_name_rejected_at_candidate_area():
    resp = client.post(
        "/api/analyze",
        json={"input": BOUNDARY_INPUT, "candidate": {"name": "A", "freq": "520.000"}},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "conflicts" not in data
    assert data["errors"]
    assert all(e["line"] == 0 for e in data["errors"])
    assert "重复" in data["errors"][0]["message"]


def test_duplicate_candidate_frequency_rejected_at_candidate_area():
    resp = client.post(
        "/api/analyze",
        json={"input": BOUNDARY_INPUT, "candidate": {"name": "X", "freq": "500.1"}},
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert [e["line"] for e in errors] == [0]
    assert "重复" in errors[0]["message"]


def test_candidate_bad_format_and_range_point_to_candidate_area():
    resp = client.post(
        "/api/analyze",
        json={"input": BOUNDARY_INPUT, "candidate": {"name": "X", "freq": "500.1234"}},
    )
    assert resp.status_code == 422
    assert resp.json()["errors"][0] == {
        "line": 0,
        "message": "候选频率「500.1234」非法：需为最多三位小数的 MHz 数值，例如 500.125",
    }

    resp = client.post(
        "/api/analyze",
        json={"input": BOUNDARY_INPUT, "candidate": {"name": "X", "freq": "700"}},
    )
    errors = resp.json()["errors"]
    assert errors[0]["line"] == 0 and "超出允许范围" in errors[0]["message"]


def test_candidate_missing_fields_point_to_candidate_area():
    resp = client.post(
        "/api/analyze", json={"input": SAFE_INPUT, "candidate": {"name": "X"}}
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert all(e["line"] == 0 for e in errors)
    assert "候选频率缺失" in errors[0]["message"]


def test_invalid_list_still_rejected_with_original_line_numbers():
    # 即便候选本身合法，清单非法仍按原行号整批拒绝，不混入候选错误
    resp = client.post(
        "/api/analyze",
        json={"input": "CH1 500.0005\nCH2 469.000", "candidate": {"name": "X", "freq": "520.000"}},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "conflicts" not in data
    assert [e["line"] for e in data["errors"]] == [1, 2]


def test_merge_over_32_channels_rejected():
    text = "\n".join(f"CH{i} {500 + i * 5}.000" for i in range(32))
    resp = client.post(
        "/api/analyze",
        json={"input": text, "candidate": {"name": "X", "freq": "690.000"}},
    )
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert [e["line"] for e in errors] == [0]
    assert "上限 32" in errors[0]["message"]
