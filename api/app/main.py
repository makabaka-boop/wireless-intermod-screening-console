"""无线话筒三阶互调排查台 —— FastAPI 服务。

接口（均为真实实现，无任何假接口）：
- GET  /api/health   健康检查
- POST /api/analyze  提交频道清单文本，返回冲突分析或带行号的全部输入错误；
  请求体可选携带 ``candidate``（{"name", "freq"}）进行彩排试加评估，
  响应在保留基线字段语义的同时增加 ``candidate`` 评估对象。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .imd import (
    CANDIDATE_LINE,
    analyze,
    build_summary,
    evaluate_candidate,
    parse_candidate,
    parse_channels,
)

app = FastAPI(title="无线话筒互调排查台 API", version="1.1.0")

# 开发联调允许跨域；生产部署由 web 容器的 nginx 反向代理 /api，同源访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CandidateIn(BaseModel):
    # 彩排临时借入的候选话筒：名称/频率为自由文本，校验与错误口径在业务层处理
    name: object = None
    freq: object = None


class AnalyzeRequest(BaseModel):
    input: str = Field(..., description="频道清单文本，每行：名称 频率(MHz)")
    # 候选结构在业务层校验：非对象形式（字符串/数字/数组等）必须按候选输入区
    # 错误（行号 0）拒绝，不能落入请求体验证异常而误报为清单第 1 行
    candidate: object = Field(
        default=None, description="可选：试加候选频道 {name, freq}，不写入正式清单"
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_req: Request, _exc: RequestValidationError):
    """请求体缺失或结构非法（如缺少 input 字段）时，仍按统一契约返回带行号的错误。"""
    return JSONResponse(
        status_code=422,
        content={
            "errors": [
                {
                    "line": 1,
                    "message": "提交内容缺失或格式错误：请求体应为 JSON，且包含 input 文本字段",
                }
            ]
        },
    )


class ChannelOut(BaseModel):
    name: str
    freq_khz: int
    line: int


class ConflictOut(BaseModel):
    target_name: str
    target_freq_khz: int
    product_khz: int
    diff_khz: int
    sources: list[list[str]]  # 每个来源组合为按名称排序的两个频道名


class NewConflictOut(BaseModel):
    target_name: str
    target_freq_khz: int
    product_khz: int
    diff_khz: int
    sources: list[list[str]]  # 合并后该「目标+产物」的全部来源组合
    new_sources: list[list[str]]  # 相对基线新增的来源组合（均含候选）
    target_is_candidate: bool


class CandidateOut(BaseModel):
    name: str
    freq_khz: int
    status: str  # safe：无增量冲突；risky：引入冲突
    merged_channel_count: int
    baseline_conflict_count: int
    merged_conflict_count: int
    new_conflict_count: int
    # 候选加入后才出现的冲突（含受影响的既有频道与候选自身）
    new_conflicts: list[NewConflictOut]
    affected_channel_names: list[str]


class AnalyzeResponse(BaseModel):
    channel_count: int
    channels: list[ChannelOut]
    conflict_count: int
    conflicts: list[ConflictOut]
    summary: str
    # 仅当请求携带候选时出现；未传候选时维持原有字段语义
    candidate: CandidateOut | None = None


class ErrorItem(BaseModel):
    line: int
    message: str


class ErrorResponse(BaseModel):
    errors: list[ErrorItem]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _conflict_out(c) -> ConflictOut:
    return ConflictOut(
        target_name=c.target_name,
        target_freq_khz=c.target_freq_khz,
        product_khz=c.product_khz,
        diff_khz=c.diff_khz,
        sources=[[x, y] for x, y in c.sources],
    )


@app.post(
    "/api/analyze",
    response_model=AnalyzeResponse,
    # 未传候选时响应中不出现 candidate 字段，严格维持旧版字段语义
    response_model_exclude_none=True,
    responses={422: {"model": ErrorResponse, "description": "输入校验失败"}},
)
def analyze_endpoint(req: AnalyzeRequest):
    channels, errors = parse_channels(req.input)
    if errors:
        # 整批清单有误：返回全部错误（含行号），绝不给出部分风险结果；
        # 此时不评估候选，避免清单行号错误掩盖候选区问题。
        return JSONResponse(
            status_code=422,
            content={"errors": [{"line": e.line, "message": e.message} for e in errors]},
        )

    candidate_out = None
    if req.candidate is not None:
        if not isinstance(req.candidate, dict):
            # 非对象形式的候选值：明确标记候选输入区（行号 0），
            # 不得误报为清单第 1 行的技术性错误
            return JSONResponse(
                status_code=422,
                content={
                    "errors": [
                        {
                            "line": CANDIDATE_LINE,
                            "message": "候选输入区格式错误：候选应为 {name, freq} 对象，"
                            '例如 {"name": "X", "freq": "499.950"}',
                        }
                    ]
                },
            )
        candidate_in = CandidateIn.model_validate(req.candidate)
        candidate, candidate_errors = parse_candidate(
            candidate_in.name, candidate_in.freq, channels
        )
        if candidate_errors:
            # 候选区错误固定 line=0，前端可据此只在候选输入区提示，
            # 且不清空上一次有效结果
            return JSONResponse(
                status_code=422,
                content={
                    "errors": [
                        {"line": e.line, "message": e.message} for e in candidate_errors
                    ]
                },
            )

        evaluation = evaluate_candidate(channels, candidate)
        candidate_out = CandidateOut(
            name=evaluation.name,
            freq_khz=evaluation.freq_khz,
            status=evaluation.status,
            merged_channel_count=evaluation.merged_channel_count,
            baseline_conflict_count=evaluation.baseline_conflict_count,
            merged_conflict_count=len(evaluation.merged_conflicts),
            new_conflict_count=len(evaluation.new_conflicts),
            new_conflicts=[
                NewConflictOut(
                    target_name=c.target_name,
                    target_freq_khz=c.target_freq_khz,
                    product_khz=c.product_khz,
                    diff_khz=c.diff_khz,
                    sources=[[x, y] for x, y in c.sources],
                    new_sources=[[x, y] for x, y in c.new_sources],
                    target_is_candidate=c.target_is_candidate,
                )
                for c in evaluation.new_conflicts
            ],
            affected_channel_names=evaluation.affected_channel_names,
        )
        # 顶层 conflicts 始终是当前展示清单（基线）的完整冲突分组；
        # 候选试加的合并结果只放在 candidate 对象中，不改变旧字段语义。
        conflicts = evaluation.baseline_conflicts
    else:
        conflicts = analyze(channels)

    return AnalyzeResponse(
        channel_count=len(channels),
        channels=[ChannelOut(name=c.name, freq_khz=c.freq_khz, line=c.line) for c in channels],
        conflict_count=len(conflicts),
        conflicts=[_conflict_out(c) for c in conflicts],
        summary=build_summary(channels, conflicts),
        candidate=candidate_out,
    )
