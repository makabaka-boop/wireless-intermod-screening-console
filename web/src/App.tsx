import { useMemo, useState } from "react";
import { analyze, InputError } from "./api";
import { formatMhz } from "./format";
import type {
  AnalyzeResponse,
  CandidateOut,
  ConflictOut,
  ErrorItem,
  FocusOut,
  NewConflictOut,
  RetuneOut,
} from "./types";

const EXAMPLE_INPUT = `# 每行一个频道：名称 频率(MHz)，470.000–694.000，最多三位小数
A 500.000
B 500.100
C 499.850
D 500.250`;

/** 候选区错误固定使用行号 0（清单正文行号从 1 开始） */
const CANDIDATE_AREA_LINE = 0;
/** 微调区错误固定使用行号 -1 */
const RETUNE_AREA_LINE = -1;
/** 聚焦区错误固定使用行号 -2 */
const FOCUS_AREA_LINE = -2;

interface ConflictGroup {
  targetName: string;
  targetFreqKhz: number;
  items: ConflictOut[];
}

interface NewConflictGroup {
  targetName: string;
  targetFreqKhz: number;
  targetIsCandidate: boolean;
  items: NewConflictOut[];
}

/** 冲突列表已按目标频率排序，按受影响频道分组展示 */
function groupByTarget(conflicts: ConflictOut[]): ConflictGroup[] {
  const groups: ConflictGroup[] = [];
  for (const c of conflicts) {
    const last = groups[groups.length - 1];
    if (last && last.targetName === c.target_name) {
      last.items.push(c);
    } else {
      groups.push({
        targetName: c.target_name,
        targetFreqKhz: c.target_freq_khz,
        items: [c],
      });
    }
  }
  return groups;
}

/** 候选引入的新增冲突同样按受影响频道分组 */
function groupNewByTarget(conflicts: NewConflictOut[]): NewConflictGroup[] {
  const groups: NewConflictGroup[] = [];
  for (const c of conflicts) {
    const last = groups[groups.length - 1];
    if (last && last.targetName === c.target_name) {
      last.items.push(c);
    } else {
      groups.push({
        targetName: c.target_name,
        targetFreqKhz: c.target_freq_khz,
        targetIsCandidate: c.target_is_candidate,
        items: [c],
      });
    }
  }
  return groups;
}

function sourceKey(pair: [string, string]) {
  return `${pair[0]} ${pair[1]}`;
}

