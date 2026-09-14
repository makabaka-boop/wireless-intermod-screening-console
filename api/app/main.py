"""无线话筒三阶互调排查台 —— FastAPI 服务。

接口（均为真实实现，无任何假接口）：
- GET  /api/health   健康检查
- POST /api/analyze  提交频道清单文本，返回冲突分析或带行号的全部输入错误；
  请求体可选携带 ``candidate``（{"name", "freq"}）进行彩排试加评估，
  响应在保留基线字段语义的同时增加 ``candidate`` 评估对象；
  亦可携带 ``retune``（已登记频道名称）获取该频道 ±500 kHz 内的
  微调频点建议（含清单版本标识 ``manifest_version``），响应增加
  ``retune`` 评估对象；
  亦可携带 ``focus``（一至三个重点频道名称列表）对现有冲突结果做
  聚焦排查，响应增加 ``focus`` 评估对象，完整结果与摘要不因此改写；
  亦可携带 ``apply``（{"name", "freq_khz", "version"}）直接应用一条
  微调建议：服务端先比对当前解析结果的版本标识，再按现有规则重算确认
  该频率仍在建议集中，通过后只替换目标行频率，响应携带新清单文本与
  ``applied`` 结果对象；版本不符或建议不可用时返回微调区（行号 -1）422。
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
    apply_retune,
    build_summary,
    evaluate_candidate,
    evaluate_focus,
    evaluate_retune,
    parse_candidate,
    parse_channels,
    parse_focus_channels,
    parse_retune_apply,
    parse_retune_target,
    validate_retune_apply,
)

app = FastAPI(title="无线话筒互调排查台 API", version="1.3.0")

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
    # 微调频道名称同样在业务层校验：非字符串或未在清单中登记的名称必须按
    # 微调区错误（行号 -1）拒绝，不能误报为清单第 1 行的技术性错误
    retune: object = Field(
        default=None,
        description="可选：微调目标频道名称，返回原频率 ±500 kHz 内的替换频点建议",
    )
    # 重点频道名称列表同样在业务层校验：非列表、名称不存在、重复或超过三个
    # 必须按聚焦区错误（行号 -2）拒绝，不能误报为清单第 1 行的技术性错误
    focus: object = Field(
        default=None,
        description="可选：一至三个重点频道名称列表，对现有冲突结果做聚焦排查",
    )
    # 应用微调建议：{name, freq_khz, version} 在业务层校验（结构错误同样按
    # 微调区行号 -1 拒绝）；与 candidate/retune/focus 互斥，应用时忽略其余可选字段
    apply: object = Field(
        default=None,
        description="可选：应用微调建议 {name, freq_khz, version}，只替换目标行频率",
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


class RetuneSuggestionOut(BaseModel):
    freq_khz: int  # 替换频率（整数 kHz）
    move_khz: int  # 相对原频率的移动量（kHz，绝对值）
    conflict_count: int  # 替换后整表冲突总数
    reduced_count: int  # 相对当前清单减少的冲突数


class RetuneOut(BaseModel):
    name: str
    original_freq_khz: int
    baseline_conflict_count: int
    # 按 冲突总数 → 移动距离 → 频率升序 稳定排名，最多 5 条；为空即范围内无改善
    suggestions: list[RetuneSuggestionOut]
    # 生成建议时的清单版本标识（频道名称 + 整数 kHz + 顺序）：
    # 应用建议时原样回传，服务端据此拒绝过期清单的旧建议
    manifest_version: str


class ApplyOut(BaseModel):
    """一次成功应用微调建议的结果（只替换了目标行频率）。"""

    name: str
    freq_khz: int  # 实际应用的替换频率（整数 kHz）
    version: str  # 应用成功后新清单的版本标识
    applied_text: str  # 替换目标行频率后的清单文本（页面据此同步编辑区）


class FocusEntryOut(BaseModel):
    relation: str  # direct：目标即重点频道；source：仅来源组合含重点频道
    target_name: str
    target_freq_khz: int
    product_khz: int
    diff_khz: int
    sources: list[list[str]]  # 该「目标+产物」的全部来源组合，不拆分


class FocusOut(BaseModel):
    names: list[str]  # 本次聚焦的重点频道（按请求顺序）
    direct_count: int  # 直接影响条目数
    source_count: int  # 来源相关条目数
    # 直接影响整体在前，两类内部保持完整结果的原有顺序；为空即无相关冲突
    entries: list[FocusEntryOut]


class AnalyzeResponse(BaseModel):
    channel_count: int
    channels: list[ChannelOut]
    conflict_count: int
    conflicts: list[ConflictOut]
    summary: str
    # 仅当请求携带候选时出现；未传候选时维持原有字段语义
    candidate: CandidateOut | None = None
    # 仅当请求携带微调参数时出现；未传微调参数时维持原有字段语义
    retune: RetuneOut | None = None
    # 仅当请求携带聚焦参数时出现；未传聚焦参数时维持原有字段语义
    focus: FocusOut | None = None
    # 仅当请求携带 apply 且应用成功时出现；其余请求维持原有字段语义
    applied: ApplyOut | None = None


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
    # 未传候选/微调/聚焦参数时响应中不出现对应字段，严格维持旧版字段语义
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

    if req.apply is not None:
        # 应用微调建议：先校验请求结构，再按当前清单重算版本与建议集，
        # 任一步失败都返回微调区错误（line=-1）且不给出任何应用结果
        name, freq_khz, version, apply_errors = parse_retune_apply(req.apply, channels)
        if apply_errors:
            return JSONResponse(
                status_code=422,
                content={
                    "errors": [
                        {"line": e.line, "message": e.message} for e in apply_errors
                    ]
                },
            )
        target, suggestion, apply_errors = validate_retune_apply(
            channels, name, freq_khz, version
        )
        if apply_errors:
            return JSONResponse(
                status_code=422,
                content={
                    "errors": [
                        {"line": e.line, "message": e.message} for e in apply_errors
                    ]
                },
            )
        # 校验通过：只替换目标行频率，对替换后的清单重算完整分析
        applied = apply_retune(req.input, channels, target, suggestion)
        return AnalyzeResponse(
            channel_count=len(applied.applied_channels),
            channels=[
                ChannelOut(name=c.name, freq_khz=c.freq_khz, line=c.line)
                for c in applied.applied_channels
            ],
            conflict_count=len(applied.conflicts),
            conflicts=[_conflict_out(c) for c in applied.conflicts],
            summary=build_summary(applied.applied_channels, applied.conflicts),
            applied=ApplyOut(
                name=applied.name,
                freq_khz=applied.freq_khz,
                version=applied.version,
                applied_text=applied.applied_text,
            ),
        )

    candidate_out = None
    conflicts = None
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

    retune_out = None
    if req.retune is not None:
        target, retune_errors = parse_retune_target(req.retune, channels)
        if retune_errors:
            # 微调区错误固定 line=-1，前端可据此只在微调区提示，
            # 且不清空上一次有效分析结果
            return JSONResponse(
                status_code=422,
                content={
                    "errors": [
                        {"line": e.line, "message": e.message} for e in retune_errors
                    ]
                },
            )
        retune_eval = evaluate_retune(channels, target)
        retune_out = RetuneOut(
            name=retune_eval.name,
            original_freq_khz=retune_eval.original_freq_khz,
            baseline_conflict_count=retune_eval.baseline_conflict_count,
            suggestions=[
                RetuneSuggestionOut(
                    freq_khz=s.freq_khz,
                    move_khz=s.move_khz,
                    conflict_count=s.conflict_count,
                    reduced_count=s.reduced_count,
                )
                for s in retune_eval.suggestions
            ],
            manifest_version=retune_eval.version,
        )
        # 顶层 conflicts 始终是当前清单的完整冲突分组；
        # 微调建议只放在 retune 对象中，不写回清单、不改变旧字段语义。
        if conflicts is None:
            conflicts = retune_eval.baseline_conflicts

    focus_out = None
    if req.focus is not None:
        focus_names, focus_errors = parse_focus_channels(req.focus, channels)
        if focus_errors:
            # 聚焦区错误固定 line=-2，前端可据此只在聚焦区提示，
            # 且不清空上一次有效分析结果
            return JSONResponse(
                status_code=422,
                content={
                    "errors": [
                        {"line": e.line, "message": e.message} for e in focus_errors
                    ]
                },
            )
        if conflicts is None:
            conflicts = analyze(channels)
        # 复用顶层完整冲突结果做聚焦分级，完整结果与摘要不因聚焦而改写
        focus_eval = evaluate_focus(conflicts, focus_names)
        focus_out = FocusOut(
            names=focus_eval.names,
            direct_count=focus_eval.direct_count,
            source_count=focus_eval.source_count,
            entries=[
                FocusEntryOut(
                    relation=e.relation,
                    target_name=e.target_name,
                    target_freq_khz=e.target_freq_khz,
                    product_khz=e.product_khz,
                    diff_khz=e.diff_khz,
                    sources=[[x, y] for x, y in e.sources],
                )
                for e in focus_eval.entries
            ],
        )

    if conflicts is None:
        conflicts = analyze(channels)

    return AnalyzeResponse(
        channel_count=len(channels),
        channels=[ChannelOut(name=c.name, freq_khz=c.freq_khz, line=c.line) for c in channels],
        conflict_count=len(conflicts),
        conflicts=[_conflict_out(c) for c in conflicts],
        summary=build_summary(channels, conflicts),
        candidate=candidate_out,
        retune=retune_out,
        focus=focus_out,
    )
