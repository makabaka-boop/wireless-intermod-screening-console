"""三阶互调（IMD3）排查核心逻辑。

判定口径（与 README「开发说明」一致）：

1. 频率一律精确换算为整数 kHz（MHz × 1000），全程整数运算，杜绝浮点误差；
2. 对每一对不同频道 A、B 分别计算三阶互调产物 2A−B 与 2B−A；
3. 只保留仍落在 470.000–694.000 MHz（含两端）频段内的产物；
4. 若产物与「生成它的两个频道之外」任一已分配频率相差不超过 50 kHz
   （包括恰好 50 kHz），即记为一条冲突；
5. 相同「产物 + 目标频道」只展示一次，但列全所有来源组合；
   每个来源组合内部按频道名称排序，组合之间也按名称排序；
6. 总结果依次按 目标频率 → 产物频率 → 目标名称 排序。

候选试加（彩排临时借话筒）：
``evaluate_candidate`` 复用上面的频道解析、整数 kHz 换算与冲突排序，
对「基线清单」与「基线 + 候选」各分析一次，并以
（目标频道，产物频率，来源组合）构成的稳定键比较两次结果，
单独标出候选加入后才出现的冲突及受影响频道。候选本身的输入错误
使用 :data:`CANDIDATE_LINE`（0）作为行号，与清单正文的行号区分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_KHZ = 470_000  # 470.000 MHz
MAX_KHZ = 694_000  # 694.000 MHz
GUARD_KHZ = 50  # 保护带：差值 ≤ 50 kHz（含恰好 50 kHz）即冲突
MIN_CHANNELS = 2
MAX_CHANNELS = 32

# 候选输入区错误的固定行号（清单正文行号从 1 开始，0 专指候选区）
CANDIDATE_LINE = 0

# 名称中不允许出现的分隔字符：空白、英文逗号、中文逗号（与清单两列分隔口径一致）
NAME_SEPARATOR_PATTERN = re.compile(r"[\s,，]")

# 最多三位小数的 MHz 数值，如 500、500.1、500.125
FREQ_PATTERN = re.compile(r"^\d{1,4}(?:\.\d{1,3})?$")


@dataclass(frozen=True)
class Channel:
    name: str
    freq_khz: int
    line: int  # 输入文本中的行号（从 1 开始）；候选频道固定为 CANDIDATE_LINE


@dataclass(frozen=True)
class InputError:
    line: int  # 出错行号（从 1 开始）；候选区错误固定为 CANDIDATE_LINE
    message: str


@dataclass
class Conflict:
    target_name: str
    target_freq_khz: int
    product_khz: int
    diff_khz: int
    # 每个来源组合为按频道名称排序的二元组；组合列表整体也按名称排序
    sources: list[tuple[str, str]] = field(default_factory=list)


def parse_freq_khz(text: str) -> int | None:
    """把 MHz 文本精确换算为整数 kHz；非法格式返回 None。"""
    if not FREQ_PATTERN.match(text):
        return None
    integer, _, frac = text.partition(".")
    frac = frac.ljust(3, "0")  # "5" -> "500"，即 500.5 MHz = 500500 kHz
    return int(integer) * 1000 + int(frac)


def format_mhz(khz: int) -> str:
    """整数 kHz -> 固定三位小数的 MHz 文本。"""
    return f"{khz // 1000}.{khz % 1000:03d}"


def parse_channels(text: str) -> tuple[list[Channel], list[InputError]]:
    """逐行解析输入文本，收集全部错误（带行号），不做提前退出。

    行格式：``名称 频率``（空白或中英文逗号分隔）；空行与 ``#`` 开头的注释行跳过。
    只要存在任何错误，调用方就不得进行风险分析（整批拒绝，不给部分结果）。
    """
    errors: list[InputError] = []
    channels: list[Channel] = []
    seen_names: dict[str, int] = {}
    seen_freqs: dict[int, int] = {}

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[\s,，]+", line)
        if len(parts) != 2:
            errors.append(
                InputError(lineno, "格式错误：应为「名称 频率」两列，例如 CH1 500.000")
            )
            continue
        name, freq_text = parts

        # 名称可为任意非空白文本（可含点号、长度不限），唯一性是唯一约束
        if name in seen_names:
            errors.append(
                InputError(
                    lineno,
                    f"频道名称「{name}」与第 {seen_names[name]} 行重复，名称必须唯一",
                )
            )
        else:
            seen_names[name] = lineno

        freq_khz = parse_freq_khz(freq_text)
        if freq_khz is None:
            errors.append(
                InputError(
                    lineno,
                    f"频率「{freq_text}」非法：需为最多三位小数的 MHz 数值，例如 500.125",
                )
            )
        elif not MIN_KHZ <= freq_khz <= MAX_KHZ:
            errors.append(
                InputError(
                    lineno,
                    f"频率 {format_mhz(freq_khz)} MHz 超出允许范围 "
                    f"{format_mhz(MIN_KHZ)}–{format_mhz(MAX_KHZ)} MHz",
                )
            )
        elif freq_khz in seen_freqs:
            errors.append(
                InputError(
                    lineno,
                    f"频率 {format_mhz(freq_khz)} MHz 与第 {seen_freqs[freq_khz]} 行重复，频率必须互不重复",
                )
            )
        else:
            seen_freqs[freq_khz] = lineno

        # 即便本行有错也照常登记，保证后续行号与错误收集完整；
        # 存在任何错误时调用方会整批拒绝，不会用到这份列表。
        channels.append(Channel(name=name, freq_khz=freq_khz or 0, line=lineno))

    if len(channels) > MAX_CHANNELS:
        overflow = channels[MAX_CHANNELS]
        errors.append(
            InputError(
                overflow.line,
                f"频道数量超出上限 {MAX_CHANNELS} 个（本行已是第 {len(channels)} 个）",
            )
        )
    elif 0 < len(channels) < MIN_CHANNELS and not errors:
        errors.append(
            InputError(
                channels[-1].line,
                f"频道数量不足：至少需要 {MIN_CHANNELS} 个，当前仅 {len(channels)} 个",
            )
        )
    elif not channels and not errors:
        errors.append(InputError(1, f"输入为空：请至少提供 {MIN_CHANNELS} 个频道"))

    return channels, errors


def analyze(channels: list[Channel]) -> list[Conflict]:
    """对已校验的频道列表执行三阶互调排查，返回排序后的冲突列表。"""
    by_key: dict[tuple[str, int], Conflict] = {}

    for i in range(len(channels)):
        for j in range(i + 1, len(channels)):
            a, b = channels[i], channels[j]
            for product in (2 * a.freq_khz - b.freq_khz, 2 * b.freq_khz - a.freq_khz):
                if not MIN_KHZ <= product <= MAX_KHZ:
                    continue  # 只保留仍在频段内的产物
                for c in channels:
                    if c.name == a.name or c.name == b.name:
                        continue  # 与生成产物的两个频道自身比较无意义
                    diff = abs(product - c.freq_khz)
                    if diff <= GUARD_KHZ:  # 含恰好 50 kHz
                        key = (c.name, product)
                        pair = (a.name, b.name) if a.name <= b.name else (b.name, a.name)
                        conflict = by_key.get(key)
                        if conflict is None:
                            by_key[key] = Conflict(
                                target_name=c.name,
                                target_freq_khz=c.freq_khz,
                                product_khz=product,
                                diff_khz=diff,
                                sources=[pair],
                            )
                        elif pair not in conflict.sources:
                            conflict.sources.append(pair)

    # 相同「产物 + 目标」只展示一次：by_key 已去重；来源组合内部与列表均按名称排序
    result = sorted(
        by_key.values(),
        key=lambda item: (item.target_freq_khz, item.product_khz, item.target_name),
    )
    for conflict in result:
        conflict.sources.sort()
    return result


def build_summary(channels: list[Channel], conflicts: list[Conflict]) -> str:
    """生成可复制的纯文本摘要。"""
    lines = [
        "无线话筒三阶互调排查摘要",
        "判定口径：产物 2A−B / 2B−A 落在 470.000–694.000 MHz 内，"
        "且与非来源频道差值 ≤ 50 kHz（含恰好 50 kHz）即记冲突",
        f"频道数：{len(channels)}",
        f"冲突总数：{len(conflicts)}",
    ]
    if not conflicts:
        lines.append("结论：安全清单，零项冲突。")
    else:
        for idx, c in enumerate(conflicts, start=1):
            sources = "；".join(f"{x} + {y}" for x, y in c.sources)
            lines.append(
                f"[{idx}] 受影响 {c.target_name}（{format_mhz(c.target_freq_khz)} MHz）"
                f" ← 产物 {format_mhz(c.product_khz)} MHz，"
                f"差值 {c.diff_khz} kHz，来源：{sources}"
            )
    return "\n".join(lines)


@dataclass
class NewConflict:
    """候选加入后才出现的冲突条目。

    ``sources`` 为合并结果中该「目标 + 产物」的全部来源组合；
    ``new_sources`` 为相对基线新增的来源组合（必然全部含候选）。
    当基线本无该「目标 + 产物」时二者相同。
    """

    target_name: str
    target_freq_khz: int
    product_khz: int
    diff_khz: int
    sources: list[tuple[str, str]]
    new_sources: list[tuple[str, str]]
    target_is_candidate: bool  # 受影响的目标频道是否就是候选自身


@dataclass
class CandidateEvaluation:
    """候选频道的试加评估结果。"""

    name: str
    freq_khz: int
    merged_channel_count: int
    baseline_conflicts: list[Conflict]  # 基线清单的完整冲突分组
    # 合并候选后的完整冲突列表（排序口径与 analyze 一致）
    merged_conflicts: list[Conflict]
    # 候选加入后才出现的冲突，沿用 目标频率 → 产物频率 → 目标名称 排序
    new_conflicts: list[NewConflict]
    affected_channel_names: list[str]  # 新增冲突涉及的全部受影响频道（按名排序去重）

    @property
    def status(self) -> str:
        """safe：无增量冲突；risky：候选为自身或既有频道引入了冲突。"""
        return "safe" if not self.new_conflicts else "risky"

    @property
    def baseline_conflict_count(self) -> int:
        return len(self.baseline_conflicts)


def parse_candidate(
    name: object, freq: object, channels: list[Channel]
) -> tuple[Channel | None, list[InputError]]:
    """校验候选名称/频率（复用整数 kHz 换算与范围口径）。

    候选不参与清单解析，错误固定指向候选输入区（:data:`CANDIDATE_LINE`）。
    ``channels`` 为已成功解析的基线频道，用于名称/频率重复与合并数量校验。
    """
    errors: list[InputError] = []

    if not isinstance(name, str) or not name.strip():
        errors.append(InputError(CANDIDATE_LINE, "候选名称缺失：请在候选输入区填写频道名称"))
        name = ""
    else:
        name = name.strip()
        if NAME_SEPARATOR_PATTERN.search(name):
            errors.append(
                InputError(
                    CANDIDATE_LINE,
                    "候选名称「%s」非法：名称不得包含空白或逗号" % name,
                )
            )
        elif any(c.name == name for c in channels):
            errors.append(
                InputError(
                    CANDIDATE_LINE,
                    f"候选名称「{name}」与清单中的现有频道重复，请换一个名称",
                )
            )

    if not isinstance(freq, str) or not freq.strip():
        errors.append(InputError(CANDIDATE_LINE, "候选频率缺失：请在候选输入区填写频率(MHz)"))
        freq_text = ""
    else:
        freq_text = freq.strip()
        freq_khz = parse_freq_khz(freq_text)
        if freq_khz is None:
            errors.append(
                InputError(
                    CANDIDATE_LINE,
                    f"候选频率「{freq_text}」非法：需为最多三位小数的 MHz 数值，例如 500.125",
                )
            )
        elif not MIN_KHZ <= freq_khz <= MAX_KHZ:
            errors.append(
                InputError(
                    CANDIDATE_LINE,
                    f"候选频率 {format_mhz(freq_khz)} MHz 超出允许范围 "
                    f"{format_mhz(MIN_KHZ)}–{format_mhz(MAX_KHZ)} MHz",
                )
            )
        elif any(c.freq_khz == freq_khz for c in channels):
            errors.append(
                InputError(
                    CANDIDATE_LINE,
                    f"候选频率 {format_mhz(freq_khz)} MHz 与清单中的现有频道重复，"
                    "频率必须互不重复",
                )
            )

    if len(channels) >= MAX_CHANNELS:
        errors.append(
            InputError(
                CANDIDATE_LINE,
                f"清单已有 {len(channels)} 个频道，试加候选后将超过上限 {MAX_CHANNELS} 个",
            )
        )

    if errors:
        return None, errors
    return Channel(name=name, freq_khz=freq_khz, line=CANDIDATE_LINE), []


def _conflict_index(
    conflicts: list[Conflict],
) -> dict[tuple[str, int], Conflict]:
    """以（目标频道名，产物 kHz）稳定键索引冲突。"""
    return {(c.target_name, c.product_khz): c for c in conflicts}


def evaluate_candidate(channels: list[Channel], candidate: Channel) -> CandidateEvaluation:
    """比较基线与「基线 + 候选」两次分析，标出候选带来的增量冲突。

    复用 :func:`analyze` 的整数运算、去重与排序口径；
    以（目标频道，产物，来源组合）稳定键比较两次结果。
    """
    baseline = analyze(channels)
    merged = analyze(channels + [candidate])
    base_index = _conflict_index(baseline)

    new_conflicts: list[NewConflict] = []
    affected: set[str] = set()
    for c in merged:
        base = base_index.get((c.target_name, c.product_khz))
        base_sources = set(base.sources) if base is not None else set()
        new_sources = [pair for pair in c.sources if pair not in base_sources]
        if not new_sources:
            continue
        new_conflicts.append(
            NewConflict(
                target_name=c.target_name,
                target_freq_khz=c.target_freq_khz,
                product_khz=c.product_khz,
                diff_khz=c.diff_khz,
                sources=list(c.sources),
                new_sources=new_sources,
                target_is_candidate=(c.target_name == candidate.name),
            )
        )
        affected.add(c.target_name)

    new_conflicts.sort(
        key=lambda item: (item.target_freq_khz, item.product_khz, item.target_name)
    )
    return CandidateEvaluation(
        name=candidate.name,
        freq_khz=candidate.freq_khz,
        merged_channel_count=len(channels) + 1,
        baseline_conflicts=baseline,
        merged_conflicts=merged,
        new_conflicts=new_conflicts,
        affected_channel_names=sorted(affected),
    )
