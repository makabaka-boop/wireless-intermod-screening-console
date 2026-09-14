export interface ChannelOut {
  name: string;
  freq_khz: number;
  line: number;
}

export interface ConflictOut {
  target_name: string;
  target_freq_khz: number;
  product_khz: number;
  diff_khz: number;
  /** 每个来源组合为按频道名称排序的两个名称 */
  sources: [string, string][];
}

export interface NewConflictOut {
  target_name: string;
  target_freq_khz: number;
  product_khz: number;
  diff_khz: number;
  /** 合并候选后该「目标 + 产物」的全部来源组合 */
  sources: [string, string][];
  /** 相对基线新增的来源组合（均含候选） */
  new_sources: [string, string][];
  /** 受影响的目标频道是否就是候选自身 */
  target_is_candidate: boolean;
}

/** 彩排试加：候选频道的增量评估（仅携带候选的请求返回） */
export interface CandidateOut {
  name: string;
  freq_khz: number;
  /** safe：无增量冲突；risky：候选为自身或既有频道引入冲突 */
  status: "safe" | "risky";
  merged_channel_count: number;
  baseline_conflict_count: number;
  merged_conflict_count: number;
  new_conflict_count: number;
  new_conflicts: NewConflictOut[];
  affected_channel_names: string[];
}

/** 微调频点：单条替换建议（均严格优于当前清单） */
export interface RetuneSuggestionOut {
  /** 替换频率（整数 kHz） */
  freq_khz: number;
  /** 相对原频率的移动量（kHz，绝对值） */
  move_khz: number;
  /** 替换后整表冲突总数 */
  conflict_count: number;
  /** 相对当前清单减少的冲突数 */
  reduced_count: number;
}

/** 微调频点评估（仅携带 retune 的请求返回） */
export interface RetuneOut {
  name: string;
  original_freq_khz: number;
  baseline_conflict_count: number;
  /** 按 冲突总数 → 移动距离 → 频率升序 稳定排名，最多 5 条；为空即范围内无改善 */
  suggestions: RetuneSuggestionOut[];
}

/** 聚焦排查：一条与重点频道相关的冲突条目（保留原目标、产物与全部来源） */
export interface FocusEntryOut {
  /** direct：目标即重点频道；source：仅来源组合含重点频道 */
  relation: "direct" | "source";
  target_name: string;
  target_freq_khz: number;
  product_khz: number;
  diff_khz: number;
  /** 该「目标 + 产物」的全部来源组合，不拆分 */
  sources: [string, string][];
}

/** 聚焦排查评估（仅携带 focus 的请求返回） */
export interface FocusOut {
  /** 本次聚焦的重点频道（按请求顺序） */
  names: string[];
  /** 直接影响条目数 */
  direct_count: number;
  /** 来源相关条目数 */
  source_count: number;
  /** 直接影响整体在前，两类内部保持完整结果的原有顺序；为空即无相关冲突 */
  entries: FocusEntryOut[];
}

export interface AnalyzeResponse {
  channel_count: number;
  channels: ChannelOut[];
  conflict_count: number;
  conflicts: ConflictOut[];
  summary: string;
  candidate?: CandidateOut;
  retune?: RetuneOut;
  focus?: FocusOut;
}

export interface ErrorItem {
  line: number;
  message: string;
}
