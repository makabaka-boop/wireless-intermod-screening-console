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

export interface AnalyzeResponse {
  channel_count: number;
  channels: ChannelOut[];
  conflict_count: number;
  conflicts: ConflictOut[];
  summary: string;
}

export interface ErrorItem {
  line: number;
  message: string;
}
