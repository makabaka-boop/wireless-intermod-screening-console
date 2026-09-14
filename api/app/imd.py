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

微调频点（演出前微调已登记话筒）：
``evaluate_retune`` 在目标频道原频率上下 500 kHz 内按 25 kHz 步长枚举
合法（频段内）且未被其他频道占用的替换频点，复用 :func:`analyze`
重新计算整表冲突，仅保留冲突总数严格优于当前清单的方案，
按（冲突总数，移动距离，频率升序）稳定排名，最多返回
:data:`RETUNE_MAX_SUGGESTIONS` 条建议；评估结果带由频道名称、整数 kHz
与顺序确定的清单版本标识（:func:`manifest_version`）。页面可直接应用某条
建议：``parse_retune_apply`` 校验应用请求，``validate_retune_apply``
先比对当前清单版本、再按现有规则重算确认频率仍在建议集中，通过后
``apply_retune`` 只替换目标行频率并重算完整分析；版本不符或建议不可用
均按微调区错误（:data:`RETUNE_LINE`，-1）拒绝，不改写清单。
微调区错误使用 :data:`RETUNE_LINE`（-1）作为行号。

聚焦排查（围绕主持人/主唱等重点话筒优先处理冲突）：
``evaluate_focus`` 复用现有冲突结果，不重新计算、不改写完整分组：
目标频道即重点频道的条目标为「直接影响」，仅来源组合含重点频道的
条目标为「来源相关」，按（关系等级，原有顺序）稳定排列——直接影响
整体在前，两类内部各自保持完整结果的原有顺序；每个聚焦条目保留
原目标、产物与全部来源组合，含多组来源的条目不拆分、不重复。
聚焦区错误使用 :data:`FOCUS_LINE`（-2）作为行号。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

MIN_KHZ = 470_000  # 470.000 MHz
MAX_KHZ = 694_000  # 694.000 MHz
GUARD_KHZ = 50  # 保护带：差值 ≤ 50 kHz（含恰好 50 kHz）即冲突
MIN_CHANNELS = 2
MAX_CHANNELS = 32

# 候选输入区错误的固定行号（清单正文行号从 1 开始，0 专指候选区）
CANDIDATE_LINE = 0

# 微调频点：原频率上下 500 kHz、25 kHz 步长、最多 5 条建议
RETUNE_RANGE_KHZ = 500
RETUNE_STEP_KHZ = 25
RETUNE_MAX_SUGGESTIONS = 5
# 微调区错误的固定行号（-1 专指微调区，与清单行号、候选区行号区分）
RETUNE_LINE = -1

# 清单版本标识：由频道名称、整数 kHz 频率与频道顺序确定（与注释/空白无关），
# 仅用于让页面在应用微调建议时确认清单未在查看建议后发生变化；
# 带版本前缀便于日后调整口径
MANIFEST_VERSION_PREFIX = "mv1"
# 应用建议请求中提交的清单版本标识形如 mv1-<64 位十六进制>
MANIFEST_VERSION_PATTERN = re.compile(r"^" + MANIFEST_VERSION_PREFIX + r"-[0-9a-f]{64}$")

# 聚焦排查：一次最多勾选 3 个重点频道
FOCUS_MAX_CHANNELS = 3
# 聚焦区错误的固定行号（-2 专指聚焦区，与清单行号、候选区、微调区行号区分）
FOCUS_LINE = -2

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


