import { useMemo, useState } from "react";
import { analyze, InputError } from "./api";
import { formatMhz } from "./format";
import type { AnalyzeResponse, ConflictOut, ErrorItem } from "./types";

const EXAMPLE_INPUT = `# 每行一个频道：名称 频率(MHz)，470.000–694.000，最多三位小数
A 500.000
B 500.100
C 499.850
D 500.250`;

interface ConflictGroup {
  targetName: string;
  targetFreqKhz: number;
  items: ConflictOut[];
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

export default function App() {
  const [text, setText] = useState(EXAMPLE_INPUT);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [errors, setErrors] = useState<ErrorItem[]>([]);
  const [fatal, setFatal] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const groups = useMemo(
    () => (result ? groupByTarget(result.conflicts) : []),
    [result],
  );

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setResult(null);
    setErrors([]);
    setFatal(null);
    setCopied(false);
    try {
      setResult(await analyze(text));
    } catch (err) {
      if (err instanceof InputError) {
        setErrors(err.errors);
      } else {
        setFatal(err instanceof Error ? err.message : "未知错误");
      }
    } finally {
      setLoading(false);
    }
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
        <label htmlFor="channels">频道清单（每行：名称 频率，支持 # 注释）</label>
        <textarea
          id="channels"
          rows={10}
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
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

      {result && (
        <section className="panel">
          {result.conflict_count === 0 ? (
            <h2 className="safe">安全清单：零项冲突（共 {result.channel_count} 个频道）</h2>
          ) : (
            <h2 className="danger">
              发现 {result.conflict_count} 项冲突（共 {result.channel_count} 个频道）
            </h2>
          )}

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
