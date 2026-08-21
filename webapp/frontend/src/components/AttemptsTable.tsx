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

/* Subtle "/op" marker appended to runtime cells when the submission was
 * measured locally (arena-local per-op methodology). Runtime is then time
 * per single solve, not whole-run wall time — so the qualifier is correct
 * only for those rows. Memory is peak on both oracles and never gets it. */
function OpMark({ show }: { show: boolean }) {
  return show
    ? <sup className="muted" style={{ fontSize: "0.68em", marginLeft: 1 }}>/op</sup>
    : null;
}

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
    ? `client-flagged as malformed (${a.prefailed_reason}): recorded as failed without LeetCode dispatch; raw response preserved in trace`
    : undefined;
  return (
    <span className={`badge ${cls}`} title={title}>
      {a.status}{isPrefailed && " ✱"}
    </span>
  );
}

function RunRow({ run, perOp, showTests }: { run: AttemptRunDetail; perOp: boolean; showTests: boolean }) {
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
        <td className="metric">
          {formatRuntime(run.runtime_ms)}
          <OpMark show={perOp && run.runtime_ms != null} />
        </td>
        <td className="metric">{formatMemory(run.memory_mb)}</td>
        <td className="metric">{formatPercentile(run.runtime_percentile)}</td>
        <td className="metric">{formatPercentile(run.memory_percentile)}</td>
        {showTests && (
          <td className="metric">{run.total_correct ?? "?"}/{run.total_testcases ?? "?"}</td>
        )}
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
          Full verbatim payload, including fields the structured log doesn't enumerate.
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

/* Round-chain header rendered inline with the problem slug when the row is
 * part of an N-round refinement chain (--rounds N in matrix_sweep or
 * iterative_refine). Shows the round index + a link to the previous round's
 * attempt so an operator can walk the chain forward-backward and see which
 * failure informed which retry. */
function RoundChainBadge({
  a, chainSize,
}: { a: AttemptResultPayload; chainSize: number }) {
  const roundIdx = a.round_index ?? 0;
  const prevAid = a.previous_attempt_id ?? null;
  if (chainSize <= 1) return null;   // no chain — hide the badge entirely
  const isRefinementStart = roundIdx === 0;
  return (
    <div className="row" style={{ gap: 6, marginTop: 3, alignItems: "center" }}>
      <span className="badge"
            title={
              isRefinementStart
                ? "Round 0 = cold-start attempt. Its verdict + error log get folded into the round-1 prompt."
                : `Round ${roundIdx} was generated after seeing round-${roundIdx - 1}'s LeetCode verdict + error log via {{rounds_history}}.`
            }
            style={{
              fontSize: "0.72rem", padding: "1px 6px",
              background: isRefinementStart ? "var(--panel-2)" : "var(--accent-soft)",
              borderColor: isRefinementStart ? "var(--border)" : "var(--accent)",
            }}>
        round {roundIdx}
      </span>
      {prevAid != null && (
        <span className="muted" style={{ fontSize: "0.72rem" }}>
          ← from #{prevAid}
        </span>
      )}
    </div>
  );
}

function AttemptRow({
  a, showEnergy, showRuntimeEnergy, showMemory, chainSize, perOp,
}: {
  a: AttemptResultPayload;
  showEnergy: boolean;
  showRuntimeEnergy: boolean;
  showMemory: boolean;
  chainSize: number;
  perOp: boolean;
}) {
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
          <RoundChainBadge a={a} chainSize={chainSize} />
        </td>
        <td><StatusBadge a={a} /></td>
        <td className="metric">{a.n_accepted_runs}/{a.n_runs || "–"}</td>
        <td className="metric">
          {formatRuntime(a.avg_runtime_ms)}
          <OpMark show={perOp && a.avg_runtime_ms != null} />
        </td>
        <td className="metric">
          {formatRuntime(a.best_runtime_ms)}
          <OpMark show={perOp && a.best_runtime_ms != null} />
        </td>
        {showMemory && (
          <td className="metric">{formatMemory(a.avg_memory_mb)}</td>
        )}
        {showMemory && (
          <td className="metric">{formatMemory(a.best_memory_mb)}</td>
        )}
        {showRuntimeEnergy && (
          <td className="metric">{formatEnergy(a.avg_runtime_energy_j)}</td>
        )}
        {showRuntimeEnergy && (
          <td className="metric">{formatEnergy(a.best_runtime_energy_j)}</td>
        )}
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
          <td colSpan={10 + (showMemory ? 2 : 0) + (showRuntimeEnergy ? 2 : 0) + (showEnergy ? 1 : 0)} style={{
            background: open === "error" ? "rgba(255,80,80,0.06)" : "rgba(127,127,127,0.06)",
          }}>
            {open === "runs" && (
              <div className="col" style={{ gap: 8 }}>
                <RunsChart runs={a.runs} perOp={perOp} />
                <table className="tbl" style={{ margin: 0 }}>
                  <thead><tr>
                    <th>Run</th><th>Status</th><th>Runtime</th><th>Peak mem</th>
                    <th>RT %ile</th><th>Mem %ile</th>
                    {a.runs.some(r => r.total_correct != null || r.total_testcases != null) && <th>Tests</th>}
                    <th></th>
                  </tr></thead>
                  <tbody>
                    {a.runs.map(r => <RunRow key={r.run_index} run={r} perOp={perOp}
                      showTests={a.runs.some(rr => rr.total_correct != null || rr.total_testcases != null)} />)}
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

export default function AttemptsTable(
  { rows, perOp = false }: { rows: AttemptResultPayload[]; perOp?: boolean },
) {
  if (rows.length === 0) return <div className="empty">No attempts to show.</div>;
  // Generation-energy column (LLM-side) — only rendered when at least one
  // row reports it.
  const showEnergy = rows.some(a => a.generation_energy_j != null);
  // Runtime-energy columns (execution-side, third perf axis) — only rendered
  // when a harness measured it. arena-local imports populate this; LC-only
  // submissions leave every row null and the columns stay hidden.
  const showRuntimeEnergy = rows.some(
    a => a.avg_runtime_energy_j != null || a.best_runtime_energy_j != null,
  );
  // Memory columns render only when at least one row has a value. The
  // arena-local harness doesn't measure memory yet — hiding these prevents
  // rows of "–" placeholders for arena-local imports.
  const showMemory = rows.some(
    a => a.avg_memory_mb != null || a.best_memory_mb != null,
  );
  // How many attempts share each problem_slug — > 1 means the slug is part
  // of an N-round refinement chain, so the row's `RoundChainBadge` should
  // render. Single-attempt slugs keep the classic flat layout (no badge).
  const chainSize: Record<string, number> = {};
  for (const a of rows) {
    chainSize[a.problem_slug] = (chainSize[a.problem_slug] ?? 0) + 1;
  }
  const anyChain = Object.values(chainSize).some(n => n > 1);
  // Sort order:
  //  • If ANY slug is a chain: group by problem_slug so the chain reads as
  //    a single vertical block, and within a chain order by round_index
  //    ASCENDING (round 0 first, then round 1, then round 2 …) so the
  //    refinement reads top-to-bottom. Groups themselves are ordered by
  //    the newest attempt inside them so recent activity floats up.
  //  • Otherwise: keep the legacy newest-first flat ordering by
  //    submitted_at → attempt_id (both monotonic with creation).
  //
  // Sort on a defensive COPY so the parent's rows array stays untouched
  // (React state immutability + downstream memoisation).
  const sorted = [...rows].sort((a, b) => {
    if (anyChain) {
      if (a.problem_slug !== b.problem_slug) {
        // Order groups by the newest attempt inside each — recency for the
        // group as a whole, not per-round.
        const newestForGroup = (slug: string): number => {
          let t = 0;
          for (const r of rows) {
            if (r.problem_slug !== slug) continue;
            const rt = r.submitted_at ? Date.parse(r.submitted_at) : r.attempt_id;
            if (rt > t) t = rt;
          }
          return t;
        };
        return newestForGroup(b.problem_slug) - newestForGroup(a.problem_slug);
      }
      // Same slug → chain order (round 0 first).
      const ra = a.round_index ?? 0;
      const rb = b.round_index ?? 0;
      if (ra !== rb) return ra - rb;
      return a.attempt_id - b.attempt_id;
    }
    const ta = a.submitted_at ? Date.parse(a.submitted_at) : 0;
    const tb = b.submitted_at ? Date.parse(b.submitted_at) : 0;
    if (ta !== tb) return tb - ta;
    return b.attempt_id - a.attempt_id;
  });
  return (
    <div className="card tbl-scroll" style={{ padding: 0 }}>
      <table className="tbl">
        <thead><tr>
          <th>Problem</th><th>Status</th>
          <th title="accepted runs / total dispatch runs">Runs ✓</th>
          <th title="mean over accepted runs">Avg runtime</th>
          <th title="fastest single run">Best runtime</th>
          {showMemory && <th title="mean peak (max resident) memory over accepted runs, peak on both oracles, never per-op">Peak mem</th>}
          {showMemory && <th title="leanest single run's peak memory">Best peak mem</th>}
          {showRuntimeEnergy && (
            <th title="Runtime energy (Joules): execution energy consumed while running this solution. Reported by execution-instrumented harnesses (e.g. arena-local). Lower is better.">Avg energy</th>
          )}
          {showRuntimeEnergy && (
            <th title="Best (least) runtime energy across dispatch runs. Same source as Avg energy.">Best energy</th>
          )}
          <th title="avg runtime / memory percentile">%ile (rt/mem)</th>
          <th title="$ cost of this LLM call (one sample)">Cost</th>
          <th title="code-only response tokens (estimate)">Tokens</th>
          <th title="generation wall-clock for this solution">Gen time</th>
          {showEnergy && <th title="generation energy for this solution (Joules)">Gen energy</th>}
          <th></th>
        </tr></thead>
        <tbody>
          {sorted.map(a => (
            <AttemptRow key={a.attempt_id} a={a}
                        showEnergy={showEnergy}
                        showRuntimeEnergy={showRuntimeEnergy}
                        showMemory={showMemory}
                        perOp={perOp}
                        chainSize={chainSize[a.problem_slug] ?? 1} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
