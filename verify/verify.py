"""一次性验收服务：对运行中的 web / api 执行端到端验收，全部通过则以 0 退出。

验收要点：
1. API 健康检查可用；
2. 保护带边界（恰好 50 kHz）被稳定命中，51 kHz 则安全；
3. 带外产物被剔除，带缘（470.000 / 694.000 MHz）产物保留；
4. 安全清单明确返回零项冲突；
5. 非法输入整批拒绝：422、全部错误带行号、无任何部分风险结果；
6. 相同产物+目标只展示一次且列全来源，结果按目标频率→产物频率→目标名称排序；
7. Web 页面可访问且通过 /api 反代到真实接口；
8. 彩排候选试加：安全候选无增量、风险候选为自身及既有频道引入冲突、
   重复/越界候选被拒绝且错误指向候选输入区、旧请求（不带候选）语义不变。
"""

from __future__ import annotations

import os
import re
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


def analyze(text: str, candidate: dict | None = None) -> httpx.Response:
    payload = {"input": text}
    if candidate is not None:
        payload["candidate"] = candidate
    return httpx.post(f"{API}/api/analyze", json=payload, timeout=10)


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

    def t_missing_content_line1_hint():
        resp = httpx.post(f"{API}/api/analyze", json={}, timeout=10)
        expect(resp.status_code == 422, f"缺失 input 字段应返回 422，实际 {resp.status_code}")
        data = resp.json()
        expect("conflicts" not in data, "不得给出部分风险结果")
        errors = data.get("errors")
        expect(bool(errors) and errors[0]["line"] == 1, f"应给出第 1 行提示: {data}")

    def t_dotted_and_long_names_accepted():
        long_name = "无线话筒." + "甲" * 40
        resp = analyze(f"Mic.01 500.000\n{long_name} 510.000")
        expect(resp.status_code == 200, f"带点号/超长名称不应被拒: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        expect(data["channel_count"] == 2, "应接受 2 个合法唯一频道")
        expect(data["channels"][1]["name"] == long_name, "超长名称应原样保留")

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

    def t_candidate_safe_no_increment():
        # 470.100 距安全清单的任何互调产物都超出保护带：试加无增量
        resp = analyze(
            "C1 500.000\nC2 510.000\nC3 530.000\nC4 580.000\nC5 600.000",
            {"name": "X", "freq": "470.100"},
        )
        expect(resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        # 顶层仍是基线结果，未传候选时的旧字段语义不变
        expect(data["conflict_count"] == 0, "安全候选下顶层冲突仍应为 0")
        cand = data.get("candidate")
        expect(bool(cand), "携带候选时响应必须包含 candidate 评估对象")
        expect(cand["status"] == "safe", f"候选应判 safe: {cand['status']}")
        expect(cand["new_conflict_count"] == 0, "安全候选不应有增量冲突")
        expect(cand["new_conflicts"] == [], "增量冲突列表应为空")
        expect(cand["affected_channel_names"] == [], "不应有受影响频道")
        expect(cand["merged_channel_count"] == 6, "合并后频道数应为 6")
        expect(cand["merged_conflict_count"] == 0, "合并后冲突数应为 0")
        expect(cand["freq_khz"] == 470_100, "候选频率应精确换算为整数 kHz")

    def t_candidate_risky_hits_itself_and_existing():
        # X=499.900：既有产物 2A−B=499.900 恰好落在候选上，
        # 同时候选参与的新产物命中 A/B/C/D 四个既有频道
        resp = analyze(
            "A 500.000\nB 500.100\nC 499.850\nD 500.250",
            {"name": "X", "freq": "499.900"},
        )
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        # 原有完整冲突分组原样保留
        expect(data["conflict_count"] == 4, f"基线冲突应为 4，实际 {data['conflict_count']}")
        expect(
            [c["target_name"] for c in data["conflicts"]] == ["C", "A", "B", "D"],
            "顶层冲突分组必须保持基线完整结果",
        )
        cand = data["candidate"]
        expect(cand["status"] == "risky", "候选应判 risky")
        expect(
            cand["baseline_conflict_count"] == 4
            and cand["merged_conflict_count"] == 9
            and cand["new_conflict_count"] == 6,
            f"冲突计数不符: {cand}",
        )
        expect(
            cand["affected_channel_names"] == ["A", "B", "C", "D", "X"],
            f"受影响频道应同时含候选自身与既有频道: {cand['affected_channel_names']}",
        )
        new = cand["new_conflicts"]
        self_hits = [c for c in new if c["target_is_candidate"]]
        expect({c["target_name"] for c in self_hits} == {"X"}, "候选自身应被既有产物命中")
        expect(
            {(c["product_khz"], c["diff_khz"]) for c in self_hits}
            == {(499_900, 0), (499_950, 50)},
            f"候选自身命中项不符: {self_hits}",
        )
        existing = {c["target_name"] for c in new if not c["target_is_candidate"]}
        expect(existing == {"A", "B", "C", "D"}, f"既有受影响频道不符: {existing}")
        # 既有目标+产物（A 499950，基线来源 B+D）新增含候选的来源 C+X
        a_row = next(
            c for c in new if c["target_name"] == "A" and c["product_khz"] == 499_950
        )
        expect(
            a_row["sources"] == [["B", "D"], ["C", "X"]],
            f"合并来源应列全: {a_row['sources']}",
        )
        expect(a_row["new_sources"] == [["C", "X"]], f"新增来源应只有 C+X: {a_row}")
        # 增量冲突沿用 目标频率→产物频率→目标名称 排序
        keys = [(c["target_freq_khz"], c["product_khz"], c["target_name"]) for c in new]
        expect(keys == sorted(keys), f"增量冲突排序不符: {keys}")

    def t_candidate_duplicate_and_malformed_rejected():
        # 重复名称
        resp = analyze(
            "A 500.000\nB 500.100\nC 499.850\nD 500.250",
            {"name": "A", "freq": "520.000"},
        )
        expect(resp.status_code == 422, f"重复候选名应 422，实际 {resp.status_code}")
        errors = resp.json()["errors"]
        expect(errors and all(e["line"] == 0 for e in errors), f"候选错误须指向候选区(line=0): {errors}")
        expect("重复" in errors[0]["message"], "错误信息应明确重复")

        # 重复频率（500.1 与 500.100 同频）
        resp = analyze(
            "A 500.000\nB 500.100\nC 499.850\nD 500.250",
            {"name": "X", "freq": "500.1"},
        )
        errors = resp.json()["errors"]
        expect(resp.status_code == 422 and errors[0]["line"] == 0, "重复频率应在候选区报错")

        # 格式越界
        for bad_freq in ["500.1234", "700", "469.999"]:
            resp = analyze(
                "C1 500.000\nC2 510.000", {"name": "X", "freq": bad_freq}
            )
            expect(resp.status_code == 422, f"候选频率 {bad_freq} 应 422")
            errors = resp.json()["errors"]
            expect(errors[0]["line"] == 0, f"{bad_freq} 的错误应指向候选区: {errors}")

    def t_candidate_merge_over_32_rejected():
        text = "\n".join(f"CH{i} {500 + i * 5}.000" for i in range(32))
        resp = analyze(text, {"name": "X", "freq": "690.000"})
        expect(resp.status_code == 422, f"合并超 32 频道应 422，实际 {resp.status_code}")
        errors = resp.json()["errors"]
        expect(errors[0]["line"] == 0 and "上限 32" in errors[0]["message"], f"提示不符: {errors}")

    def t_invalid_list_rejected_even_with_candidate():
        # 清单非法时仍按原行号整批拒绝，不评估候选、不给部分结果
        resp = analyze("CH1 500.0005\nCH2 469.000", {"name": "X", "freq": "520.000"})
        expect(resp.status_code == 422, f"应 422，实际 {resp.status_code}")
        data = resp.json()
        expect("conflicts" not in data, "不得给出部分风险结果")
        expect([e["line"] for e in data["errors"]] == [1, 2], "须保留原行号整批拒绝")

    def t_legacy_request_without_candidate_unchanged():
        # 不带候选的旧请求：响应字段与旧版逐字段一致（无 candidate 键）
        resp = httpx.post(
            f"{API}/api/analyze",
            json={"input": "A 500.000\nB 500.100\nC 499.850\nD 500.250"},
            timeout=10,
        )
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        expect("candidate" not in data, "旧请求响应中不应出现 candidate 字段")
        expect(
            set(data.keys())
            == {"channel_count", "channels", "conflict_count", "conflicts", "summary"},
            f"旧字段集合被改变: {set(data.keys())}",
        )

    def t_web_page_served():
        resp = httpx.get(f"{WEB}/", timeout=10, follow_redirects=True)
        expect(resp.status_code == 200, f"Web HTTP {resp.status_code}")
        expect("无线话筒互调排查台" in resp.text, "首页应包含应用标题")
        # 候选试加入口由 React 渲染，确认其已打进 JS bundle
        assets = re.findall(r'src="([^"]+\.js)"', resp.text)
        expect(bool(assets), f"首页应引用 JS bundle: {resp.text[:200]}")
        bundle = httpx.get(f"{WEB}{assets[0]}", timeout=10).text
        expect("试加频道" in bundle, "JS bundle 应包含候选试加按钮")
        expect("候选输入区" in bundle, "JS bundle 应包含候选错误定位提示")

    def t_web_proxy_to_api():
        # 通过 web 容器的 /api 反代提交，验证联调链路真实可用
        resp = httpx.post(
            f"{WEB}/api/analyze",
            json={"input": "A 500.000\nB 500.100\nC 499.850\nD 500.250"},
            timeout=10,
        )
        expect(resp.status_code == 200, f"经 web 反代提交失败: HTTP {resp.status_code}")
        expect(resp.json()["conflict_count"] == 4, "经 web 反代应命中 4 项边界冲突")

    def t_web_proxy_candidate_trial():
        # 页面的候选试加同样经 /api 反代到真实接口
        resp = httpx.post(
            f"{WEB}/api/analyze",
            json={
                "input": "A 500.000\nB 500.100\nC 499.850\nD 500.250",
                "candidate": {"name": "X", "freq": "499.900"},
            },
            timeout=10,
        )
        expect(resp.status_code == 200, f"经反代试加失败: HTTP {resp.status_code}")
        data = resp.json()
        expect(data["conflict_count"] == 4, "顶层结果应保持基线 4 项")
        cand = data.get("candidate")
        expect(bool(cand) and cand["status"] == "risky", "反代候选评估应为 risky")
        expect(cand["new_conflict_count"] == 6, f"应新增 6 项: {cand and cand['new_conflict_count']}")

    checks = [
        ("API 健康检查", t_health),
        ("保护带边界：恰好 50 kHz 稳定命中", t_boundary_50khz_hit),
        ("保护带之外：51 kHz 安全", t_51khz_safe),
        ("频段边缘产物保留、带外产物剔除", t_band_edges),
        ("安全清单零项冲突", t_safe_list_zero),
        ("非法输入整批拒绝且错误带行号", t_invalid_rejected_with_line_numbers),
        ("提交内容缺失返回第 1 行提示", t_missing_content_line1_hint),
        ("带点号/超长名称的合法频道可排查", t_dotted_and_long_names_accepted),
        ("相同产物+目标去重并列全来源、结果排序", t_dedup_and_ordering),
        ("候选试加：安全候选无增量", t_candidate_safe_no_increment),
        ("候选试加：风险候选命中自身与既有频道", t_candidate_risky_hits_itself_and_existing),
        ("候选试加：重复名称/频率与格式越界被拒（指向候选区）", t_candidate_duplicate_and_malformed_rejected),
        ("候选试加：合并后超过 32 频道直接提示", t_candidate_merge_over_32_rejected),
        ("候选试加：清单非法仍按原行号整批拒绝", t_invalid_list_rejected_even_with_candidate),
        ("不带候选的旧请求维持原字段语义", t_legacy_request_without_candidate_unchanged),
        ("Web 页面可访问", t_web_page_served),
        ("Web /api 反代联调链路", t_web_proxy_to_api),
        ("Web /api 反代候选试加链路", t_web_proxy_candidate_trial),
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
