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

export interface AnalyzeResponse {
  channel_count: number;
  channels: ChannelOut[];
  conflict_count: number;
  conflicts: ConflictOut[];
  summary: string;
  candidate?: CandidateOut;
}

export interface ErrorItem {
  line: number;
  message: string;
}
