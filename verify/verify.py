"""一次性验收服务：对运行中的 web / api 执行端到端验收，全部通过则以 0 退出。

验收要点：
1. API 健康检查可用；
2. 保护带边界（恰好 50 kHz）被稳定命中，51 kHz 则安全；
3. 带外产物被剔除，带缘（470.000 / 694.000 MHz）产物保留；
4. 安全清单明确返回零项冲突；
5. 非法输入整批拒绝：422、全部错误带行号、无任何部分风险结果；
6. 相同产物+目标只展示一次且列全来源，结果按目标频率→产物频率→目标名称排序；
7. Web 页面可访问且通过 /api 反代到真实接口。
"""

from __future__ import annotations

import os
import sys
import time

import httpx

API = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
WEB = os.environ.get("WEB_BASE", "http://localhost:8080").rstrip("/")
TIMEOUT = float(os.environ.get("VERIFY_TIMEOUT", "60"))

failures: list[str] = []
passed = 0


def check(name: str, fn) -> None:
    global passed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 验收脚本需要汇总所有失败
        failures.append(f"{name}: {exc}")
        print(f"  ✘ {name}: {exc}")
    else:
        passed += 1
        print(f"  ✔ {name}")


def wait_ready() -> None:
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        try:
            if httpx.get(f"{API}/api/health", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"等待 {API} 就绪超时（{TIMEOUT}s）")


def analyze(text: str) -> httpx.Response:
    return httpx.post(f"{API}/api/analyze", json={"input": text}, timeout=10)


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    print(f"[verify] API={API} WEB={WEB}")
    wait_ready()
    print("[verify] API 已就绪，开始验收\n")

    def t_health():
        body = httpx.get(f"{API}/api/health", timeout=5).json()
        expect(body == {"status": "ok"}, f"健康检查返回异常: {body}")

    def t_boundary_50khz_hit():
        resp = analyze("A 500.000\nB 500.100\nC 499.850\nD 500.250")
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        got = [
            (c["target_name"], c["product_khz"], c["diff_khz"], c["sources"])
            for c in data["conflicts"]
        ]
        expect(
            got
            == [
                ("C", 499_900, 50, [["A", "B"]]),
                ("A", 499_950, 50, [["B", "D"]]),
                ("B", 500_150, 50, [["A", "C"]]),
                ("D", 500_200, 50, [["A", "B"]]),
            ],
            f"边界冲突不符: {got}",
        )
        expect(data["conflict_count"] == 4, "conflict_count 应为 4")
        expect("差值 50 kHz" in data["summary"], "摘要应包含差值 50 kHz")

    def t_51khz_safe():
        resp = analyze("A 500.000\nB 500.100\nC 499.849\nD 500.251")
        data = resp.json()
        expect(data["conflict_count"] == 0, f"51 kHz 不应记冲突: {data['conflicts']}")

    def t_band_edges():
        low = analyze("A 470.100\nB 470.200\nC 470.050").json()
        expect(
            any(c["product_khz"] == 470_000 for c in low["conflicts"]),
            "470.000 MHz 带缘产物应保留",
        )
        high = analyze("A 693.800\nB 693.900\nC 693.950").json()
        expect(
            any(c["product_khz"] == 694_000 for c in high["conflicts"]),
            "694.000 MHz 带缘产物应保留",
        )
        # 产物 469.980 MHz 距目标仅 20 kHz，但在频段外，必须剔除
        out = analyze("A 470.020\nB 470.060\nC 470.000").json()
        expect(
            all(c["product_khz"] >= 470_000 for c in out["conflicts"]),
            f"带外产物应剔除: {out['conflicts']}",
        )

    def t_safe_list_zero():
        resp = analyze("C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000")
        data = resp.json()
        expect(data["conflict_count"] == 0 and data["conflicts"] == [], "安全清单应为零项冲突")
        expect("零项冲突" in data["summary"], "摘要应明确零项冲突")

    def t_invalid_rejected_with_line_numbers():
        resp = analyze("CH1 500.0005\nCH1 510.000\nCH2 469.000")
        expect(resp.status_code == 422, f"应返回 422，实际 {resp.status_code}")
        data = resp.json()
        expect("conflicts" not in data, "整批输入有误时不得给出部分风险结果")
        errors = data["errors"]
        expect([e["line"] for e in errors] == [1, 2, 3], f"错误行号不符: {errors}")
        expect(all(isinstance(e["line"], int) and e["line"] >= 1 for e in errors), "行号必须 ≥1")

    def t_dedup_and_ordering():
        resp = analyze("T 500.000\nA 500.100\nB 500.150\nC 500.200\nD 500.350")
        got = [
            (c["target_name"], c["product_khz"], c["sources"])
            for c in resp.json()["conflicts"]
        ]
        expect(
            got
            == [
                ("T", 499_950, [["B", "D"]]),
                ("T", 500_000, [["A", "C"]]),
                ("T", 500_050, [["A", "B"], ["C", "D"]]),
                ("A", 500_050, [["C", "D"]]),
                ("A", 500_100, [["B", "C"]]),
                ("B", 500_200, [["A", "T"]]),
                ("C", 500_200, [["A", "B"], ["A", "T"]]),
                ("D", 500_300, [["A", "C"], ["B", "T"]]),
                ("D", 500_400, [["C", "T"]]),
            ],
            f"去重/排序结果不符: {got}",
        )

    def t_web_page_served():
        resp = httpx.get(f"{WEB}/", timeout=10, follow_redirects=True)
        expect(resp.status_code == 200, f"Web HTTP {resp.status_code}")
        expect("无线话筒互调排查台" in resp.text, "首页应包含应用标题")

    def t_web_proxy_to_api():
        # 通过 web 容器的 /api 反代提交，验证联调链路真实可用
        resp = httpx.post(
            f"{WEB}/api/analyze",
            json={"input": "A 500.000\nB 500.100\nC 499.850\nD 500.250"},
            timeout=10,
        )
        expect(resp.status_code == 200, f"经 web 反代提交失败: HTTP {resp.status_code}")
        expect(resp.json()["conflict_count"] == 4, "经 web 反代应命中 4 项边界冲突")

    checks = [
        ("API 健康检查", t_health),
        ("保护带边界：恰好 50 kHz 稳定命中", t_boundary_50khz_hit),
        ("保护带之外：51 kHz 安全", t_51khz_safe),
        ("频段边缘产物保留、带外产物剔除", t_band_edges),
        ("安全清单零项冲突", t_safe_list_zero),
        ("非法输入整批拒绝且错误带行号", t_invalid_rejected_with_line_numbers),
        ("相同产物+目标去重并列全来源、结果排序", t_dedup_and_ordering),
        ("Web 页面可访问", t_web_page_served),
        ("Web /api 反代联调链路", t_web_proxy_to_api),
    ]
    for name, fn in checks:
        check(name, fn)

    print(f"\n[verify] 通过 {passed}/{len(checks)} 项")
    if failures:
        print("[verify] 验收失败：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("[verify] 全部验收通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
