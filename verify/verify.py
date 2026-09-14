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
   重复/越界候选被拒绝且错误指向候选输入区、旧请求（不带候选）语义不变；
   候选仅作产物来源时受影响名单仍含候选自身、非对象候选值与井号开头
   名称均按候选输入区（行号 0）拒绝。
9. 微调频点：多个改善频点按（冲突总数，移动距离，频率升序）稳定排名且
   字段可复算、±500 kHz 与带缘边界候选参与计算、无改善返回空建议、
   未知微调频道指向微调区（行号 -1）且不影响既有结果、
   未携带微调参数的旧请求语义不变；微调建议带由频道名称、整数 kHz 与
   顺序确定的清单版本标识，可直接应用：未编辑清单可应用建议且冲突数按
   返回结果下降（只替换目标行，注释/空白保留），改名或改频后携带旧标识
   确定失败（提示重新查找建议），仅改注释/空白仍可应用，未被返回的频率
   遭拒绝（建议不可用），旧请求不出现 applied 字段。
10. 聚焦排查：直接影响（目标即重点频道）整体排在来源相关（仅来源组合
    含重点频道）之前且两类内部保持原有顺序、含多组来源的条目不拆分不
    重复、无相关冲突返回明确空结果、完整冲突分组与摘要不因聚焦改写、
    名称不存在/重复/超过三个/非列表值指向聚焦区（行号 -2）、
    未携带聚焦参数的旧请求语义不变。
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


