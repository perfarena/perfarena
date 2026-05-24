import { Fragment, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, BaselineSummary, getApiKey, MePayload, RunPublic, SubmissionDetail, SubmissionPublic } from "../lib/api";
import { ErrorPanel, LogEntryPanel, ProvenancePanel, hasErrorLog, sigBadge } from "../components/AuditPanels";
import {
  formatCoefficient, formatMemory, formatPassAt1, formatPercentile, formatRuntime,
} from "../lib/format";
import LanguageLink from "../components/LanguageLink";

function BaselineSourceChip({ summary }: { summary?: BaselineSummary }) {
  if (!summary || summary.n_used === 0) return null;
  const real = summary.by_source["leetcode-distribution"] ?? 0;
  const syn  = summary.by_source["synthetic"] ?? 0;
  const missing = summary.by_source["missing"] ?? 0;
  const allReal = real > 0 && syn === 0 && missing === 0;
  const fresh = summary.newest_fetched_at
    ? new Date(summary.newest_fetched_at).toLocaleString()
    : null;
  return (
    <div className="card" style={{
      padding: 10,
      borderColor: allReal ? "var(--easy)" : "var(--medium)",
      background: allReal
        ? "rgba(120,200,120,0.08)"
        : "rgba(220,180,80,0.08)",
      fontSize: "0.85rem",
    }}>
      <b>{allReal ? "Real baselines" : "Mixed / synthetic baselines"}</b> ·
      {" "}leetcode-distribution: <b>{real}</b>
      {" "}· synthetic placeholder: <b>{syn}</b>
      {missing > 0 && <> · missing: <b>{missing}</b></>}
      {" "}({summary.n_used} accepted problems in {summary.language})
      {fresh && <> · newest fetched {fresh}</>}
      {!allReal && (
        <div className="muted" style={{ marginTop: 4 }}>
          Coefficients that include synthetic baselines are estimates — run
          {" "}<code>POST /api/admin/baselines/refresh</code> or wait for the
          {" "}auto-refresh trigger to replace them with real LeetCode data.
        </div>
      )}
    </div>
  );
}


function LeaderboardBanner({ s }: { s: SubmissionPublic }) {
  if (s.leaderboard_eligible) {
    return (
      <div className="card" style={{ borderColor: "var(--easy)", background: "rgba(120,200,120,0.08)" }}>
        <b>On the leaderboard.</b> This submission is complete and passes more than 25% of supported problems.
      </div>
    );
  }
  const r = s.leaderboard_reason;
  let msg: string;
  if (r === "incomplete") {
    msg = `Not on the leaderboard yet: this submission needs at least one attempt for every supported problem. Solved ${s.total_solved}/${s.total_problems}; total attempts so far: ${s.n_attempts}. Append the missing slugs via /api/submissions/${s.id}/attempts.`;
  } else if (r === "pass-rate-too-low") {
    const pct = s.total_problems > 0 ? Math.round((s.total_solved / s.total_problems) * 100) : 0;
    msg = `Not on the leaderboard: pass-rate ${pct}% does not exceed the 25% threshold. Fix or re-submit failing problems to raise it.`;
  } else if (r === "not-judged") {
    msg = `Submission is still being judged — it will be evaluated for the leaderboard once dispatch finishes.`;
  } else {
    msg = `Not on the leaderboard.`;
  }
  return (
    <div className="card" style={{ borderColor: "var(--medium)", background: "rgba(220,180,80,0.08)" }}>
      <b>Not on leaderboard.</b> {msg}
    </div>
  );
}

type RowPanel = null | "error" | "provenance" | "log";

function RunPublicRow({ run, trace }: { run: RunPublic; trace?: import("../lib/api").AttemptTrace }) {
  const [open, setOpen] = useState<RowPanel>(null);
  const errLog = run.error_log;
  const hasErr = hasErrorLog(errLog);
  const hasTrace = !!trace && (!!trace.trace || !!trace.inference_trace);
  const pv = trace?.provenance;
  const hasChain = !!pv && pv.log_seq != null;

  function toggle(p: RowPanel) { setOpen(o => (o === p ? null : p)); }

  return (
    <Fragment>
      <tr>
        <td><code>{run.problem_slug}</code></td>
        <td><span className={`badge ${run.status_msg === "Accepted" ? "badge-easy" : "badge-hard"}`}>{run.status_msg}</span></td>
        <td className="metric">{formatRuntime(run.runtime_ms)}</td>
        <td className="metric">{formatMemory(run.memory_mb)}</td>
        <td className="metric">{formatPercentile(run.runtime_percentile)}</td>
        <td className="metric">{formatPercentile(run.memory_percentile)}</td>
        <td className="metric">{run.total_correct ?? "?"} / {run.total_testcases ?? "?"}</td>
        <td>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {hasErr && (
              <button className="secondary"
                      style={open === "error" ? {} : { borderColor: "var(--bad)", color: "var(--bad)" }}
                      onClick={() => toggle("error")}>
                {open === "error" ? "Hide error" : "Error log"}
              </button>
            )}
            {hasTrace && (
              <button className="secondary"
                      onClick={() => toggle("provenance")}>
                {open === "provenance" ? "Hide provenance" : "Provenance"}
              </button>
            )}
            {hasChain && (
              <button className="secondary"
                      onClick={() => toggle("log")}>
                {open === "log" ? "Hide log entry" : `Log entry #${pv?.log_seq}`}
              </button>
            )}
          </div>
        </td>
      </tr>
      {open !== null && (
        <tr>
          <td colSpan={8} style={{
            background: open === "error" ? "rgba(255,80,80,0.06)" : "rgba(127,127,127,0.06)",
          }}>
            {open === "error" && hasErr && <ErrorPanel log={errLog!} />}
            {open === "provenance" && trace && <ProvenancePanel t={trace} />}
            {open === "log" && pv && <LogEntryPanel pv={pv} />}
          </td>
        </tr>
      )}
    </Fragment>
  );
}


