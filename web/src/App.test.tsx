import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { AnalyzeResponse, CandidateOut, FocusOut, RetuneOut } from "./types";

const CONFLICT_RESPONSE: AnalyzeResponse = {
  channel_count: 4,
  channels: [
    { name: "A", freq_khz: 500_000, line: 2 },
    { name: "B", freq_khz: 500_100, line: 3 },
    { name: "C", freq_khz: 499_850, line: 4 },
    { name: "D", freq_khz: 500_250, line: 5 },
  ],
  conflict_count: 4,
  conflicts: [
    {
      target_name: "C",
      target_freq_khz: 499_850,
      product_khz: 499_900,
      diff_khz: 50,
      sources: [["A", "B"]],
    },
    {
      target_name: "A",
      target_freq_khz: 500_000,
      product_khz: 499_950,
      diff_khz: 50,
      sources: [["B", "D"]],
    },
    {
      target_name: "B",
      target_freq_khz: 500_100,
      product_khz: 500_150,
      diff_khz: 50,
      sources: [["A", "C"]],
    },
    {
      target_name: "D",
      target_freq_khz: 500_250,
      product_khz: 500_200,
      diff_khz: 50,
      sources: [["A", "B"]],
    },
  ],
  summary:
    "无线话筒三阶互调排查摘要\n冲突总数：4\n[1] 受影响 C（499.850 MHz）← 产物 499.900 MHz，差值 50 kHz，来源：A + B",
};

const SAFE_RESPONSE: AnalyzeResponse = {
  channel_count: 2,
  channels: [
    { name: "C1", freq_khz: 500_000, line: 1 },
    { name: "C2", freq_khz: 510_000, line: 2 },
  ],
  conflict_count: 0,
  conflicts: [],
  summary: "无线话筒三阶互调排查摘要\n冲突总数：0\n结论：安全清单，零项冲突。",
};

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function submitForm(input: string) {
  const textarea = screen.getByLabelText(/频道清单/);
  fireEvent.change(textarea, { target: { value: input } });
  fireEvent.click(screen.getByRole("button", { name: /提交排查/ }));
}

const RISKY_CANDIDATE: CandidateOut = {
  name: "X",
  freq_khz: 499_900,
  status: "risky",
  merged_channel_count: 5,
  baseline_conflict_count: 4,
  merged_conflict_count: 9,
  new_conflict_count: 6,
  affected_channel_names: ["A", "B", "C", "D", "X"],
  new_conflicts: [
    {
      target_name: "C",
      target_freq_khz: 499_850,
      product_khz: 499_800,
      diff_khz: 50,
      sources: [["A", "X"]],
      new_sources: [["A", "X"]],
      target_is_candidate: false,
    },
    {
      target_name: "X",
      target_freq_khz: 499_900,
      product_khz: 499_900,
      diff_khz: 0,
      sources: [["A", "B"]],
      new_sources: [["A", "B"]],
      target_is_candidate: true,
    },
    {
      target_name: "A",
      target_freq_khz: 500_000,
      product_khz: 499_950,
      diff_khz: 50,
      sources: [["B", "D"], ["C", "X"]],
      new_sources: [["C", "X"]],
      target_is_candidate: false,
    },
  ],
};

const RISKY_RESPONSE: AnalyzeResponse = {
  ...CONFLICT_RESPONSE,
  candidate: RISKY_CANDIDATE,
};

const SAFE_CANDIDATE: CandidateOut = {
  name: "X",
  freq_khz: 470_100,
  status: "safe",
  merged_channel_count: 6,
  baseline_conflict_count: 0,
  merged_conflict_count: 0,
  new_conflict_count: 0,
  affected_channel_names: [],
  new_conflicts: [],
};

function fillCandidate(name: string, freq: string) {
  fireEvent.change(screen.getByLabelText(/候选名称/), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/候选频率/), { target: { value: freq } });
}

const RETUNE_RESULT: RetuneOut = {
  name: "A",
  original_freq_khz: 500_000,
  baseline_conflict_count: 4,
  suggestions: [
    { freq_khz: 499_875, move_khz: 125, conflict_count: 0, reduced_count: 4 },
    { freq_khz: 500_125, move_khz: 125, conflict_count: 0, reduced_count: 4 },
    { freq_khz: 499_825, move_khz: 175, conflict_count: 0, reduced_count: 4 },
  ],
};

