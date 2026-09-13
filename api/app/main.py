"""无线话筒三阶互调排查台 —— FastAPI 服务。

接口（均为真实实现，无任何假接口）：
- GET  /api/health   健康检查
- POST /api/analyze  提交频道清单文本，返回冲突分析或带行号的全部输入错误
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .imd import analyze, build_summary, parse_channels

app = FastAPI(title="无线话筒互调排查台 API", version="1.0.0")

# 开发联调允许跨域；生产部署由 web 容器的 nginx 反向代理 /api，同源访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    input: str = Field(..., description="频道清单文本，每行：名称 频率(MHz)")


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


class AnalyzeResponse(BaseModel):
    channel_count: int
    channels: list[ChannelOut]
    conflict_count: int
    conflicts: list[ConflictOut]
    summary: str


class ErrorItem(BaseModel):
    line: int
    message: str


class ErrorResponse(BaseModel):
    errors: list[ErrorItem]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/analyze",
    response_model=AnalyzeResponse,
    responses={422: {"model": ErrorResponse, "description": "输入校验失败"}},
)
def analyze_endpoint(req: AnalyzeRequest):
    channels, errors = parse_channels(req.input)
    if errors:
        # 整批输入有误：返回全部错误（含行号），绝不给出部分风险结果
        return JSONResponse(
            status_code=422,
            content={"errors": [{"line": e.line, "message": e.message} for e in errors]},
        )

    conflicts = analyze(channels)
    return AnalyzeResponse(
        channel_count=len(channels),
        channels=[ChannelOut(name=c.name, freq_khz=c.freq_khz, line=c.line) for c in channels],
        conflict_count=len(conflicts),
        conflicts=[
            ConflictOut(
                target_name=c.target_name,
                target_freq_khz=c.target_freq_khz,
                product_khz=c.product_khz,
                diff_khz=c.diff_khz,
                sources=[[x, y] for x, y in c.sources],
            )
            for c in conflicts
        ],
        summary=build_summary(channels, conflicts),
    )