export default function SubmissionDetailPage() {
  const { id = "" } = useParams();
  const [d, setD] = useState<SubmissionDetail | null>(null);
  const [me, setMe] = useState<MePayload | null>(null);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  useEffect(() => {
    api.submissionDetail(id).then(setD).catch((e: unknown) => setErr(String(e)));
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
  }, [id]);

  async function onDelete() {
    if (!confirm(`Permanently delete submission #${id} and all its attempts/runs? This can't be undone.`)) return;
    try {
      await api.deleteSubmission(id);
      nav("/me");
    } catch (e: unknown) {
      setErr(String(e));
    }
  }

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;
  const { submission: s } = d;

  return (
    <div className="col">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12, alignItems: "flex-start" }}>
        <div>
          <h1 style={{ marginBottom: 4 }}>{s.model_name} <span className="muted">{s.model_version}</span></h1>
          <div className="muted">
            submitted {new Date(s.created_at).toLocaleString()} ·
            by {d.submitter.org ?? d.submitter.display_name} · <LanguageLink name={s.language} /> ·
            <span className="badge badge-status" style={{ marginLeft: 8 }}>{s.status}</span>
          </div>
        </div>
        {me && (me.role === "admin" || me.display_name === d.submitter.display_name) && (
          <button className="secondary" onClick={onDelete}
                  style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>
            Delete submission
          </button>
        )}
      </div>

      <LeaderboardBanner s={s} />

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label={`k (samples)`} value={String(s.n_samples_per_problem)} />
        <Stat label="pass@1" value={formatPassAt1(s.pass_at_1)} />
        <Stat label="pass@k" value={formatPassAt1(s.pass_at_k)} />
        <Stat label="pass@full" value={formatPassAt1(s.pass_at_full)} />
        <Stat label="solved" value={`${s.total_solved} / ${s.total_problems}`} />
        <Stat label="attempts" value={String(s.n_attempts)} />
        <Stat label="avg runtime" value={formatRuntime(s.avg_runtime_ms)} />
        <Stat label="best runtime" value={formatRuntime(s.best_runtime_ms)} />
        <Stat label="avg memory" value={formatMemory(s.avg_memory_mb)} />
        <Stat label="best memory" value={formatMemory(s.best_memory_mb)} />
        <Stat label="avg runtime %ile" value={formatPercentile(s.avg_runtime_percentile)} />
        <Stat label="avg memory %ile" value={formatPercentile(s.avg_memory_percentile)} />
      </div>

      <p className="muted" style={{ marginTop: -4, fontSize: "0.85rem" }}>
        Every problem is dispatched to LeetCode 5× and all runs are stored.
        “avg” is the mean of each problem's runs; “best” is each problem's fastest /
        leanest run. Submission-level numbers are the mean of those per-problem values
        across accepted problems.
      </p>

      <h2 style={{ marginBottom: 0 }}>vs. human baselines</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        Ratio of human-baseline metric to model metric. <b>&gt; 1.0×</b> means the model
        outperforms the cohort; <b>&lt; 1.0×</b> means it's worse.
      </p>
      <BaselineSourceChip summary={d.baselines} />
      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="time vs human avg" value={formatCoefficient(s.time_human_avg)} />
        <Stat label="time vs human expert" value={formatCoefficient(s.time_human_expert)} />
        <Stat label="memory vs human avg" value={formatCoefficient(s.mem_human_avg)} />
        <Stat label="memory vs human expert" value={formatCoefficient(s.mem_human_expert)} />
      </div>

      <div className="card">
        <h2>Model parameters</h2>
        <pre className="code">{JSON.stringify(d.model_params, null, 2)}</pre>
      </div>

      <div className="card">
        <h2>Prompt template</h2>
        <pre className="code">{d.prompt_template}</pre>
      </div>

      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Problem</th><th>Status</th>
            <th title="LeetCode-reported runtime (ms)">Runtime</th>
            <th title="LeetCode-reported memory (MB)">Memory</th>
            <th>Runtime %ile</th><th>Memory %ile</th><th>Tests</th><th></th>
          </tr></thead>
          <tbody>
            {d.runs.map((r, i) => (
              <RunPublicRow key={i} run={r} trace={d.traces?.[i]} />
            ))}
          </tbody>
        </table>
      </div>

      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Per row above: <b>Error log</b> surfaces LeetCode's traceback / failing
        testcase when a run failed · <b>Provenance</b> shows the exact prompt,
        the model's full response, and its reasoning trace · <b>Log entry</b>
        is that attempt's append-only transparency-log row (sequence, hashes,
        signature). The entries are hash-chained to the previous one, so any
        tampering is detectable.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 140 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
