import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { AttemptResultPayload, AttemptRunDetail } from "../lib/api";
import { formatDuration, formatEnergy, formatMemory, formatPercentile, formatRuntime } from "../lib/format";
import { ErrorPanel, LogEntryPanel, ProvenancePanel, hasErrorLog } from "./AuditPanels";
import { RunsChart } from "./RunsChart";

/* Shared per-attempt table — the full breakdown of one submission's attempts,
 * each row expandable to the per-run table + the RunsChart (runtime/memory
 * spread across the N dispatch repeats), plus error log / provenance / log
 * entry panels.
 *
 * Used by both /submit/:id/attempts (AttemptResults, gated owner view) and
 * /submissions/:id (SubmissionDetail, public view) so the chart + run
 * breakdown render identically in both places. Pure presentational — the
 * host page supplies the rows (from /api/submissions/:id/attempts) and any
 * surrounding filters / stats. */

export type StatusFilter = "all" | "accepted" | "rejected" | "queued";

export function bucket(a: AttemptResultPayload): Exclude<StatusFilter, "all"> {
  if (a.accepted) return "accepted";
  if (a.status === "queued") return "queued";
  return "rejected";
}

function StatusBadge({ a }: { a: AttemptResultPayload }) {
  const b = bucket(a);
  const cls = b === "accepted" ? "badge-easy" : b === "queued" ? "badge-medium" : "badge-hard";
  // Prefailed rows are client-flagged failures (wrong language extracted, no
  // code, etc.) — recorded without LeetCode dispatch. Mark with ✱ + tooltip
  // so users can tell them apart from real LC verdicts.
  const isPrefailed = !!a.prefailed_reason;
  const title = isPrefailed
    ? `client-flagged as malformed (${a.prefailed_reason}) — recorded as failed without LeetCode dispatch; raw response preserved in trace`
    : undefined;
  return (
    <span className={`badge ${cls}`} title={title}>
      {a.status}{isPrefailed && " ✱"}
    </span>
  );
}