def analyze(
    text: str,
    candidate: dict | None = None,
    retune: str | None = None,
    focus: list | None = None,
    apply: dict | None = None,
) -> httpx.Response:
    payload = {"input": text}
    if candidate is not None:
        payload["candidate"] = candidate
    if retune is not None:
        payload["retune"] = retune
    if focus is not None:
        payload["focus"] = focus
    if apply is not None:
        payload["apply"] = apply
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

    def t_candidate_source_only_affected_includes_candidate():
        # 候选仅作为产物来源命中既有频道（自身不被命中）时，
        # 受影响频道名单也必须包含候选自身
        resp = analyze("A 500.000\nB 500.100", {"name": "X", "freq": "500.050"})
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        cand = resp.json()["candidate"]
        expect(cand["status"] == "risky", "候选引入增量冲突应判 risky")
        expect(
            all(not c["target_is_candidate"] for c in cand["new_conflicts"]),
            "本场景候选自身不应被产物命中",
        )
        expect(
            cand["affected_channel_names"] == ["A", "B", "X"],
            f"受影响名单应同时包含候选与目标频道: {cand['affected_channel_names']}",
        )

    def t_non_object_candidate_points_to_candidate_area():
        # 非对象形式的候选值必须标记候选输入区（line=0），不得误报清单第 1 行
        resp = httpx.post(
            f"{API}/api/analyze",
            json={"input": "C1 500.000\nC2 510.000", "candidate": "X 500.000"},
            timeout=10,
        )
        expect(resp.status_code == 422, f"非对象候选应 422，实际 {resp.status_code}")
        errors = resp.json()["errors"]
        expect(
            errors and all(e["line"] == 0 for e in errors),
            f"非对象候选应指向候选输入区(line=0): {errors}",
        )

    def t_hash_prefix_candidate_name_rejected():
        # 井号开头名称写入正式清单会被当作注释，候选校验须与清单口径一致
        resp = analyze("C1 500.000\nC2 510.000", {"name": "#X", "freq": "520.000"})
        expect(resp.status_code == 422, f"井号开头候选名应 422，实际 {resp.status_code}")
        errors = resp.json()["errors"]
        expect(
            errors and all(e["line"] == 0 for e in errors),
            f"井号开头候选名应指向候选输入区(line=0): {errors}",
        )

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

    def t_retune_finds_and_stably_orders_improvements():
        resp = analyze("A 500.000\nB 500.100\nC 499.850\nD 500.250", retune="A")
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        # 顶层仍是当前清单的完整冲突分组，建议不写回清单
        expect(data["conflict_count"] == 4, "顶层冲突应保持基线 4 项")
        expect(
            [c["freq_khz"] for c in data["channels"]]
            == [500_000, 500_100, 499_850, 500_250],
            "微调建议不得写回清单",
        )
        ret = data.get("retune")
        expect(bool(ret), "携带微调参数时响应必须包含 retune 评估对象")
        expect(
            ret["name"] == "A" and ret["original_freq_khz"] == 500_000,
            f"微调目标不符: {ret}",
        )
        expect(ret["baseline_conflict_count"] == 4, "基线冲突数应为 4")
        suggestions = ret["suggestions"]
        expect(2 <= len(suggestions) <= 5, f"应找到多条（≤5）改善频点: {len(suggestions)}")
        got = [
            (s["freq_khz"], s["move_khz"], s["conflict_count"], s["reduced_count"])
            for s in suggestions
        ]
        expect(
            got
            == [
                (499_875, 125, 0, 4),
                (500_125, 125, 0, 4),  # 移动距离相同，按频率升序打破平局
                (499_825, 175, 0, 4),
                (499_800, 200, 0, 4),
                (499_775, 225, 0, 4),
            ],
            f"建议内容或稳定排序不符: {got}",
        )
        keys = [(s["conflict_count"], s["move_khz"], s["freq_khz"]) for s in suggestions]
        expect(keys == sorted(keys), "建议应按 冲突总数→移动距离→频率升序 稳定排名")
        # 字段自洽，且每条建议都可用接口独立复算验证
        for s in suggestions:
            expect(s["move_khz"] == abs(s["freq_khz"] - 500_000), "移动量应为 |新频点−原频点|")
            expect(
                s["reduced_count"] == 4 - s["conflict_count"],
                "减少数量应等于 基线冲突数 − 替换后冲突数",
            )
            expect(
                (s["freq_khz"] - 500_000) % 25 == 0
                and 499_500 <= s["freq_khz"] <= 500_500
                and s["freq_khz"] != 500_000,
                f"频点应落在 ±500 kHz、25 kHz 网格上且非原位: {s}",
            )
            replaced = "\n".join(
                f"A {s['freq_khz'] // 1000}.{s['freq_khz'] % 1000:03d}"
                if line.startswith("A ")
                else line
                for line in ["A 500.000", "B 500.100", "C 499.850", "D 500.250"]
            )
            again = analyze(replaced).json()
            expect(
                again["conflict_count"] == s["conflict_count"],
                f"替换后复算冲突数与建议不符: {s}",
            )

    def t_retune_edge_candidates_participate():
        # CH3 的 +500 kHz 边界候选（595.275）恰为改善方案：少枚举一步就会漏掉
        resp = analyze(
            "CH0 593.975\nCH1 594.550\nCH2 594.675\nCH3 594.775\nCH4 594.800\nCH5 595.450",
            retune="CH3",
        )
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        suggestions = resp.json()["retune"]["suggestions"]
        got = [(s["freq_khz"], s["move_khz"], s["conflict_count"]) for s in suggestions]
        expect(
            got
            == [
                (595_200, 425, 2),
                (595_225, 450, 2),
                (595_250, 475, 2),
                (595_275, 500, 2),  # 恰好 +500 kHz 的边界候选
            ],
            f"±500 kHz 边界候选应参与计算: {got}",
        )
        # 带缘 470.000 MHz 本身也可作为替换频点并排在首位
        resp = analyze("CH0 470.100\nCH1 470.400\nCH2 470.725\nCH3 471.300", retune="CH0")
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        suggestions = resp.json()["retune"]["suggestions"]
        expect(bool(suggestions), "带缘场景应给出建议")
        expect(
            suggestions[0]["freq_khz"] == 470_000
            and suggestions[0]["conflict_count"] == 0,
            f"带缘 470.000 应作为首选替换频点: {suggestions[0]}",
        )
        expect(
            all(470_000 <= s["freq_khz"] <= 694_000 for s in suggestions),
            "不得给出带外频点",
        )

    def t_retune_no_improvement_returns_empty():
        # E 与任何冲突无关：±500 kHz 内没有更优方案
        resp = analyze("A 500.000\nB 500.100\nC 499.850\nD 500.250\nE 600.000", retune="E")
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        ret = resp.json()["retune"]
        expect(ret["baseline_conflict_count"] == 4, "基线冲突数应为 4")
        expect(ret["suggestions"] == [], f"无改善时建议应为空: {ret['suggestions']}")
        # 安全清单同样不可能再改善
        resp = analyze("C1 500.000\nC2 510.000\nC3 530.000", retune="C1")
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        expect(resp.json()["retune"]["suggestions"] == [], "安全清单建议应为空")

    def t_retune_unknown_channel_points_to_retune_area():
        resp = analyze("A 500.000\nB 500.100\nC 499.850\nD 500.250", retune="ZZ")
        expect(resp.status_code == 422, f"未知微调频道应 422，实际 {resp.status_code}")
        data = resp.json()
        expect("conflicts" not in data, "微调频道非法时不得给出风险结果")
        errors = data["errors"]
        expect(
            errors and all(e["line"] == -1 for e in errors),
            f"微调错误须指向微调区(line=-1): {errors}",
        )
        expect("ZZ" in errors[0]["message"], "错误信息应包含所填名称")
        # 非字符串微调值同样指向微调区，不得误报清单第 1 行
        resp = httpx.post(
            f"{API}/api/analyze",
            json={"input": "A 500.000\nB 500.100", "retune": 123},
            timeout=10,
        )
        expect(resp.status_code == 422, f"非字符串微调值应 422，实际 {resp.status_code}")
        expect(
            all(e["line"] == -1 for e in resp.json()["errors"]),
            "非字符串微调值应指向微调区(line=-1)",
        )

    def t_retune_invalid_list_rejected_with_original_line_numbers():
        # 清单非法时仍按原行号整批拒绝，不评估微调、不给部分结果
        resp = analyze("CH1 500.0005\nCH2 469.000", retune="CH1")
        expect(resp.status_code == 422, f"应 422，实际 {resp.status_code}")
        data = resp.json()
        expect("conflicts" not in data, "不得给出部分风险结果")
        expect([e["line"] for e in data["errors"]] == [1, 2], "清单非法须按原行号整批拒绝")

    def t_legacy_request_without_retune_unchanged():
        # 未携带微调参数的请求：响应字段与旧版逐字段一致（无 retune 键）
        resp = analyze("A 500.000\nB 500.100\nC 499.850\nD 500.250")
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        expect("retune" not in data, "旧请求响应中不应出现 retune 字段")
        expect(
            set(data.keys())
            == {"channel_count", "channels", "conflict_count", "conflicts", "summary"},
            f"旧字段集合被改变: {set(data.keys())}",
        )

    BOUNDARY_TEXT = "A 500.000\nB 500.100\nC 499.850\nD 500.250"

    def _retune_version_and_suggestions(text: str, name: str):
        resp = analyze(text, retune=name)
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        ret = resp.json()["retune"]
        version = ret.get("manifest_version")
        expect(
            isinstance(version, str)
            and re.fullmatch(r"mv1-[0-9a-f]{64}", version) is not None,
            f"微调建议应带清单版本标识: {ret}",
        )
        return version, ret["suggestions"]

    def t_retune_response_carries_manifest_version():
        # 版本标识由频道名称、整数 kHz 与顺序确定：同清单稳定、等价小数写法不变
        v1, _ = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        v2, _ = _retune_version_and_suggestions(BOUNDARY_TEXT, "B")
        expect(v1 == v2, f"同清单版本应一致: {v1} != {v2}")
        v3, _ = _retune_version_and_suggestions(
            "A 500.000\nB 500.1\nC 499.850\nD 500.250", "A"
        )
        expect(v1 == v3, "500.1 与 500.100 同频，版本应一致")

    def t_apply_suggestion_unedited_list_conflict_count_drops():
        # 未编辑清单：可直接应用返回的建议，冲突数按返回结果下降，只替换目标行
        version, suggestions = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        expect(suggestions, "应存在改善建议")
        first = suggestions[0]
        expect(
            first["freq_khz"] == 499_875 and first["conflict_count"] == 0,
            f"首选建议不符: {first}",
        )
        resp = analyze(
            BOUNDARY_TEXT,
            apply={"name": "A", "freq_khz": first["freq_khz"], "version": version},
        )
        expect(resp.status_code == 200, f"应用建议应成功: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        applied = data.get("applied")
        expect(bool(applied), "应用成功响应必须包含 applied 对象")
        expected_text = "A 499.875\nB 500.100\nC 499.850\nD 500.250"
        expect(
            applied["applied_text"] == expected_text and applied["freq_khz"] == 499_875,
            f"只应替换目标行频率: {applied}",
        )
        expect(
            [c["freq_khz"] for c in data["channels"]] == [499_875, 500_100, 499_850, 500_250],
            "顶层 channels 应为替换后清单",
        )
        # 冲突数按返回结果下降（4 -> 0），完整分析与摘要均针对替换后清单
        expect(data["conflict_count"] == 0, f"冲突数应降为 0: {data['conflict_count']}")
        expect(data["conflicts"] == [], "替换后应零项冲突")
        expect("冲突总数：0" in data["summary"], "摘要应反映零冲突")
        # 顶层结果与 applied_text 重新解析分析自洽
        plain = analyze(expected_text).json()
        expect(
            data["channels"] == plain["channels"] and data["conflicts"] == plain["conflicts"],
            "应用结果应与新清单重算结果一致",
        )
        # 成功响应只多出 applied 字段，不夹带 retune/candidate/focus
        expect(
            set(data.keys())
            == {"channel_count", "channels", "conflict_count", "conflicts", "summary", "applied"},
            f"应用响应字段集合不符: {set(data.keys())}",
        )
        # 每条返回建议都应可应用且冲突数与建议一致
        for s in suggestions:
            r = analyze(
                BOUNDARY_TEXT,
                apply={"name": "A", "freq_khz": s["freq_khz"], "version": version},
            )
            expect(r.status_code == 200, f"建议 {s} 应可应用")
            expect(r.json()["conflict_count"] == s["conflict_count"], "冲突数应与建议一致")

    def t_apply_stale_version_after_rename_or_freq_change_fails():
        # 改名或改频（含调序）后携带旧版本标识：确定失败，提示重新查找建议
        version, _ = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        for changed in [
            "A2 500.000\nB 500.100\nC 499.850\nD 500.250",
            "A 500.001\nB 500.100\nC 499.850\nD 500.250",
            "B 500.100\nA 500.000\nC 499.850\nD 500.250",
        ]:
            resp = analyze(
                changed,
                apply={"name": "A", "freq_khz": 499_875, "version": version},
            )
            expect(resp.status_code == 422, f"旧标识应 422: {changed}")
            data = resp.json()
            expect("applied" not in data and "conflicts" not in data, "失败不得出现应用结果字段")
            errors = data["errors"]
            expect(
                [e["line"] for e in errors] == [-1],
                f"应用失败错误须指向微调区(line=-1): {errors}",
            )
            expect(
                errors[0]["message"] == "清单已变化，请重新查找建议",
                f"版本不符提示不符: {errors}",
            )

    def t_apply_still_works_after_comment_or_whitespace_only_edit():
        # 仅调整注释或空白而频道序列未变：旧版本标识仍可应用，注释/空白原样保留
        version, _ = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        edited = (
            "# 演出前排频表\n"
            "  A 500.000\n"
            "\n"
            "B   500.100\n"
            "C 499.850\n"
            "D 500.250\n"
        )
        resp = analyze(
            edited,
            apply={"name": "A", "freq_khz": 499_875, "version": version},
        )
        expect(resp.status_code == 200, f"注释/空白编辑后应仍可应用: HTTP {resp.status_code}")
        applied = resp.json()["applied"]
        expect(
            applied["applied_text"]
            == "# 演出前排频表\n  A 499.875\n\nB   500.100\nC 499.850\nD 500.250\n",
            f"注释与空白应原样保留、仅替换目标行: {applied['applied_text']!r}",
        )
        expect(resp.json()["conflict_count"] == 0, "冲突数应按返回结果下降")

    def t_apply_frequency_not_in_suggestion_set_rejected():
        # 未被返回的频率：占用频点 / 原位频率 / 非 25 kHz 网格 / 带外，均拒绝
        version, suggestions = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        returned = {s["freq_khz"] for s in suggestions}
        for bad_freq in [500_100, 500_000, 499_999, 469_975]:
            expect(bad_freq not in returned, f"{bad_freq} 不应在建议集中")
            resp = analyze(
                BOUNDARY_TEXT,
                apply={"name": "A", "freq_khz": bad_freq, "version": version},
            )
            expect(resp.status_code == 422, f"未返回频率 {bad_freq} 应 422")
            data = resp.json()
            expect("applied" not in data and "conflicts" not in data, "拒绝时不得出现应用结果字段")
            errors = data["errors"]
            expect([e["line"] for e in errors] == [-1], f"应指向微调区: {errors}")
            expect("建议不可用" in errors[0]["message"], f"应提示建议不可用: {errors}")

    def t_apply_malformed_request_points_to_retune_area():
        version, _ = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        cases = [
            "oops",
            123,
            [],
            {},
            {"name": "A", "freq_khz": 499_875},  # 缺 version
            {"name": "A", "version": version},  # 缺 freq_khz
            {"freq_khz": 499_875, "version": version},  # 缺 name
            {"name": "A", "freq_khz": "499875", "version": version},  # 频率非整数 kHz
            {"name": "A", "freq_khz": 499_875, "version": "garbage"},  # 版本形态非法
        ]
        for value in cases:
            resp = httpx.post(
                f"{API}/api/analyze",
                json={"input": BOUNDARY_TEXT, "apply": value},
                timeout=10,
            )
            expect(resp.status_code == 422, f"apply={value} 应 422")
            data = resp.json()
            expect("applied" not in data and "conflicts" not in data, "结构非法不得给结果")
            errors = data["errors"]
            expect(
                errors and all(e["line"] == -1 for e in errors),
                f"应用结构错误须指向微调区: {errors}",
            )

    def t_apply_invalid_list_rejected_with_original_line_numbers():
        version, _ = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        resp = analyze(
            "CH1 500.0005\nCH2 469.000",
            apply={"name": "A", "freq_khz": 499_875, "version": version},
        )
        expect(resp.status_code == 422, f"应 422，实际 {resp.status_code}")
        data = resp.json()
        expect("applied" not in data and "conflicts" not in data, "清单非法不得给应用结果")
        expect([e["line"] for e in data["errors"]] == [1, 2], "须保留原行号整批拒绝")

    def t_legacy_requests_never_contain_applied_field():
        # 普通分析 / 候选试加 / 仅查询微调建议 / 聚焦排查的响应均不出现 applied
        for payload in [
            {"input": BOUNDARY_TEXT},
            {"input": BOUNDARY_TEXT, "candidate": {"name": "X", "freq": "470.100"}},
            {"input": BOUNDARY_TEXT, "retune": "A"},
            {"input": BOUNDARY_TEXT, "focus": ["A"]},
        ]:
            resp = httpx.post(f"{API}/api/analyze", json=payload, timeout=10)
            expect(resp.status_code == 200, f"HTTP {resp.status_code}")
            expect("applied" not in resp.json(), f"旧请求不应出现 applied: {payload}")
        # 应用失败响应同样不得夹带 applied 字段
        version, _ = _retune_version_and_suggestions(BOUNDARY_TEXT, "A")
        failed = analyze(
            "A2 500.000\nB 500.100\nC 499.850\nD 500.250",
            apply={"name": "A", "freq_khz": 499_875, "version": version},
        )
        expect("applied" not in failed.json(), "失败响应不得出现 applied")

    def t_focus_direct_before_source_and_results_unchanged():
        # 重点频道 A：目标为 A 的条目（直接影响）整体排在仅来源含 A 的条目
        # （来源相关）之前；完整冲突分组与摘要不因聚焦而改写
        text = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
        resp = analyze(text, focus=["A"])
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        plain = analyze(text).json()
        expect(data["conflict_count"] == 4, "顶层冲突应保持完整 4 项")
        expect(data["conflicts"] == plain["conflicts"], "完整冲突分组不得因聚焦改写")
        expect(data["summary"] == plain["summary"], "可复制摘要不得因聚焦改写")
        focus = data.get("focus")
        expect(bool(focus), "携带聚焦参数时响应必须包含 focus 评估对象")
        expect(focus["names"] == ["A"], f"重点频道不符: {focus['names']}")
        expect(
            focus["direct_count"] == 1 and focus["source_count"] == 3,
            f"分级计数不符: {focus}",
        )
        got = [
            (e["relation"], e["target_name"], e["product_khz"]) for e in focus["entries"]
        ]
        expect(
            got
            == [
                ("direct", "A", 499_950),  # 目标即重点频道，整体在前
                ("source", "C", 499_900),  # 仅来源含 A，保持原有冲突顺序
                ("source", "B", 500_150),
                ("source", "D", 500_200),
            ],
            f"聚焦排序不符: {got}",
        )
        # 每个聚焦条目保留原目标、产物与全部来源
        entry = focus["entries"][0]
        expect(
            entry["target_freq_khz"] == 500_000
            and entry["diff_khz"] == 50
            and entry["sources"] == [["B", "D"]],
            f"聚焦条目字段不符: {entry}",
        )

    def t_focus_multi_source_entry_not_split_or_duplicated():
        text = "T 500.000\nA 500.100\nB 500.150\nC 500.200\nD 500.350"
        resp = analyze(text, focus=["T"])
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        focus = resp.json()["focus"]
        expect(
            focus["direct_count"] == 3 and focus["source_count"] == 4,
            f"分级计数不符: {focus}",
        )
        got = [
            (e["relation"], e["target_name"], e["product_khz"]) for e in focus["entries"]
        ]
        expect(
            got
            == [
                ("direct", "T", 499_950),
                ("direct", "T", 500_000),
                ("direct", "T", 500_050),
                ("source", "B", 500_200),
                ("source", "C", 500_200),
                ("source", "D", 500_300),
                ("source", "D", 500_400),
            ],
            f"聚焦排序不符: {got}",
        )
        # C 500200 含两组来源 [A,B] 与 [A,T]：只出现一次且列全来源
        hits = [
            e
            for e in focus["entries"]
            if (e["target_name"], e["product_khz"]) == ("C", 500_200)
        ]
        expect(len(hits) == 1, f"含多组来源的条目不得拆分或重复: {hits}")
        expect(
            hits[0]["sources"] == [["A", "B"], ["A", "T"]],
            f"来源组合应列全: {hits[0]['sources']}",
        )

    def t_focus_no_related_conflicts_returns_empty():
        # E 与任何冲突无关：聚焦返回明确空结果，完整结果保持原样
        resp = analyze(
            "A 500.000\nB 500.100\nC 499.850\nD 500.250\nE 600.000", focus=["E"]
        )
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        expect(data["conflict_count"] == 4, "完整冲突结果应保持 4 项")
        focus = data["focus"]
        expect(focus["names"] == ["E"], f"重点频道不符: {focus['names']}")
        expect(focus["entries"] == [], f"无相关冲突时聚焦条目应为空: {focus['entries']}")
        expect(
            focus["direct_count"] == 0 and focus["source_count"] == 0,
            "无相关冲突时分级计数应为 0",
        )

    def t_focus_invalid_selections_point_to_focus_area():
        text = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
        cases = [
            (["ZZ"], "不在当前清单"),  # 名称不存在
            (["A", "C", "A"], "重复"),  # 重复选择
            (["A", "B", "C", "D"], "最多选择 3 个"),  # 超过三个
            ([], "缺失"),  # 空列表
        ]
        for value, keyword in cases:
            resp = analyze(text, focus=value)
            expect(resp.status_code == 422, f"focus={value} 应 422，实际 {resp.status_code}")
            data = resp.json()
            expect("conflicts" not in data, "聚焦选择非法时不得给出风险结果")
            errors = data["errors"]
            expect(
                errors and all(e["line"] == -2 for e in errors),
                f"聚焦错误须指向聚焦区(line=-2): {errors}",
            )
            expect(keyword in errors[0]["message"], f"提示应包含「{keyword}」: {errors}")
        # 非列表形式的聚焦值同样指向聚焦区，不得误报清单第 1 行
        resp = httpx.post(
            f"{API}/api/analyze",
            json={"input": text, "focus": "A"},
            timeout=10,
        )
        expect(resp.status_code == 422, f"非列表聚焦值应 422，实际 {resp.status_code}")
        expect(
            all(e["line"] == -2 for e in resp.json()["errors"]),
            "非列表聚焦值应指向聚焦区(line=-2)",
        )

    def t_focus_invalid_list_rejected_with_original_line_numbers():
        # 清单非法时仍按原行号整批拒绝，不评估聚焦、不给部分结果
        resp = analyze("CH1 500.0005\nCH2 469.000", focus=["CH1"])
        expect(resp.status_code == 422, f"应 422，实际 {resp.status_code}")
        data = resp.json()
        expect("conflicts" not in data, "不得给出部分风险结果")
        expect([e["line"] for e in data["errors"]] == [1, 2], "清单非法须按原行号整批拒绝")

    def t_legacy_request_without_focus_unchanged():
        # 未携带聚焦参数的请求：响应字段与旧版逐字段一致（无 focus 键）
        resp = analyze("A 500.000\nB 500.100\nC 499.850\nD 500.250")
        expect(resp.status_code == 200, f"HTTP {resp.status_code}")
        data = resp.json()
        expect("focus" not in data, "旧请求响应中不应出现 focus 字段")
        expect(
            set(data.keys())
            == {"channel_count", "channels", "conflict_count", "conflicts", "summary"},
            f"旧字段集合被改变: {set(data.keys())}",
        )

    def t_web_page_served():
        resp = httpx.get(f"{WEB}/", timeout=10, follow_redirects=True)
        expect(resp.status_code == 200, f"Web HTTP {resp.status_code}")
        expect("无线话筒互调排查台" in resp.text, "首页应包含应用标题")
        # 候选试加与微调频点入口由 React 渲染，确认其已打进 JS bundle
        assets = re.findall(r'src="([^"]+\.js)"', resp.text)
        expect(bool(assets), f"首页应引用 JS bundle: {resp.text[:200]}")
        bundle = httpx.get(f"{WEB}{assets[0]}", timeout=10).text
        expect("试加频道" in bundle, "JS bundle 应包含候选试加按钮")
        expect("候选输入区" in bundle, "JS bundle 应包含候选错误定位提示")
        expect("查找微调频点" in bundle, "JS bundle 应包含微调频点按钮")
        expect("应用此频点" in bundle, "JS bundle 应包含应用建议按钮")
        expect("清单已变化，请重新查找建议" in bundle, "JS bundle 应包含清单过期提示")
        expect("建议不可用" in bundle, "JS bundle 应包含建议不可用提示")
        expect("范围内无改善" in bundle, "JS bundle 应包含微调无改善提示")
        expect("微调区" in bundle, "JS bundle 应包含微调错误定位提示")
        expect("聚焦排查" in bundle, "JS bundle 应包含聚焦排查按钮")
        expect("直接影响" in bundle, "JS bundle 应包含直接影响标签")
        expect("来源相关" in bundle, "JS bundle 应包含来源相关标签")
        expect("聚焦区" in bundle, "JS bundle 应包含聚焦错误定位提示")

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

    def t_web_proxy_retune():
        # 页面的微调频点查询同样经 /api 反代到真实接口
        resp = httpx.post(
            f"{WEB}/api/analyze",
            json={"input": "A 500.000\nB 500.100\nC 499.850\nD 500.250", "retune": "A"},
            timeout=10,
        )
        expect(resp.status_code == 200, f"经反代微调失败: HTTP {resp.status_code}")
        data = resp.json()
        expect(data["conflict_count"] == 4, "顶层结果应保持基线 4 项")
        ret = data.get("retune")
        expect(bool(ret) and ret["name"] == "A", "反代微调应返回 retune 评估对象")
        expect(
            len(ret["suggestions"]) == 5
            and ret["suggestions"][0]["freq_khz"] == 499_875,
            f"反代微调建议不符: {ret and ret['suggestions']}",
        )
        expect(
            isinstance(ret.get("manifest_version"), str),
            "反代微调建议应带清单版本标识",
        )

    def t_web_proxy_apply_retune():
        # 页面的「应用此频点」同样经 /api 反代到真实接口：
        # 先查建议取版本标识，再应用首条建议，冲突数按返回结果下降
        text = "A 500.000\nB 500.100\nC 499.850\nD 500.250"
        ret = httpx.post(
            f"{WEB}/api/analyze", json={"input": text, "retune": "A"}, timeout=10
        ).json()["retune"]
        version = ret["manifest_version"]
        first = ret["suggestions"][0]
        resp = httpx.post(
            f"{WEB}/api/analyze",
            json={
                "input": text,
                "apply": {
                    "name": "A",
                    "freq_khz": first["freq_khz"],
                    "version": version,
                },
            },
            timeout=10,
        )
        expect(resp.status_code == 200, f"经反代应用失败: HTTP {resp.status_code}")
        data = resp.json()
        expect(data["conflict_count"] == 0, "应用后冲突应降为 0")
        expect(
            data["applied"]["applied_text"]
            == "A 499.875\nB 500.100\nC 499.850\nD 500.250",
            "反代应用应只替换目标行频率",
        )

    def t_web_proxy_focus():
        # 页面的聚焦排查同样经 /api 反代到真实接口
        resp = httpx.post(
            f"{WEB}/api/analyze",
            json={
                "input": "A 500.000\nB 500.100\nC 499.850\nD 500.250",
                "focus": ["A"],
            },
            timeout=10,
        )
        expect(resp.status_code == 200, f"经反代聚焦失败: HTTP {resp.status_code}")
        data = resp.json()
        expect(data["conflict_count"] == 4, "顶层结果应保持完整 4 项")
        focus = data.get("focus")
        expect(bool(focus), "反代聚焦应返回 focus 评估对象")
        expect(
            focus["direct_count"] == 1 and focus["source_count"] == 3,
            f"反代聚焦分级计数不符: {focus and focus['names']}",
        )
        expect(
            [e["relation"] for e in focus["entries"]] == ["direct", "source", "source", "source"],
            "直接影响应整体排在来源相关之前",
        )

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
        ("候选试加：候选仅作来源时受影响名单仍含候选自身", t_candidate_source_only_affected_includes_candidate),
        ("候选试加：非对象候选值指向候选输入区", t_non_object_candidate_points_to_candidate_area),
        ("候选试加：井号开头名称与清单口径一致被拒", t_hash_prefix_candidate_name_rejected),
        ("候选试加：清单非法仍按原行号整批拒绝", t_invalid_list_rejected_even_with_candidate),
        ("不带候选的旧请求维持原字段语义", t_legacy_request_without_candidate_unchanged),
        ("微调频点：多个改善频点稳定排序且可复算", t_retune_finds_and_stably_orders_improvements),
        ("微调频点：±500 kHz 与带缘边界候选参与计算", t_retune_edge_candidates_participate),
        ("微调频点：无改善时返回空建议", t_retune_no_improvement_returns_empty),
        ("微调频点：未知/非法微调频道指向微调区", t_retune_unknown_channel_points_to_retune_area),
        ("微调频点：清单非法仍按原行号整批拒绝", t_retune_invalid_list_rejected_with_original_line_numbers),
        ("不带微调参数的旧请求维持原字段语义", t_legacy_request_without_retune_unchanged),
        ("应用建议：微调响应带由名称/整数kHz/顺序确定的清单版本标识", t_retune_response_carries_manifest_version),
        ("应用建议：未编辑清单可应用且冲突数按返回结果下降、只替换目标行", t_apply_suggestion_unedited_list_conflict_count_drops),
        ("应用建议：改名/改频/调序后携带旧版本标识确定失败", t_apply_stale_version_after_rename_or_freq_change_fails),
        ("应用建议：仅调整注释或空白而频道序列未变时仍可应用", t_apply_still_works_after_comment_or_whitespace_only_edit),
        ("应用建议：未被返回的频率遭拒绝且提示建议不可用", t_apply_frequency_not_in_suggestion_set_rejected),
        ("应用建议：结构错误指向微调区且不出现应用结果字段", t_apply_malformed_request_points_to_retune_area),
        ("应用建议：清单非法仍按原行号整批拒绝", t_apply_invalid_list_rejected_with_original_line_numbers),
        ("应用建议：普通/候选/微调/聚焦旧请求不出现 applied 字段", t_legacy_requests_never_contain_applied_field),
        ("聚焦排查：直接影响排在来源相关之前且完整结果不改写", t_focus_direct_before_source_and_results_unchanged),
        ("聚焦排查：含多组来源的条目不拆分不重复", t_focus_multi_source_entry_not_split_or_duplicated),
        ("聚焦排查：无相关冲突返回明确空结果", t_focus_no_related_conflicts_returns_empty),
        ("聚焦排查：不存在/重复/超三个/非列表指向聚焦区", t_focus_invalid_selections_point_to_focus_area),
        ("聚焦排查：清单非法仍按原行号整批拒绝", t_focus_invalid_list_rejected_with_original_line_numbers),
        ("不带聚焦参数的旧请求维持原字段语义", t_legacy_request_without_focus_unchanged),
        ("Web 页面可访问", t_web_page_served),
        ("Web /api 反代联调链路", t_web_proxy_to_api),
        ("Web /api 反代候选试加链路", t_web_proxy_candidate_trial),
        ("Web /api 反代微调频点链路", t_web_proxy_retune),
        ("Web /api 反代应用微调频点链路", t_web_proxy_apply_retune),
        ("Web /api 反代聚焦排查链路", t_web_proxy_focus),
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