def manifest_version(channels: list[Channel]) -> str:
    """由频道名称、整数 kHz 频率与频道顺序确定的清单版本标识。

    每项取 ``顺序:名称:整数kHz``（顺序从 0 开始），以换行连接后取 SHA-256；
    注释与空白不参与解析，自然不影响版本——只调整注释或空白而频道序列
    （名称/频率/顺序）未变时，版本标识保持一致。
    """
    payload = "\n".join(
        f"{idx}:{c.name}:{c.freq_khz}" for idx, c in enumerate(channels)
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{MANIFEST_VERSION_PREFIX}-{digest}"


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
    名称口径与清单可接纳的名称一致：不得含空白/逗号分隔符，也不得以 ``#``
    开头（清单中 ``#`` 开头的整行会被当作注释，候选写入后将丢失）。
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
        elif name.startswith("#"):
            # 清单中 # 开头的整行会被当作注释，此类名称写入正式清单后会丢失，
            # 候选校验须与清单可接纳名称口径一致
            errors.append(
                InputError(
                    CANDIDATE_LINE,
                    f"候选名称「{name}」非法：名称不得以 # 开头，"
                    "否则写入正式清单后整行会被当作注释",
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
        # 每条增量冲突都涉及候选（作为被命中目标或新增来源组合的一方），
        # 即便候选自身从未被产物命中，也必须列入受影响频道名单
        affected.add(candidate.name)

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


@dataclass
class RetuneSuggestion:
    """一条微调替换建议（均严格优于当前清单）。"""

    freq_khz: int  # 替换频率（整数 kHz）
    move_khz: int  # 相对原频率的移动量（kHz，绝对值）
    conflict_count: int  # 替换后整表冲突总数
    reduced_count: int  # 相对当前清单减少的冲突数


@dataclass
class RetuneEvaluation:
    """微调频点评估结果；``suggestions`` 为空表示范围内无改善。"""

    name: str
    original_freq_khz: int
    baseline_conflicts: list[Conflict]  # 当前清单的完整冲突分组
    # 按（冲突总数，移动距离，频率升序）稳定排名，最多 RETUNE_MAX_SUGGESTIONS 条
    suggestions: list[RetuneSuggestion]
    # 生成建议时的清单版本标识（频道名称 + 整数 kHz + 顺序）：
    # 页面应用建议时回传，服务端据此拒绝过期清单的旧建议
    version: str

    @property
    def baseline_conflict_count(self) -> int:
        return len(self.baseline_conflicts)


def parse_retune_target(
    name: object, channels: list[Channel]
) -> tuple[Channel | None, list[InputError]]:
    """校验微调目标频道名称，返回清单中已登记的对应频道。

    微调区错误固定指向 :data:`RETUNE_LINE`，与清单正文行号、候选区行号区分；
    名称不存在时不影响调用方保留上一次有效分析结果。
    """
    if not isinstance(name, str) or not name.strip():
        return None, [
            InputError(
                RETUNE_LINE,
                "微调频道缺失：请从分析结果关联的频道中选择要微调的话筒",
            )
        ]
    stripped = name.strip()
    for c in channels:
        if c.name == stripped:
            return c, []
    return None, [
        InputError(
            RETUNE_LINE,
            f"微调频道「{stripped}」不在当前清单中，请从分析结果关联的频道中选择",
        )
    ]


def evaluate_retune(channels: list[Channel], target: Channel) -> RetuneEvaluation:
    """在目标频道原频率 ±500 kHz 内按 25 kHz 步长枚举替换频点。

    只保留合法（仍在 470.000–694.000 MHz 频段内，含两端边界候选）且未被
    其他频道占用的频点；对每个候选频点复用 :func:`analyze` 重算整表冲突，
    仅保留冲突总数严格少于当前清单的方案，按（冲突总数，移动距离，频率升序）
    稳定排名，最多返回 :data:`RETUNE_MAX_SUGGESTIONS` 条。建议只作展示，
    不写回清单。
    """
    baseline = analyze(channels)
    baseline_count = len(baseline)
    occupied = {c.freq_khz for c in channels if c.name != target.name}

    suggestions: list[RetuneSuggestion] = []
    start = target.freq_khz - RETUNE_RANGE_KHZ
    stop = target.freq_khz + RETUNE_RANGE_KHZ
    # range 上界再进一步，保证 ±500 kHz 的边界候选也参与计算
    for freq in range(start, stop + RETUNE_STEP_KHZ, RETUNE_STEP_KHZ):
        if freq == target.freq_khz:
            continue  # 原位不算微调
        if not MIN_KHZ <= freq <= MAX_KHZ:
            continue  # 只保留频段内的合法频点
        if freq in occupied:
            continue  # 已被其他频道占用
        replaced = [
            Channel(c.name, freq, c.line) if c.name == target.name else c
            for c in channels
        ]
        count = len(analyze(replaced))
        if count < baseline_count:
            suggestions.append(
                RetuneSuggestion(
                    freq_khz=freq,
                    move_khz=abs(freq - target.freq_khz),
                    conflict_count=count,
                    reduced_count=baseline_count - count,
                )
            )

    suggestions.sort(key=lambda s: (s.conflict_count, s.move_khz, s.freq_khz))
    return RetuneEvaluation(
        name=target.name,
        original_freq_khz=target.freq_khz,
        baseline_conflicts=baseline,
        suggestions=suggestions[:RETUNE_MAX_SUGGESTIONS],
        version=manifest_version(channels),
    )


# 聚焦条目与重点频道的关系等级：直接影响（目标即重点频道）整体排在
# 来源相关（仅来源组合含重点频道）之前
FOCUS_RELATION_DIRECT = "direct"
FOCUS_RELATION_SOURCE = "source"


@dataclass
class FocusEntry:
    """一条与重点频道相关的冲突条目（完整保留原目标、产物与全部来源）。"""

    relation: str  # FOCUS_RELATION_DIRECT / FOCUS_RELATION_SOURCE
    target_name: str
    target_freq_khz: int
    product_khz: int
    diff_khz: int
    sources: list[tuple[str, str]]  # 该「目标 + 产物」的全部来源组合，不拆分


@dataclass
class FocusEvaluation:
    """聚焦排查结果；``entries`` 为空表示重点频道与当前冲突均无关联。"""

    names: list[str]  # 本次聚焦的重点频道（按请求顺序）
    # 按（关系等级，原有顺序）稳定排列：直接影响在前，两类内部保持原有冲突顺序
    entries: list[FocusEntry]

    @property
    def direct_count(self) -> int:
        return sum(1 for e in self.entries if e.relation == FOCUS_RELATION_DIRECT)

    @property
    def source_count(self) -> int:
        return sum(1 for e in self.entries if e.relation == FOCUS_RELATION_SOURCE)


def parse_focus_channels(
    value: object, channels: list[Channel]
) -> tuple[list[str] | None, list[InputError]]:
    """校验重点频道名称列表（一至三个、不得重复、必须已在清单中登记）。

    聚焦区错误固定指向 :data:`FOCUS_LINE`，与清单正文行号、候选区、
    微调区行号区分；名称不存在/重复/超限时调用方保留上一次有效分析结果。
    """
    if not isinstance(value, list):
        return None, [
            InputError(
                FOCUS_LINE,
                "聚焦频道格式错误：应为重点频道名称列表，"
                '例如 ["主持人", "主唱"]',
            )
        ]

    errors: list[InputError] = []
    names: list[str] = []
    if not value:
        errors.append(
            InputError(
                FOCUS_LINE,
                f"聚焦频道缺失：请从分析结果关联的频道中勾选一至 {FOCUS_MAX_CHANNELS} 个重点频道",
            )
        )
    else:
        if len(value) > FOCUS_MAX_CHANNELS:
            errors.append(
                InputError(
                    FOCUS_LINE,
                    f"重点频道数量超出上限：最多选择 {FOCUS_MAX_CHANNELS} 个，"
                    f"当前选择 {len(value)} 个",
                )
            )
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item.strip():
                errors.append(
                    InputError(FOCUS_LINE, "重点频道名称缺失或非法：名称应为非空文本")
                )
                continue
            name = item.strip()
            if name in seen:
                errors.append(
                    InputError(
                        FOCUS_LINE,
                        f"重点频道「{name}」重复选择，每个频道只能勾选一次",
                    )
                )
                continue
            seen.add(name)
            if not any(c.name == name for c in channels):
                errors.append(
                    InputError(
                        FOCUS_LINE,
                        f"重点频道「{name}」不在当前清单中，"
                        "请从分析结果关联的频道中勾选",
                    )
                )
                continue
            names.append(name)

    if errors:
        return None, errors
    return names, []


def evaluate_focus(
    conflicts: list[Conflict], focus_names: list[str]
) -> FocusEvaluation:
    """从现有冲突结果中筛出与重点频道相关的条目并分级排序。

    只读传入的冲突列表（顶层完整结果），不重新计算、不改写：
    目标频道即重点频道的记为「直接影响」，仅来源组合含重点频道的记为
    「来源相关」（目标优先，同一条目不重复计入）；按（关系等级，原有顺序）
    稳定排列，直接影响整体在前，两类内部各自保持完整结果的原有顺序。
    """
    focus = set(focus_names)
    entries: list[FocusEntry] = []
    for c in conflicts:
        if c.target_name in focus:
            relation = FOCUS_RELATION_DIRECT
        elif any(x in focus or y in focus for x, y in c.sources):
            relation = FOCUS_RELATION_SOURCE
        else:
            continue
        entries.append(
            FocusEntry(
                relation=relation,
                target_name=c.target_name,
                target_freq_khz=c.target_freq_khz,
                product_khz=c.product_khz,
                diff_khz=c.diff_khz,
                sources=list(c.sources),
            )
        )

    # Python sort 稳定：仅按关系等级排序即可在两类内部保留原有冲突顺序
    entries.sort(key=lambda e: 0 if e.relation == FOCUS_RELATION_DIRECT else 1)
    return FocusEvaluation(names=list(focus_names), entries=entries)


@dataclass
class RetuneApply:
    """一次成功的「应用此频点」：只替换目标行频率后的清单与重算结果。"""

    name: str
    freq_khz: int  # 实际应用的替换频率（整数 kHz）
    version: str  # 应用成功后新清单的版本标识
    applied_text: str  # 替换目标行频率后的清单文本（注释/空白与其余行原样保留）
    applied_channels: list[Channel]  # 替换后的频道列表（行号不变）
    conflicts: list[Conflict]  # 替换后清单的完整冲突分组


def parse_retune_apply(
    value: object, channels: list[Channel]
) -> tuple[str, int, str, list[InputError]]:
    """校验应用建议请求 ``{name, freq_khz, version}`` 的字段与类型。

    成功返回（频道名称，整数 kHz 频率，版本标识，无错误）；
    失败时前三项为空值且错误全部指向微调区（:data:`RETUNE_LINE`）。
    这里只做结构与类型校验：版本是否匹配、频率是否仍在建议集中由
    :func:`validate_retune_apply` 依据当前清单重算确认，避免旧建议被误写入。
    """
    if not isinstance(value, dict):
        return "", 0, "", [
            InputError(
                RETUNE_LINE,
                "应用微调频点请求格式错误：应为包含频道名称、建议频率与清单版本的对象",
            )
        ]

    errors: list[InputError] = []
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(
            InputError(
                RETUNE_LINE,
                "应用微调频点请求缺少频道名称：请从微调建议旁重新点击「应用此频点」",
            )
        )
        name = ""
    else:
        name = name.strip()

    freq_khz = value.get("freq_khz")
    # bool 是 int 的子类型，须显式排除；整数 kHz 不接受浮点/字符串
    if not isinstance(freq_khz, int) or isinstance(freq_khz, bool):
        errors.append(
            InputError(
                RETUNE_LINE,
                "应用微调频点请求的建议频率格式错误：应为整数 kHz，请从微调建议旁重新点击"
                "「应用此频点」",
            )
        )
        freq_khz = 0

    version = value.get("version")
    if not isinstance(version, str) or not MANIFEST_VERSION_PATTERN.match(version):
        errors.append(
            InputError(
                RETUNE_LINE,
                "应用微调频点请求的清单版本标识格式错误：请重新查找建议后再应用",
            )
        )
        version = ""

    if errors:
        return "", 0, "", errors
    return name, freq_khz, version, []


def validate_retune_apply(
    channels: list[Channel], name: str, freq_khz: int, version: str
) -> tuple[Channel | None, RetuneSuggestion | None, list[InputError]]:
    """按当前清单重新校验应用请求，顺序与页面提示对应。

    1. 先比对当前解析结果的版本标识与请求携带的标识：不一致说明清单已变化
       （改名、改频或调整频道顺序），提示重新查找建议；
    2. 再确认微调目标频道仍在当前清单中；
    3. 最后按现有规则重算该频道的建议集，确认请求频率仍是其中一条
       （合法、未被占用且严格优于当前清单）。
    任何一步失败都不得改写清单。
    """
    if manifest_version(channels) != version:
        return None, None, [
            InputError(
                RETUNE_LINE,
                "清单已变化，请重新查找建议",
            )
        ]

    target, target_errors = parse_retune_target(name, channels)
    if target_errors:
        return None, None, target_errors

    # 按现有规则重算建议集（枚举口径与「查找微调频点」完全一致），
    # 只接受仍在建议集中的频率，杜绝未被返回的频率被写入
    evaluation = evaluate_retune(channels, target)
    for suggestion in evaluation.suggestions:
        if suggestion.freq_khz == freq_khz:
            return target, suggestion, []
    return None, None, [
        InputError(
            RETUNE_LINE,
            f"建议不可用：{format_mhz(freq_khz)} MHz 不在按当前清单重算的微调建议集中，"
            "请重新查找建议",
        )
    ]


def apply_retune_text(text: str, target: Channel, freq_khz: int) -> str:
    """只替换目标行（``target.line``）的频率文本，其余字符（含注释与空白）原样保留。

    行内仍按解析口径的两列结构（名称 + 频率，空白或中英文逗号分隔）定位频率，
    行首缩进与列间分隔符保持不变；行尾换行若存在也原样保留。
    """
    lines = text.splitlines(keepends=True)
    idx = target.line - 1
    raw = lines[idx]
    if raw.endswith("\n"):
        body, ending = raw[:-1], "\n"
    else:
        body, ending = raw, ""
    if body.endswith("\r"):
        body, ending = body[:-1], "\r" + ending

    match = re.match(r"^(\s*\S+[\s,，]+)(\S+)(\s*)$", body)
    if match:
        prefix, _old_freq, suffix = match.groups()
        body = f"{prefix}{format_mhz(freq_khz)}{suffix}"
    else:  # 兜底：理论上不会到达（该行已按同一口径成功解析）
        body = f"{target.name} {format_mhz(freq_khz)}"
    lines[idx] = body + ending
    return "".join(lines)


def apply_retune(
    text: str,
    channels: list[Channel],
    target: Channel,
    suggestion: RetuneSuggestion,
) -> RetuneApply:
    """只替换目标行频率并对替换后的清单重算完整分析。"""
    freq_khz = suggestion.freq_khz
    applied_channels = [
        Channel(c.name, freq_khz, c.line) if c.name == target.name else c
        for c in channels
    ]
    return RetuneApply(
        name=target.name,
        freq_khz=freq_khz,
        version=manifest_version(applied_channels),
        applied_text=apply_retune_text(text, target, freq_khz),
        applied_channels=applied_channels,
        conflicts=analyze(applied_channels),
    )
