import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { AnalyzeResponse } from "./types";

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