const RETUNE_RESPONSE: AnalyzeResponse = {
  ...CONFLICT_RESPONSE,
  retune: RETUNE_RESULT,
};

const RETUNE_EMPTY_RESPONSE: AnalyzeResponse = {
  ...CONFLICT_RESPONSE,
  retune: {
    name: "A",
    original_freq_khz: 500_000,
    baseline_conflict_count: 4,
    suggestions: [],
  },
};

function selectRetuneTarget(name: string) {
  fireEvent.change(screen.getByLabelText("微调频道"), { target: { value: name } });
}

const FOCUS_RESULT: FocusOut = {
  names: ["A"],
  direct_count: 1,
  source_count: 3,
  entries: [
    {
      relation: "direct",
      target_name: "A",
      target_freq_khz: 500_000,
      product_khz: 499_950,
      diff_khz: 50,
      sources: [["B", "D"]],
    },
    {
      relation: "source",
      target_name: "C",
      target_freq_khz: 499_850,
      product_khz: 499_900,
      diff_khz: 50,
      sources: [["A", "B"]],
    },
    {
      relation: "source",
      target_name: "B",
      target_freq_khz: 500_100,
      product_khz: 500_150,
      diff_khz: 50,
      sources: [["A", "C"]],
    },
    {
      relation: "source",
      target_name: "D",
      target_freq_khz: 500_250,
      product_khz: 500_200,
      diff_khz: 50,
      sources: [["A", "B"]],
    },
  ],
};

const FOCUS_RESPONSE: AnalyzeResponse = {
  ...CONFLICT_RESPONSE,
  focus: FOCUS_RESULT,
};

const CONFLICT_WITH_E_RESPONSE: AnalyzeResponse = {
  ...CONFLICT_RESPONSE,
  channel_count: 5,
  channels: [...CONFLICT_RESPONSE.channels, { name: "E", freq_khz: 600_000, line: 6 }],
};

const FOCUS_EMPTY_RESPONSE: AnalyzeResponse = {
  ...CONFLICT_WITH_E_RESPONSE,
  focus: { names: ["E"], direct_count: 0, source_count: 0, entries: [] },
};

function checkFocusChannel(name: string) {
  fireEvent.click(screen.getByRole("checkbox", { name }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("页面提交", () => {
  it("提交频道清单后按受影响频道分组展示差值、产物与来源", async () => {
    const fetchMock = mockFetch(200, CONFLICT_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");

    // 真实请求后端接口路径与载荷
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/analyze");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      input: "A 500.000\nB 500.100\nC 499.850\nD 500.250",
    });

    // 分组标题：受影响频道 + 频率
    expect(await screen.findByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
    expect(screen.getByText(/受影响频道 A（500\.000 MHz）/)).toBeInTheDocument();
    // 产物、差值、来源
    expect(screen.getByText("499.900 MHz")).toBeInTheDocument();
    expect(screen.getAllByText("50 kHz").length).toBe(4);
    expect(screen.getAllByText("A + B").length).toBe(2);
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
    // 可复制摘要
    expect(screen.getByText(/冲突总数：4/)).toBeInTheDocument();
  });

  it("输入有误时展示全部错误及行号，不展示任何风险结果", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        errors: [
          { line: 1, message: "频率「500.0005」非法：需为最多三位小数的 MHz 数值" },
          { line: 3, message: "频率 469.000 MHz 超出允许范围 470.000–694.000 MHz" },
        ],
      }),
    );
    render(<App />);

    submitForm("CH1 500.0005\nCH2 510.000\nCH3 469.000");

    expect(await screen.findByText(/输入有误（2 项）/)).toBeInTheDocument();
    expect(screen.getByText("第 1 行")).toBeInTheDocument();
    expect(screen.getByText("第 3 行")).toBeInTheDocument();
    expect(screen.getByText(/最多三位小数/)).toBeInTheDocument();
    expect(screen.getByText(/超出允许范围/)).toBeInTheDocument();
    expect(screen.queryByText(/受影响频道/)).not.toBeInTheDocument();
  });

  it("提交内容缺失时展示第 1 行提示而非技术错误", async () => {
    // 后端对缺失 input 字段等技术性 422 也统一返回 errors 结构；
    // 这里模拟异常路径（无 errors 字段），前端兜底仍须给出第 1 行提示
    vi.stubGlobal(
      "fetch",
      mockFetch(422, { detail: [{ loc: ["body", "input"], msg: "Field required" }] }),
    );
    render(<App />);

    submitForm("");

    expect(await screen.findByText("第 1 行")).toBeInTheDocument();
    expect(screen.getByText(/提交内容缺失或格式错误/)).toBeInTheDocument();
    expect(screen.queryByText(/受影响频道/)).not.toBeInTheDocument();
  });

  it("安全清单明确显示零项冲突", async () => {
    vi.stubGlobal("fetch", mockFetch(200, SAFE_RESPONSE));
    render(<App />);

    submitForm("C1 500.000\nC2 510.000");

    expect(await screen.findByText(/安全清单：零项冲突/)).toBeInTheDocument();
    expect(screen.getByText(/结论：安全清单，零项冲突。/)).toBeInTheDocument();
  });

  it("点击复制摘要写入剪贴板", async () => {
    vi.stubGlobal("fetch", mockFetch(200, SAFE_RESPONSE));
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<App />);

    submitForm("C1 500.000\nC2 510.000");
    await screen.findByText(/安全清单：零项冲突/);

    await userEvent.click(screen.getByRole("button", { name: "复制摘要" }));
    expect(writeText).toHaveBeenCalledWith(SAFE_RESPONSE.summary);
    expect(await screen.findByText("已复制 ✓")).toBeInTheDocument();
  });
});

