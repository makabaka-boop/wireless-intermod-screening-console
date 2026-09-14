import type { AnalyzeResponse, ErrorItem } from "./types";

/** 输入校验失败：后端返回 422 与全部错误（含行号） */
export class InputError extends Error {
  constructor(public readonly errors: ErrorItem[]) {
    super("输入校验失败");
    this.name = "InputError";
  }
}

/** 彩排试加的候选频道；名称/频率在候选输入区单独填写 */
export interface CandidateInput {
  name: string;
  freq: string;
}

/**
 * 提交频道清单进行互调排查。
 * 请求真实后端接口 /api/analyze（开发环境由 vite 代理，生产由 nginx 反代）。
 * candidate 非空时携带候选对象，后端在保留基线结果的同时返回增量评估；
 * retune 非空时携带微调频道名称，后端返回该频道 ±500 kHz 内的替换频点建议；
 * focus 非空时携带一至三个重点频道名称，后端复用现有冲突结果返回聚焦分级，
 * 完整结果与可复制摘要不因聚焦而改写。
 */
export async function analyze(
  input: string,
  candidate?: CandidateInput,
  retune?: string,
  focus?: string[],
): Promise<AnalyzeResponse> {
  const payload: Record<string, unknown> = { input };
  if (candidate) {
    payload.candidate = { name: candidate.name, freq: candidate.freq };
  }
  if (retune) {
    payload.retune = retune;
  }
  if (focus && focus.length > 0) {
    payload.focus = focus;
  }
  let res: Response;
  try {
    res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error("无法连接 API 服务，请确认 api 容器已启动");
  }

  if (res.status === 422) {
    const data = await res.json().catch(() => null);
    // 正常路径后端返回 { errors: [...] }；异常路径（如代理层 422）兜底给出第 1 行提示
    const errors =
      data && Array.isArray(data.errors) && data.errors.length > 0
        ? (data.errors as ErrorItem[])
        : [{ line: 1, message: "提交内容缺失或格式错误：请检查频道清单后重试" }];
    throw new InputError(errors);
  }
  if (!res.ok) {
    throw new Error(`服务器错误（HTTP ${res.status}）`);
  }
  return (await res.json()) as AnalyzeResponse;
}