function RunRow({ run }: { run: AttemptRunDetail }) {
  const [open, setOpen] = useState(false);
  const log = run.error_log;
  const showLog = hasErrorLog(log);
  // Always offer the panel when we have ANY diagnostic — the structured log
  // covers enumerated error fields, but a non-Accepted run with no
  // recognised fields still carries the verbatim LC response. Skipping the
  // toggle on those rows used to hide the only evidence we had.
  const showRaw = !!run.leetcode_full_response;
  const canExpand = showLog || showRaw;
  return (
    <Fragment>
      <tr>
        <td className="metric">#{run.run_index}</td>
        <td>
          <span className={`badge ${run.status_msg === "Accepted" ? "badge-easy" : "badge-hard"}`}>
            {run.status_msg}
          </span>
        </td>
        <td className="metric">{formatRuntime(run.runtime_ms)}</td>
        <td className="metric">{formatMemory(run.memory_mb)}</td>
        <td className="metric">{formatPercentile(run.runtime_percentile)}</td>
        <td className="metric">{formatPercentile(run.memory_percentile)}</td>
        <td className="metric">{run.total_correct ?? "?"}/{run.total_testcases ?? "?"}</td>
        <td>
          {canExpand && (
            <button className="secondary" onClick={() => setOpen(o => !o)}>
              {open ? "Hide log" : "Show log"}
            </button>
          )}
        </td>
      </tr>
      {open && canExpand && (
        <tr>
          <td colSpan={8} style={{ background: "rgba(255,80,80,0.06)" }}>
            {showLog && <ErrorPanel log={log!} />}
            {showRaw && (
              <RawLeetCodeResponse data={run.leetcode_full_response!}
                                   alreadyHasStructuredLog={showLog} />
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

/* Verbatim LeetCode /check/ payload — collapsed by default (it can be a few
 * KB on noisy errors) but always one click away. Renders as pretty JSON
 * because we can't predict every field shape LeetCode might ship. */
function RawLeetCodeResponse({
  data, alreadyHasStructuredLog,
}: { data: Record<string, unknown>; alreadyHasStructuredLog: boolean }) {
  const [open, setOpen] = useState(!alreadyHasStructuredLog);
  return (
    <div className="col" style={{ gap: 6, marginTop: alreadyHasStructuredLog ? 12 : 0 }}>
      <div className="row" style={{ gap: 6, alignItems: "center" }}>
        <button className="secondary" onClick={() => setOpen(o => !o)} aria-expanded={open}>
          {open ? "Hide" : "Show"} raw LeetCode response
        </button>
        <span className="muted" style={{ fontSize: "0.78rem" }}>
          Full verbatim payload — includes fields the structured log doesn't enumerate.
        </span>
      </div>
      {open && (
        <pre className="code"
             style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
                      maxHeight: 360, overflow: "auto", fontSize: "0.78rem" }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

type AttemptPanel = null | "runs" | "error" | "provenance" | "log";

function AttemptRow({ a, showEnergy }: { a: AttemptResultPayload; showEnergy: boolean }) {
  const [open, setOpen] = useState<AttemptPanel>(null);
  // "Error log" at the attempt level surfaces the LAST run's error (the most
  // recent dispatch). Per-run errors are still reachable through the runs
  // expander's own RunRow buttons.
  const lastRunErr = a.runs.length ? a.runs[a.runs.length - 1].error_log : null;
  const hasErr = hasErrorLog(lastRunErr) && !a.accepted;
  const hasTrace = !!a.trace || !!a.inference_trace;
  const pv = a.provenance ?? null;
  const hasChain = !!pv && pv.log_seq != null;

  function toggle(p: AttemptPanel) { setOpen(o => (o === p ? null : p)); }

  return (
    <Fragment>
      <tr>
        <td>
          <Link to={`/problems/${a.problem_slug}`}><code>{a.problem_slug}</code></Link>
          <div className="muted" style={{ fontSize: "0.75rem" }}>{a.level}</div>
        </td>
        <td><StatusBadge a={a} /></td>
        <td className="metric">{a.n_accepted_runs}/{a.n_runs || "–"}</td>
        <td className="metric">{formatRuntime(a.avg_runtime_ms)}</td>
        <td className="metric">{formatRuntime(a.best_runtime_ms)}</td>
        <td className="metric">{formatMemory(a.avg_memory_mb)}</td>
        <td className="metric">{formatMemory(a.best_memory_mb)}</td>
        <td className="metric">
          {formatPercentile(a.avg_runtime_percentile)} / {formatPercentile(a.avg_memory_percentile)}
        </td>
        <td className="metric" title="$ cost of this LLM call (0 for Ollama)">
          {a.cost_usd == null ? "–" : `$${a.cost_usd.toFixed(4)}`}
        </td>
        <td className="metric" title="code-only response tokens (estimate)">
          {a.response_tokens == null ? "–" : a.response_tokens.toLocaleString()}
        </td>
        <td className="metric" title="generation wall-clock for this solution">
          {formatDuration(a.wall_clock_ms)}
        </td>
        {showEnergy && (
          <td className="metric" title="generation energy for this solution (Joules)">
            {formatEnergy(a.generation_energy_j)}
          </td>
        )}
        <td>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {a.runs.length > 0 && (
              <button className="secondary" onClick={() => toggle("runs")}>
                {open === "runs" ? "Hide runs" : `${a.runs.length} runs`}
              </button>
            )}
            {hasErr && (
              <button className="secondary"
                      style={open === "error" ? {} : { borderColor: "var(--bad)", color: "var(--bad)" }}
                      onClick={() => toggle("error")}>
                {open === "error" ? "Hide error" : "Error log"}
              </button>
            )}
            {hasTrace && (
              <button className="secondary" onClick={() => toggle("provenance")}>
                {open === "provenance" ? "Hide provenance" : "Provenance"}
              </button>
            )}
            {hasChain && (
              <button className="secondary" onClick={() => toggle("log")}>
                {open === "log" ? "Hide log entry" : `Log entry #${pv?.log_seq}`}
              </button>
            )}
          </div>
        </td>
      </tr>
      {open !== null && (
        <tr>
          <td colSpan={showEnergy ? 13 : 12} style={{
            background: open === "error" ? "rgba(255,80,80,0.06)" : "rgba(127,127,127,0.06)",
          }}>
            {open === "runs" && (
              <div className="col" style={{ gap: 8 }}>
                <RunsChart runs={a.runs} />
                <table className="tbl" style={{ margin: 0 }}>
                  <thead><tr>
                    <th>Run</th><th>Status</th><th>Runtime</th><th>Memory</th>
                    <th>RT %ile</th><th>Mem %ile</th><th>Tests</th><th></th>
                  </tr></thead>
                  <tbody>
                    {a.runs.map(r => <RunRow key={r.run_index} run={r} />)}
                  </tbody>
                </table>
              </div>
            )}
            {open === "error" && hasErr && <ErrorPanel log={lastRunErr!} />}
            {open === "provenance" && (
              <ProvenancePanel t={{ trace: a.trace, inference_trace: a.inference_trace ?? "", provenance: pv }} />
            )}
            {open === "log" && pv && <LogEntryPanel pv={pv} />}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function AttemptsTable({ rows }: { rows: AttemptResultPayload[] }) {
  if (rows.length === 0) return <div className="empty">No attempts to show.</div>;
  // Energy column appears only when at least one attempt reports a measurement.
  // LeetCode-only data paths leave every row null and the column stays hidden.
  const showEnergy = rows.some(a => a.generation_energy_j != null);
  return (
    <div className="card tbl-scroll" style={{ padding: 0 }}>
      <table className="tbl">
        <thead><tr>
          <th>Problem</th><th>Status</th>
          <th title="accepted runs / total dispatch runs">Runs ✓</th>
          <th title="mean over accepted runs">Avg runtime</th>
          <th title="fastest single run">Best runtime</th>
          <th title="mean over accepted runs">Avg memory</th>
          <th title="leanest single run">Best memory</th>
          <th title="avg runtime / memory percentile">%ile (rt/mem)</th>
          <th title="$ cost of this LLM call (one sample)">Cost</th>
          <th title="code-only response tokens (estimate)">Tokens</th>
          <th title="generation wall-clock for this solution">Gen time</th>
          {showEnergy && <th title="generation energy for this solution (Joules)">Gen energy</th>}
          <th></th>
        </tr></thead>
        <tbody>
          {rows.map(a => <AttemptRow key={a.attempt_id} a={a} showEnergy={showEnergy} />)}
        </tbody>
      </table>
    </div>
  );
}
