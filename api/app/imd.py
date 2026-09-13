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
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_KHZ = 470_000  # 470.000 MHz
MAX_KHZ = 694_000  # 694.000 MHz
GUARD_KHZ = 50  # 保护带：差值 ≤ 50 kHz（含恰好 50 kHz）即冲突
MIN_CHANNELS = 2
MAX_CHANNELS = 32

# 最多三位小数的 MHz 数值，如 500、500.1、500.125
FREQ_PATTERN = re.compile(r"^\d{1,4}(?:\.\d{1,3})?$")
# 频道名称：字母/数字/下划线/连字符/中文，1–32 字符
NAME_PATTERN = re.compile(r"^[\w\-]{1,32}$")


@dataclass(frozen=True)
class Channel:
    name: str
    freq_khz: int
    line: int  # 输入文本中的行号（从 1 开始）


@dataclass(frozen=True)
class InputError:
    line: int  # 出错行号（从 1 开始）
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

        if not NAME_PATTERN.match(name):
            errors.append(
                InputError(
                    lineno,
                    f"频道名称「{name}」非法：仅允许字母、数字、下划线、连字符或中文，1–32 字符",
                )
            )
        elif name in seen_names:
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
