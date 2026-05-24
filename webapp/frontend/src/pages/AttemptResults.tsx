import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, AttemptResultPayload, AttemptRunDetail, SubmissionDetail } from "../lib/api";
import { formatMemory, formatPercentile, formatRuntime } from "../lib/format";
import {
  ErrorPanel, LogEntryPanel, ProvenancePanel, hasErrorLog,
} from "../components/AuditPanels";

function RunRow({ run }: { run: AttemptRunDetail }) {
  const [open, setOpen] = useState(false);
  const log = run.error_log;
  const showLog = hasErrorLog(log);
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
          {showLog && (
            <button className="secondary" onClick={() => setOpen(o => !o)}>
              {open ? "Hide log" : "Show log"}
            </button>
          )}
        </td>
      </tr>
      {open && showLog && (
        <tr>
          <td colSpan={8} style={{ background: "rgba(255,80,80,0.06)" }}>
            <ErrorPanel log={log!} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

type AttemptPanel = null | "runs" | "error" | "provenance" | "log";

function AttemptRow({ a }: { a: AttemptResultPayload }) {
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
          <td colSpan={9} style={{
            background: open === "error" ? "rgba(255,80,80,0.06)" : "rgba(127,127,127,0.06)",
          }}>
            {open === "runs" && (
              <table className="tbl" style={{ margin: 0 }}>
                <thead><tr>
                  <th>Run</th><th>Status</th><th>Runtime</th><th>Memory</th>
                  <th>RT %ile</th><th>Mem %ile</th><th>Tests</th><th></th>
                </tr></thead>
                <tbody>
                  {a.runs.map(r => <RunRow key={r.run_index} run={r} />)}
                </tbody>
              </table>
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

type StatusFilter = "all" | "accepted" | "rejected" | "queued";

function bucket(a: AttemptResultPayload): Exclude<StatusFilter, "all"> {
  if (a.accepted) return "accepted";
  if (a.status === "queued") return "queued";
  return "rejected";
}

function StatusBadge({ a }: { a: AttemptResultPayload }) {
  const b = bucket(a);
  const cls = b === "accepted" ? "badge-easy" : b === "queued" ? "badge-medium" : "badge-hard";
  return <span className={`badge ${cls}`}>{a.status}</span>;
}

export default function AttemptResults() {
  const { id = "" } = useParams();
  const [rows, setRows] = useState<AttemptResultPayload[] | null>(null);
  const [meta, setMeta] = useState<SubmissionDetail | null>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [q, setQ] = useState("");

  async function refresh() {
    try {
      const [r, d] = await Promise.all([
        api.submissionAttempts(id),
        api.submissionDetail(id),
      ]);
      setRows(r); setMeta(d);
    } catch (e: unknown) {
      setErr(String(e));
    }
  }
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [id]);

  const counts = useMemo(() => {
    const c = { accepted: 0, rejected: 0, queued: 0 };
    (rows ?? []).forEach(a => { c[bucket(a)] += 1; });
    return c;
  }, [rows]);

  if (err && !rows) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!rows) return <div className="empty">Loading…</div>;

  const visible = rows.filter(a =>
    (filter === "all" || bucket(a) === filter) &&
    (!q || a.problem_slug.includes(q.toLowerCase()) || a.problem_title.toLowerCase().includes(q.toLowerCase()))
  );
  const s = meta?.submission;

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>
          Attempt results
          {s && <span className="muted"> · {s.model_name} {s.model_version}</span>}
        </h1>
        <p className="muted">
          Submission #{id}{s ? ` · ${s.language}` : ""} ·{" "}
          <Link to={`/submit/${id}`}>← back to progress</Link> ·{" "}
          <Link to={`/submissions/${id}`}>public view →</Link>
        </p>
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="attempts" value={String(rows.length)} />
        <Stat label="accepted" value={String(counts.accepted)} />
        <Stat label="rejected" value={String(counts.rejected)} />
        <Stat label="queued" value={String(counts.queued)} />
      </div>

      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {(["all", "accepted", "rejected", "queued"] as StatusFilter[]).map(f => (
            <button
              key={f}
              className={filter === f ? "" : "secondary"}
              onClick={() => setFilter(f)}
            >
              {f}{f !== "all" && ` (${counts[f]})`}
            </button>
          ))}
        </div>
        <div className="row" style={{ gap: 8 }}>
          <input placeholder="Filter problem…" value={q}
                 onChange={e => setQ(e.target.value)} style={{ minWidth: 220 }} />
          <button className="secondary" onClick={refresh}>Refresh</button>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="empty">No attempts match.</div>
      ) : (
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
              <th></th>
            </tr></thead>
            <tbody>
              {visible.map(a => (
                <AttemptRow key={a.attempt_id} a={a} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 120 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