export default function App() {
  const [text, setText] = useState(EXAMPLE_INPUT);
  const [candidateName, setCandidateName] = useState("");
  const [candidateFreq, setCandidateFreq] = useState("");
  const [retuneTarget, setRetuneTarget] = useState("");
  const [focusNames, setFocusNames] = useState<string[]>([]);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [errors, setErrors] = useState<ErrorItem[]>([]);
  const [candidateErrors, setCandidateErrors] = useState<ErrorItem[]>([]);
  const [retuneErrors, setRetuneErrors] = useState<ErrorItem[]>([]);
  const [focusErrors, setFocusErrors] = useState<ErrorItem[]>([]);
  const [fatal, setFatal] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const groups = useMemo(
    () => (result ? groupByTarget(result.conflicts) : []),
    [result],
  );
  const newGroups = useMemo(
    () => (result?.candidate ? groupNewByTarget(result.candidate.new_conflicts) : []),
    [result],
  );

  async function runAnalyze(
    mode: "list" | "candidate" | "retune" | "focus" | "apply",
    applyReq?: { name: string; freqKhz: number; manifestVersion: string },
  ) {
    setLoading(true);
    setFatal(null);
    setCopied(false);
    try {
      const data = await analyze(
        text,
        mode === "candidate" ? { name: candidateName, freq: candidateFreq } : undefined,
        mode === "retune" ? retuneTarget : undefined,
        mode === "focus" ? focusNames : undefined,
        mode === "apply" && applyReq
          ? {
              name: applyReq.name,
              freqKhz: applyReq.freqKhz,
              manifestVersion: applyReq.manifestVersion,
            }
          : undefined,
      );
      setResult(data);
      setErrors([]);
      setCandidateErrors([]);
      setRetuneErrors([]);
      setFocusErrors([]);
      // 应用成功：用服务端只替换目标行后的文本同步编辑区（注释/空白原样保留），
      // 完整冲突分组与摘要已随返回结果更新
      if (mode === "apply" && data.applied) {
        setText(data.applied.applied_text);
        setRetuneTarget("");
      }
      // 重点频道勾选项随最新有效结果收敛，避免残留已不在清单中的不可见勾选
      const validNames = new Set(data.channels.map((c) => c.name));
      setFocusNames((prev) => prev.filter((n) => validNames.has(n)));
    } catch (err) {
      if (err instanceof InputError) {
        // 应用建议失败时，把服务端判定归一为页面约定的稳定提示：
        // 版本不符 -> 「清单已变化，请重新查找建议」；频率已不在重算建议集 ->
        // 「建议不可用」。其余微调区错误沿用服务端原文。
        let reportedErrors = err.errors;
        if (mode === "apply") {
          reportedErrors = err.errors.map((e) => {
            if (e.line !== RETUNE_AREA_LINE) return e;
            if (e.message.includes("清单已变化")) {
              return { ...e, message: "清单已变化，请重新查找建议" };
            }
            if (e.message.includes("建议不可用")) {
              return { ...e, message: `建议不可用：${formatMhz(applyReq?.freqKhz ?? 0)} MHz 已不在按当前清单重算的建议集中，请重新查找建议` };
            }
            return e;
          });
        }
        // 行号 0 专指候选输入区、-1 专指微调区（含应用建议失败）、-2 专指聚焦区：
        // 区域错误只在对应区域提示，且不覆盖上一次有效结果；
        // 清单行号（≥1）错误整批拒绝。
        // 应用失败时保留当前输入文本：runAnalyze 只在成功路径改写编辑区，
        // 最近一次有效结果同样保留，协调员可据提示重新查找建议。
        const listErrors = reportedErrors.filter((e) => e.line >= 1);
        const areaErrors = reportedErrors.filter((e) => e.line === CANDIDATE_AREA_LINE);
        const tuneErrors = reportedErrors.filter((e) => e.line === RETUNE_AREA_LINE);
        const areaFocusErrors = reportedErrors.filter((e) => e.line === FOCUS_AREA_LINE);
        if (listErrors.length > 0) {
          setErrors(listErrors);
          setCandidateErrors([]);
          setRetuneErrors([]);
          setFocusErrors([]);
          setResult(null); // 清单非法：整批拒绝，不展示任何风险结果
        } else if (tuneErrors.length > 0) {
          setRetuneErrors(tuneErrors);
          setErrors([]);
          setCandidateErrors([]);
          setFocusErrors([]);
        } else if (areaFocusErrors.length > 0) {
          setFocusErrors(areaFocusErrors);
          setErrors([]);
          setCandidateErrors([]);
          setRetuneErrors([]);
        } else {
          setCandidateErrors(areaErrors);
          setErrors([]);
          setRetuneErrors([]);
          setFocusErrors([]);
        }
      } else {
        setFatal(err instanceof Error ? err.message : "未知错误");
      }
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    void runAnalyze("list");
  }

  function onTryAdd() {
    void runAnalyze("candidate");
  }

  function onRetune() {
    void runAnalyze("retune");
  }

  /**
   * 直接采用一条微调建议：提交当前清单文本、频道名称、建议频率（整数 kHz）
   * 与查询建议时返回的清单版本标识。服务端先比对版本、再按现有规则重算
   * 确认频率仍在建议集中；成功后只替换目标行频率，页面据返回结果同步
   * 编辑区、冲突分组与摘要。失败（清单已变化/建议不可用）只在微调区提示，
   * 当前输入与最近一次有效结果均保留。
   */
  function onApplySuggestion(name: string, freqKhz: number, manifestVersion: string) {
    void runAnalyze("apply", { name, freqKhz, manifestVersion });
  }

  function toggleFocus(name: string) {
    // 改选即意味着当前展示的聚焦结果（或聚焦区错误）对应的是上一次选择：
    // 立即隐藏旧结果并清除错误，避免旧条目冒充当前选择，也让超限纠正即时生效
    setFocusErrors([]);
    setResult((prev) => (prev?.focus ? { ...prev, focus: undefined } : prev));
    setFocusNames((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  }

  function onFocus() {
    void runAnalyze("focus");
  }

  /** 清单文本一旦被编辑，基于旧清单算出的聚焦结果即失效（名称/频率可能已改） */
  function onListChange(value: string) {
    setText(value);
    setFocusErrors([]);
    setResult((prev) => (prev?.focus ? { ...prev, focus: undefined } : prev));
  }

  async function copySummary() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.summary);
    } catch {
      // 剪贴板 API 不可用时的回退：选中文本框便于手动复制
      const el = document.getElementById("summary-text");
      if (el) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
    }
    setCopied(true);
  }

  const candidate: CandidateOut | undefined = result?.candidate;
  const retune: RetuneOut | undefined = result?.retune;
  const focus: FocusOut | undefined = result?.focus;

  return (
    <main className="page">
      <header>
        <h1>无线话筒三阶互调排查台</h1>
        <p className="hint">
          判定口径：对每对频道计算 2A−B 与 2B−A，仅保留 470.000–694.000 MHz
          内的产物；产物与非来源频道差值 ≤ 50 kHz（含恰好 50 kHz）即记冲突。
        </p>
      </header>

      <form onSubmit={onSubmit}>
        <div className="input-grid">
          <div className="input-col">
            <label htmlFor="channels">频道清单（每行：名称 频率，支持 # 注释）</label>
            <textarea
              id="channels"
              rows={10}
              spellCheck={false}
              value={text}
              onChange={(e) => onListChange(e.target.value)}
            />
          </div>
          <aside className="candidate-box" aria-label="候选试加区">
            <h2>候选话筒（仅试加，不写入正式清单）</h2>
            <label htmlFor="candidate-name">候选名称</label>
            <input
              id="candidate-name"
              type="text"
              spellCheck={false}
              autoComplete="off"
              placeholder="例如 X"
              value={candidateName}
              onChange={(e) => setCandidateName(e.target.value)}
            />
            <label htmlFor="candidate-freq">候选频率（MHz，470.000–694.000）</label>
            <input
              id="candidate-freq"
              type="text"
              spellCheck={false}
              autoComplete="off"
              placeholder="例如 499.950"
              value={candidateFreq}
              onChange={(e) => setCandidateFreq(e.target.value)}
            />
            <button
              type="button"
              className="secondary"
              disabled={loading}
              onClick={onTryAdd}
            >
              {loading ? "试加评估中…" : "试加频道"}
            </button>
            <p className="hint">临时借入的话筒先试评估增量影响，再决定是否写入清单。</p>
          </aside>
        </div>
        <button type="submit" disabled={loading}>
          {loading ? "排查中…" : "提交排查"}
        </button>
      </form>

      {fatal && (
        <section className="panel error-panel" role="alert">
          <h2>请求失败</h2>
          <p>{fatal}</p>
        </section>
      )}

      {errors.length > 0 && (
        <section className="panel error-panel" role="alert">
          <h2>输入有误（{errors.length} 项），本次不给出任何风险结果</h2>
          <ul className="error-list">
            {errors.map((e, i) => (
              <li key={i}>
                <span className="line-badge">第 {e.line} 行</span>
                {e.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {candidateErrors.length > 0 && (
        <section className="panel error-panel candidate-error-panel" role="alert">
          <h2>
            候选输入有误（{candidateErrors.length} 项）
            {result ? "，已保留上一次有效结果" : "，请修正后重试"}
          </h2>
          <ul className="error-list">
            {candidateErrors.map((e, i) => (
              <li key={i}>
                <span className="line-badge">候选输入区</span>
                {e.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {retuneErrors.length > 0 && (
        <section className="panel error-panel retune-error-panel" role="alert">
          <h2>
            微调频点操作有误（{retuneErrors.length} 项）
            {result ? "，已保留当前输入与最近一次有效结果" : "，请重新选择"}
          </h2>
          <ul className="error-list">
            {retuneErrors.map((e, i) => (
              <li key={i}>
                <span className="line-badge">微调区</span>
                {e.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {focusErrors.length > 0 && (
        <section className="panel error-panel focus-error-panel" role="alert">
          <h2>
            聚焦频道选择有误（{focusErrors.length} 项）
            {result ? "，已保留当前分析结果" : "，请重新勾选"}
          </h2>
          <ul className="error-list">
            {focusErrors.map((e, i) => (
              <li key={i}>
                <span className="line-badge">聚焦区</span>
                {e.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {result && (
        <section className="panel">
          {result.conflict_count === 0 ? (
            <h2 className="safe">安全清单：零项冲突（共 {result.channel_count} 个频道）</h2>
          ) : (
            <h2 className="danger">
              发现 {result.conflict_count} 项冲突（共 {result.channel_count} 个频道）
            </h2>
          )}

          {candidate && (
            <div
              className={
                candidate.status === "safe"
                  ? "candidate-verdict candidate-safe"
                  : "candidate-verdict candidate-risky"
              }
              role="status"
            >
              {candidate.status === "safe" ? (
                <>
                  <h3 className="safe">
                    候选 {candidate.name}（{formatMhz(candidate.freq_khz)} MHz）试加安全：无增量冲突
                  </h3>
                  <p>
                    合并后共 {candidate.merged_channel_count} 个频道、
                    {candidate.merged_conflict_count} 项冲突，与基线清单一致，可考虑正式加入。
                  </p>
                </>
              ) : (
                <>
                  <h3 className="danger">
                    候选 {candidate.name}（{formatMhz(candidate.freq_khz)} MHz）试加有风险：新增{" "}
                    {candidate.new_conflict_count} 项冲突
                  </h3>
                  <p>
                    基线 {candidate.baseline_conflict_count} 项 → 合并后{" "}
                    {candidate.merged_conflict_count} 项（共 {candidate.merged_channel_count}{" "}
                    个频道）；受影响频道：
                    {candidate.affected_channel_names.map((n) => (
                      <span
                        className={
                          n === candidate.name ? "affected affected-candidate" : "affected"
                        }
                        key={n}
                      >
                        {n}
                        {n === candidate.name ? "（候选自身）" : ""}
                      </span>
                    ))}
                  </p>
                </>
              )}
            </div>
          )}

          <div className="focus-box">
            <div className="focus-controls">
              <h3>聚焦排查</h3>
              {result.channels.map((c) => {
                const checked = focusNames.includes(c.name);
                // 已选满三个时，其余频道禁止再勾选；已勾选项仍可取消
                return (
                  <label className="focus-option" key={c.name}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!checked && focusNames.length >= 3}
                      onChange={() => toggleFocus(c.name)}
                    />
                    {c.name}（{formatMhz(c.freq_khz)} MHz）
                  </label>
                );
              })}
              <button
                type="button"
                className="secondary"
                disabled={loading || focusNames.length === 0}
                onClick={onFocus}
              >
                {loading ? "聚焦中…" : "聚焦排查"}
              </button>
            </div>
            <p className="hint">
              勾选一至三个重点频道（如主持人、主唱话筒，最多 3
              个，已选满时其余频道不可再勾选），在顶部优先展示与之相关的冲突条目；
              完整冲突分组与可复制摘要保持不变。
              {focusNames.length >= 3 && <strong>已选满 3 个重点频道。</strong>}
            </p>

            {focus && (
              <div className="focus-result" role="status">
                <h4>
                  重点频道：{focus.names.join("、")} — 直接影响 {focus.direct_count}{" "}
                  项，来源相关 {focus.source_count} 项
                </h4>
                {focus.entries.length === 0 ? (
                  <p className="focus-empty">
                    重点频道与当前 {result.conflict_count} 项冲突均无关联，无需优先处理。
                  </p>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>关系</th>
                        <th>受影响频道</th>
                        <th>互调产物</th>
                        <th>差值</th>
                        <th>来源组合</th>
                      </tr>
                    </thead>
                    <tbody>
                      {focus.entries.map((e) => (
                        <tr key={`${e.target_name}-${e.product_khz}`}>
                          <td>
                            <span
                              className={
                                e.relation === "direct" ? "tag tag-direct" : "tag tag-source"
                              }
                            >
                              {e.relation === "direct" ? "直接影响" : "来源相关"}
                            </span>
                          </td>
                          <td>
                            {e.target_name}（{formatMhz(e.target_freq_khz)} MHz）
                          </td>
                          <td>{formatMhz(e.product_khz)} MHz</td>
                          <td>{e.diff_khz} kHz</td>
                          <td>
                            {e.sources.map((pair, i) => (
                              <span className="source" key={i}>
                                {pair[0]} + {pair[1]}
                              </span>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>

          {groups.map((g) => (
            <article className="group" key={g.targetName}>
              <h3>
                受影响频道 {g.targetName}（{formatMhz(g.targetFreqKhz)} MHz）—{" "}
                {g.items.length} 项
              </h3>
              <table>
                <thead>
                  <tr>
                    <th>互调产物</th>
                    <th>差值</th>
                    <th>来源组合</th>
                  </tr>
                </thead>
                <tbody>
                  {g.items.map((c) => (
                    <tr key={c.product_khz}>
                      <td>{formatMhz(c.product_khz)} MHz</td>
                      <td>{c.diff_khz} kHz</td>
                      <td>
                        {c.sources.map((pair, i) => (
                          <span className="source" key={i}>
                            {pair[0]} + {pair[1]}
                          </span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </article>
          ))}

          {candidate && candidate.status === "risky" && (
            <article className="new-conflicts">
              <h3 className="danger">候选加入后才出现的冲突（{newGroups.length} 个受影响频道）</h3>
              {newGroups.map((g) => (
                <div className="group" key={g.targetName}>
                  <h4>
                    受影响频道 {g.targetName}（{formatMhz(g.targetFreqKhz)} MHz）
                    {g.targetIsCandidate && <span className="tag tag-candidate">候选自身</span>}
                    {" — "}
                    {g.items.length} 项新增
                  </h4>
                  <table>
                    <thead>
                      <tr>
                        <th>互调产物</th>
                        <th>差值</th>
                        <th>来源组合（加粗为新增来源）</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.items.map((c) => {
                        const newSourceKeys = new Set(c.new_sources.map(sourceKey));
                        return (
                          <tr key={c.product_khz}>
                            <td>{formatMhz(c.product_khz)} MHz</td>
                            <td>{c.diff_khz} kHz</td>
                            <td>
                              {c.sources.map((pair, i) => (
                                <span
                                  className={
                                    newSourceKeys.has(sourceKey(pair))
                                      ? "source source-new"
                                      : "source"
                                  }
                                  key={i}
                                >
                                  {pair[0]} + {pair[1]}
                                  {newSourceKeys.has(sourceKey(pair)) && (
                                    <em className="new-badge">新增</em>
                                  )}
                                </span>
                              ))}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ))}
            </article>
          )}

          <div className="retune-box">
            <div className="retune-controls">
              <h3>微调频点</h3>
              <label htmlFor="retune-target">微调频道</label>
              <select
                id="retune-target"
                value={retuneTarget}
                onChange={(e) => setRetuneTarget(e.target.value)}
              >
                <option value="">请选择要微调的频道</option>
                {result.channels.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}（{formatMhz(c.freq_khz)} MHz）
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="secondary"
                disabled={loading || !retuneTarget}
                onClick={onRetune}
              >
                {loading ? "查找中…" : "查找微调频点"}
              </button>
            </div>
            <p className="hint">
              在原频率 ±500 kHz 内按 25 kHz 步长枚举合法且未占用的频点，最多 5
              条建议；点「应用此频点」只会把目标行频率写入编辑区并按新清单重算，
              其余行（含注释与空白）保持不变。清单改名、改频或调序后旧建议会被服务端拒绝。
            </p>

            {retune && (
              <div className="retune-result" role="status">
                <h4>
                  {retune.name}（当前 {formatMhz(retune.original_freq_khz)} MHz，当前冲突{" "}
                  {retune.baseline_conflict_count} 项）
                </h4>
                {retune.suggestions.length === 0 ? (
                  <p className="retune-empty">
                    范围内无改善：±500 kHz 内没有能减少冲突的替换频点。
                  </p>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>替换频率</th>
                        <th>移动量</th>
                        <th>替换后冲突</th>
                        <th>减少</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {retune.suggestions.map((s) => (
                        <tr key={s.freq_khz}>
                          <td>{formatMhz(s.freq_khz)} MHz</td>
                          <td>{s.move_khz} kHz</td>
                          <td>{s.conflict_count} 项</td>
                          <td>−{s.reduced_count} 项</td>
                          <td>
                            <button
                              type="button"
                              className="secondary apply-retune-btn"
                              data-testid={`apply-retune-${s.freq_khz}`}
                              disabled={loading}
                              onClick={() =>
                                onApplySuggestion(
                                  retune.name,
                                  s.freq_khz,
                                  retune.manifest_version,
                                )
                              }
                            >
                              {loading ? "应用中…" : "应用此频点"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>

          <div className="summary-header">
            <h3>可复制摘要</h3>
            <button type="button" onClick={copySummary}>
              {copied ? "已复制 ✓" : "复制摘要"}
            </button>
          </div>
          <pre id="summary-text" className="summary">
            {result.summary}
          </pre>
        </section>
      )}
    </main>
  );
}
