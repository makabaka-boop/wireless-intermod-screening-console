"""FastAPI 接口测试：提交、边界命中、错误行号、整批拒绝。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BOUNDARY_INPUT = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
SAFE_INPUT = "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000"


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_analyze_boundary_hits_exact_50khz():
    resp = client.post("/api/analyze", json={"input": BOUNDARY_INPUT})
    assert resp.status_code == 200
    data = resp.json()
    assert data["channel_count"] == 4
    assert data["conflict_count"] == 4
    assert [
        (c["target_name"], c["product_khz"], c["diff_khz"], c["sources"])
        for c in data["conflicts"]
    ] == [
        ("C", 499_900, 50, [["A", "B"]]),
        ("A", 499_950, 50, [["B", "D"]]),
        ("B", 500_150, 50, [["A", "C"]]),
        ("D", 500_200, 50, [["A", "B"]]),
    ]
    assert "差值 50 kHz" in data["summary"]
    assert "受影响 C（499.850 MHz）" in data["summary"]


def test_analyze_safe_list_zero_conflicts():
    resp = client.post("/api/analyze", json={"input": SAFE_INPUT})
    assert resp.status_code == 200
    data = resp.json()
    assert data["conflict_count"] == 0
    assert data["conflicts"] == []
    assert "零项冲突" in data["summary"]


def test_analyze_dedup_and_ordering():
    resp = client.post(
        "/api/analyze",
        json={"input": "T 500.000\nA 500.100\nB 500.150\nC 500.200\nD 500.350"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [
        (c["target_name"], c["product_khz"], c["sources"]) for c in data["conflicts"]
    ] == [
        ("T", 499_950, [["B", "D"]]),
        ("T", 500_000, [["A", "C"]]),
        ("T", 500_050, [["A", "B"], ["C", "D"]]),
        ("A", 500_050, [["C", "D"]]),
        ("A", 500_100, [["B", "C"]]),
        ("B", 500_200, [["A", "T"]]),
        ("C", 500_200, [["A", "B"], ["A", "T"]]),
        ("D", 500_300, [["A", "C"], ["B", "T"]]),
        ("D", 500_400, [["C", "T"]]),
    ]


def test_invalid_input_returns_422_with_line_numbers_and_no_partial_result():
    resp = client.post(
        "/api/analyze",
        json={"input": "CH1 500.0005\nCH1 510.000\nCH2 469.000"},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "conflicts" not in data, "整批输入有误时不得给出部分风险结果"
    errors = data["errors"]
    assert [e["line"] for e in errors] == [1, 2, 3]
    assert all(e["message"] for e in errors)


def test_single_channel_rejected():
    resp = client.post("/api/analyze", json={"input": "CH1 500.000"})
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["line"] == 1


def test_33_channels_rejected_at_line_33():
    text = "\n".join(f"CH{i} {470 + i}.000" for i in range(33))
    resp = client.post("/api/analyze", json={"input": text})
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert errors[0]["line"] == 33


def test_32_channels_accepted():
    text = "\n".join(f"CH{i} {500 + i * 5}.000" for i in range(32))
    resp = client.post("/api/analyze", json={"input": text})
    assert resp.status_code == 200
    assert resp.json()["channel_count"] == 32