describe("候选试加", () => {
  it("点击试加频道携带 candidate 调用接口，展示风险判定、候选自身与受影响既有频道", async () => {
    const fetchMock = mockFetch(200, RISKY_RESPONSE);
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fireEvent.change(screen.getByLabelText(/频道清单/), {
      target: { value: "A 500.000\nB 500.100\nC 499.850\nD 500.250" },
    });
    fillCandidate("X", "499.900");
    fireEvent.click(screen.getByRole("button", { name: /试加频道/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/analyze");
    expect(JSON.parse(init.body)).toEqual({
      input: "A 500.000\nB 500.100\nC 499.850\nD 500.250",
      candidate: { name: "X", freq: "499.900" },
    });

    // 风险判定横幅与计数
    expect(await screen.findByText(/试加有风险：新增 6 项冲突/)).toBeInTheDocument();
    expect(screen.getByText(/基线 4 项 → 合并后 9 项/)).toBeInTheDocument();
    // 受影响频道：候选自身与既有频道都标出
    expect(screen.getByText("X（候选自身）")).toBeInTheDocument();
    expect(screen.getByText("候选自身")).toBeInTheDocument(); // 分组徽标

    // 原有完整冲突分组仍然保留（新增分区中也可能出现同名受影响频道）
    expect(
      screen.getAllByText(/受影响频道 C（499\.850 MHz）/).length,
    ).toBeGreaterThan(0);

    // 候选加入后才出现的冲突分区
    expect(screen.getByText(/候选加入后才出现的冲突（3 个受影响频道）/)).toBeInTheDocument();
    // 既有目标+产物新增来源：C + X 标为新增，旧来源 B + D 仍展示
    expect(screen.getAllByText("499.950 MHz").length).toBeGreaterThan(0);
    expect(screen.getByText("C + X")).toBeInTheDocument();
    expect(screen.getAllByText("B + D").length).toBeGreaterThan(0);
    expect(screen.getAllByText("新增").length).toBeGreaterThan(0);
  });

  it("安全候选显示无增量且不出现新增冲突分区", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(200, {
        ...SAFE_RESPONSE,
        channel_count: 5,
        channels: [
          { name: "C1", freq_khz: 500_000, line: 1 },
          { name: "C2", freq_khz: 510_000, line: 2 },
          { name: "C3", freq_khz: 530_000, line: 3 },
          { name: "C4", freq_khz: 580_000, line: 4 },
          { name: "C5", freq_khz: 600_000, line: 5 },
        ],
        candidate: SAFE_CANDIDATE,
      }),
    );
    render(<App />);

    fillCandidate("X", "470.100");
    fireEvent.click(screen.getByRole("button", { name: /试加频道/ }));

    expect(
      await screen.findByText(/候选 X（470\.100 MHz）试加安全：无增量冲突/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/候选加入后才出现的冲突/)).not.toBeInTheDocument();
  });

  it("候选重复/越界时错误指向候选输入区，且不覆盖上一次有效结果", async () => {
    const fetchMock = vi
      .fn()
      // 第一次：正常试加，拿到有效结果
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => RISKY_RESPONSE,
      })
      // 第二次：候选名称与清单重复，line=0
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          errors: [{ line: 0, message: "候选名称「A」与清单中的现有频道重复，请换一个名称" }],
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fillCandidate("X", "499.900");
    fireEvent.click(screen.getByRole("button", { name: /试加频道/ }));
    await screen.findByText(/试加有风险：新增 6 项冲突/);

    // 改用重复名称再次试加
    fireEvent.change(screen.getByLabelText(/候选名称/), { target: { value: "A" } });
    fireEvent.click(screen.getByRole("button", { name: /试加频道/ }));

    expect(await screen.findByText(/候选输入有误（1 项），已保留上一次有效结果/)).toBeInTheDocument();
    expect(screen.getByText("候选输入区")).toBeInTheDocument();
    expect(screen.getByText(/候选名称「A」与清单中的现有频道重复/)).toBeInTheDocument();
    // 上一次有效结果仍在
    expect(screen.getByText(/试加有风险：新增 6 项冲突/)).toBeInTheDocument();
    // 不混入清单错误面板
    expect(screen.queryByText(/^第 0 行$/)).not.toBeInTheDocument();
  });

  it("清单非法时仍按原行号整批拒绝并清空风险结果，即使点的是试加", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        errors: [{ line: 1, message: "频率「500.0005」非法：需为最多三位小数的 MHz 数值" }],
      }),
    );
    render(<App />);

    fireEvent.change(screen.getByLabelText(/频道清单/), { target: { value: "CH1 500.0005" } });
    fillCandidate("X", "499.900");
    fireEvent.click(screen.getByRole("button", { name: /试加频道/ }));

    expect(await screen.findByText(/输入有误（1 项）/)).toBeInTheDocument();
    expect(screen.getByText("第 1 行")).toBeInTheDocument();
    expect(screen.queryByText(/受影响频道/)).not.toBeInTheDocument();
    expect(screen.queryByText(/候选输入有误/)).not.toBeInTheDocument();
  });

  it("提交排查（无候选）不携带 candidate 字段，且会清走上一次候选评估", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => RISKY_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => SAFE_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fillCandidate("X", "499.900");
    fireEvent.click(screen.getByRole("button", { name: /试加频道/ }));
    await screen.findByText(/试加有风险/);

    fireEvent.change(screen.getByLabelText(/频道清单/), {
      target: { value: "C1 500.000\nC2 510.000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /提交排查/ }));

    await screen.findByText(/安全清单：零项冲突/);
    const [, init2] = fetchMock.mock.calls[1];
    expect(JSON.parse(init2.body)).toEqual({ input: "C1 500.000\nC2 510.000" });
    expect(screen.queryByText(/试加有风险/)).not.toBeInTheDocument();
  });
});

describe("微调频点", () => {
  it("选择频道后点击查找微调频点，展示建议且保留当前冲突分组，不写回清单", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => RETUNE_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const listInput = "A 500.000\nB 500.100\nC 499.850\nD 500.250";
    submitForm(listInput);
    await screen.findByText(/发现 4 项冲突/);

    // 从分析结果关联的频道中选择一个并查找微调频点
    selectRetuneTarget("A");
    fireEvent.click(screen.getByRole("button", { name: /查找微调频点/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/analyze");
    expect(JSON.parse(init.body)).toEqual({ input: listInput, retune: "A" });

    // 建议表：替换频率、移动量、替换后冲突数、减少数量
    expect(await screen.findByText("499.875 MHz")).toBeInTheDocument();
    expect(screen.getByText("500.125 MHz")).toBeInTheDocument();
    expect(screen.getAllByText("125 kHz").length).toBe(2);
    expect(screen.getAllByText("−4 项").length).toBe(3);
    expect(screen.getByText(/当前 500\.000 MHz，当前冲突 4 项/)).toBeInTheDocument();

    // 当前冲突分组原样保留
    expect(screen.getByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
    // 建议不写回清单输入框
    expect(screen.getByLabelText(/频道清单/)).toHaveValue(listInput);
  });

  it("无改善时明确显示范围内无改善", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => RETUNE_EMPTY_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    selectRetuneTarget("A");
    fireEvent.click(screen.getByRole("button", { name: /查找微调频点/ }));

    expect(await screen.findByText(/范围内无改善/)).toBeInTheDocument();
    // 冲突分组仍然保留
    expect(screen.getByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
  });

  it("微调频道不存在时错误定位到微调区，且不清空既有分析结果", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          errors: [
            { line: -1, message: "微调频道「A」不在当前清单中，请从分析结果关联的频道中选择" },
          ],
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    // 提交后改掉清单文本（移除 A），再基于上一次结果发起微调
    fireEvent.change(screen.getByLabelText(/频道清单/), {
      target: { value: "B 500.100\nC 499.850\nD 500.250" },
    });
    selectRetuneTarget("A");
    fireEvent.click(screen.getByRole("button", { name: /查找微调频点/ }));

    expect(
      await screen.findByText(/微调频道选择有误（1 项），已保留当前分析结果/),
    ).toBeInTheDocument();
    expect(screen.getByText("微调区")).toBeInTheDocument();
    expect(screen.getByText(/微调频道「A」不在当前清单中/)).toBeInTheDocument();
    // 既有分析结果（冲突分组）仍然保留
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
    expect(screen.getByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
    // 不混入清单错误面板与候选错误面板
    expect(screen.queryByText(/输入有误/)).not.toBeInTheDocument();
    expect(screen.queryByText(/候选输入有误/)).not.toBeInTheDocument();
  });

  it("清单非法时仍按原行号整批拒绝并清空结果，即使点的是查找微调频点", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          errors: [{ line: 1, message: "频率「500.0005」非法：需为最多三位小数的 MHz 数值" }],
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    fireEvent.change(screen.getByLabelText(/频道清单/), {
      target: { value: "CH1 500.0005" },
    });
    selectRetuneTarget("A");
    fireEvent.click(screen.getByRole("button", { name: /查找微调频点/ }));

    expect(await screen.findByText(/输入有误（1 项）/)).toBeInTheDocument();
    expect(screen.getByText("第 1 行")).toBeInTheDocument();
    expect(screen.queryByText(/受影响频道/)).not.toBeInTheDocument();
    expect(screen.queryByText(/微调频道选择有误/)).not.toBeInTheDocument();
  });
});

describe("聚焦排查", () => {
  it("勾选重点频道后点击聚焦排查，顶部展示分级条目且完整分组与摘要不变", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => FOCUS_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<App />);

    const listInput = "A 500.000\nB 500.100\nC 499.850\nD 500.250";
    submitForm(listInput);
    await screen.findByText(/发现 4 项冲突/);

    // 从成功返回的频道中勾选重点频道并聚焦
    checkFocusChannel("A（500.000 MHz）");
    fireEvent.click(screen.getByRole("button", { name: /聚焦排查/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/analyze");
    expect(JSON.parse(init.body)).toEqual({ input: listInput, focus: ["A"] });

    // 聚焦结果：直接影响整体排在来源相关之前，标签区分两类关系
    expect(
      await screen.findByRole("heading", {
        name: "重点频道：A — 直接影响 1 项，来源相关 3 项",
      }),
    ).toBeInTheDocument();
    const rows = Array.from(container.querySelectorAll(".focus-result tbody tr"));
    expect(rows).toHaveLength(4);
    expect(rows[0].textContent).toContain("直接影响");
    expect(rows[0].textContent).toContain("A（500.000 MHz）");
    for (const row of rows.slice(1)) {
      expect(row.textContent).toContain("来源相关");
    }
    expect(screen.getByText("直接影响")).toBeInTheDocument();
    expect(screen.getAllByText("来源相关")).toHaveLength(3);
    // 聚焦条目保留原目标、产物与全部来源
    expect(rows[0].textContent).toContain("499.950 MHz");
    expect(rows[0].textContent).toContain("B + D");

    // 聚焦结果位于全部冲突分组之前，分组与摘要原样保留
    const focusEl = container.querySelector(".focus-result");
    const groupEl = container.querySelector(".group");
    expect(focusEl).not.toBeNull();
    expect(groupEl).not.toBeNull();
    expect(
      focusEl!.compareDocumentPosition(groupEl!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
    expect(screen.getByText(/冲突总数：4/)).toBeInTheDocument();
  });

  it("重点频道与冲突无关时显示明确空结果", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_WITH_E_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => FOCUS_EMPTY_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250\nE 600.000");
    await screen.findByText(/发现 4 项冲突/);

    checkFocusChannel("E（600.000 MHz）");
    fireEvent.click(screen.getByRole("button", { name: /聚焦排查/ }));

    expect(
      await screen.findByText(/重点频道与当前 4 项冲突均无关联，无需优先处理/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "重点频道：E — 直接影响 0 项，来源相关 0 项" }),
    ).toBeInTheDocument();
    // 完整冲突分组仍然保留
    expect(screen.getByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
  });

  it("重点频道不存在时错误定位到聚焦区，且保留上一次有效分析", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          errors: [
            { line: -2, message: "重点频道「A」不在当前清单中，请从分析结果关联的频道中勾选" },
          ],
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    // 提交后改掉清单文本（移除 A），再基于上一次结果发起聚焦
    fireEvent.change(screen.getByLabelText(/频道清单/), {
      target: { value: "B 500.100\nC 499.850\nD 500.250" },
    });
    checkFocusChannel("A（500.000 MHz）");
    fireEvent.click(screen.getByRole("button", { name: /聚焦排查/ }));

    expect(
      await screen.findByText(/聚焦频道选择有误（1 项），已保留当前分析结果/),
    ).toBeInTheDocument();
    expect(screen.getByText("聚焦区")).toBeInTheDocument();
    expect(screen.getByText(/重点频道「A」不在当前清单中/)).toBeInTheDocument();
    // 既有分析结果（冲突分组）仍然保留
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
    expect(screen.getByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
    // 不混入清单、候选与微调错误面板
    expect(screen.queryByText(/输入有误/)).not.toBeInTheDocument();
    expect(screen.queryByText(/候选输入有误/)).not.toBeInTheDocument();
    expect(screen.queryByText(/微调频道选择有误/)).not.toBeInTheDocument();
  });

  it("提交排查（无聚焦）不携带 focus 字段，且会清走上一次聚焦结果", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => FOCUS_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const listInput = "A 500.000\nB 500.100\nC 499.850\nD 500.250";
    submitForm(listInput);
    await screen.findByText(/发现 4 项冲突/);

    checkFocusChannel("A（500.000 MHz）");
    fireEvent.click(screen.getByRole("button", { name: /聚焦排查/ }));
    await screen.findByRole("heading", {
      name: "重点频道：A — 直接影响 1 项，来源相关 3 项",
    });

    fireEvent.click(screen.getByRole("button", { name: /提交排查/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const [, init3] = fetchMock.mock.calls[2];
    expect(JSON.parse(init3.body)).toEqual({ input: listInput });
    // 旧请求语义不变：响应无 focus 时不再展示聚焦结果
    expect(screen.queryByText(/重点频道：/)).not.toBeInTheDocument();
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
  });

  it("重点频道勾选上限为三个：选满后其余频道禁止勾选", async () => {
    vi.stubGlobal("fetch", mockFetch(200, CONFLICT_RESPONSE));
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    const checkbox = (name: string) =>
      screen.getByRole("checkbox", { name }) as HTMLInputElement;

    checkFocusChannel("A（500.000 MHz）");
    checkFocusChannel("B（500.100 MHz）");
    checkFocusChannel("C（499.850 MHz）");

    // 已勾选的三个保持可用，第四个被禁用，无法把选择扩大到四个
    expect(checkbox("A（500.000 MHz）").disabled).toBe(false);
    expect(checkbox("B（500.100 MHz）").disabled).toBe(false);
    expect(checkbox("C（499.850 MHz）").disabled).toBe(false);
    expect(checkbox("D（500.250 MHz）").disabled).toBe(true);
    expect(screen.getByText("已选满 3 个重点频道。")).toBeInTheDocument();

    // 取消一项后第四个恢复可选（纠正后限制放开）
    checkFocusChannel("C（499.850 MHz）");
    expect(checkbox("D（500.250 MHz）").disabled).toBe(false);
  });

  it("聚焦结果已显示时改选重点频道，立即隐藏旧结果，不拿旧条目冒充当前选择", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => FOCUS_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    checkFocusChannel("A（500.000 MHz）");
    fireEvent.click(screen.getByRole("button", { name: /聚焦排查/ }));
    await screen.findByRole("heading", {
      name: "重点频道：A — 直接影响 1 项，来源相关 3 项",
    });

    // 改选：在 A 之外再勾选 B，旧的聚焦条目必须立刻消失
    checkFocusChannel("B（500.100 MHz）");
    expect(screen.queryByText(/重点频道：/)).not.toBeInTheDocument();
    expect(container.querySelectorAll(".focus-result tbody tr")).toHaveLength(0);
    // 完整冲突分组仍保留，等待用户就新选择重新发起聚焦
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
    expect(screen.getByText(/受影响频道 C（499\.850 MHz）/)).toBeInTheDocument();
    // 未重新点击聚焦前不发起新请求
    expect(fetchMock.mock.calls).toHaveLength(2);
  });

  it("聚焦被超限拒绝后取消一项使选择恢复合法，聚焦区原错误随之清除", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      // 服务端仍以行号 -2 拒绝超限选择（后端是最终校验方，例如并发旧页面提交了 4 个）
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          errors: [{ line: -2, message: "重点频道最多选择 3 个，请取消后重新选择" }],
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    checkFocusChannel("A（500.000 MHz）");
    checkFocusChannel("B（500.100 MHz）");
    checkFocusChannel("C（499.850 MHz）");
    fireEvent.click(screen.getByRole("button", { name: /聚焦排查/ }));

    expect(await screen.findByText(/聚焦频道选择有误（1 项）/)).toBeInTheDocument();
    expect(screen.getByText(/重点频道最多选择 3 个/)).toBeInTheDocument();

    // 取消一项使选择恢复合法：原超限错误应立即随纠正清除，无需重新发起请求
    checkFocusChannel("C（499.850 MHz）");
    expect(screen.queryByText(/聚焦频道选择有误/)).not.toBeInTheDocument();
    expect(screen.queryByText(/重点频道最多选择 3 个/)).not.toBeInTheDocument();
    // 上一次有效分析仍然保留
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
    expect(fetchMock.mock.calls).toHaveLength(2);
  });

  it("完成聚焦后直接编辑清单名称或频率，基于旧清单的聚焦结果立即隐藏", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => CONFLICT_RESPONSE,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => FOCUS_RESPONSE,
      });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<App />);

    submitForm("A 500.000\nB 500.100\nC 499.850\nD 500.250");
    await screen.findByText(/发现 4 项冲突/);

    checkFocusChannel("A（500.000 MHz）");
    fireEvent.click(screen.getByRole("button", { name: /聚焦排查/ }));
    await screen.findByRole("heading", {
      name: "重点频道：A — 直接影响 1 项，来源相关 3 项",
    });

    // 不重新提交，直接修改清单中 A 的频率：旧聚焦条目随之失效
    fireEvent.change(screen.getByLabelText(/频道清单/), {
      target: { value: "A 510.000\nB 500.100\nC 499.850\nD 500.250" },
    });

    expect(screen.queryByText(/重点频道：/)).not.toBeInTheDocument();
    expect(container.querySelectorAll(".focus-result tbody tr")).toHaveLength(0);
    // 修改尚未提交，不发起新请求；主分析结果（上一次有效结果）仍保留供重新排查
    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(screen.getByText(/发现 4 项冲突/)).toBeInTheDocument();
  });
});
