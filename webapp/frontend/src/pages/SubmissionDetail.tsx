import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, AttemptResultPayload, BaselineSummary, getApiKey, MePayload, SubmissionDetail, SubmissionPublic } from "../lib/api";
import {
  formatCoefficient, formatDuration, formatEnergy, formatMemory, formatPassAt1, formatPercentile, formatRuntime,
} from "../lib/format";
import LanguageLink from "../components/LanguageLink";
import AttemptsTable from "../components/AttemptsTable";

function BaselineSourceChip({
  summary, isAdmin, onRefreshed,
}: {
  summary?: BaselineSummary;
  isAdmin: boolean;
  onRefreshed: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  if (!summary || summary.n_used === 0) return null;
  const real = summary.by_source["leetcode-distribution"] ?? 0;
  const syn  = summary.by_source["synthetic"] ?? 0;
  const missing = summary.by_source["missing"] ?? 0;
  const allReal = real > 0 && syn === 0 && missing === 0;
  const fresh = summary.newest_fetched_at
    ? new Date(summary.newest_fetched_at).toLocaleString()
    : null;

  async function refresh() {
    setBusy(true); setNote("");
    try {
      const r = await api.refreshBaselines();
      setNote(`Backfilled: ${r.real} real · ${r.synthetic_placeholders} synthetic · ${r.already_real} already real.`);
      onRefreshed();   // reload the submission so the chip reflects new sources
    } catch (e: unknown) { setNote(String(e)); }
    finally { setBusy(false); }
  }

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
          Coefficients that include synthetic baselines are estimates. New
          submissions auto-seed a baseline for their (language, problem) pair;
          the auto-refresher upgrades placeholders to real LeetCode data when
          an accepted submission + credentials are available.
          {isAdmin && (
            <div className="row" style={{ gap: 8, marginTop: 6, alignItems: "center" }}>
              <button className="secondary" disabled={busy} onClick={refresh}>
                {busy ? "Refreshing…" : "Refresh baselines now"}
              </button>
              {note && <span className="muted">{note}</span>}
            </div>
          )}
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

export default function SubmissionDetailPage() {
  const { id = "" } = useParams();
  const [d, setD] = useState<SubmissionDetail | null>(null);
  const [attempts, setAttempts] = useState<AttemptResultPayload[] | null>(null);
  const [me, setMe] = useState<MePayload | null>(null);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  function loadDetail() {
    api.submissionDetail(id).then(setD).catch((e: unknown) => setErr(String(e)));
  }

  useEffect(() => {
    loadDetail();
    // Per-attempt detail (with the full N-run arrays) drives the shared
    // AttemptsTable + RunsChart — same component the gated /submit/:id/attempts
    // page uses. The endpoint is public, so it works on this public page too.
    api.submissionAttempts(id).then(setAttempts).catch(() => setAttempts([]));
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        {/* Delete is now admin-only: the platform is curated, so mutating the
            submission set is reserved for the team. The owner-can-delete path
            was retired alongside self-submission. */}
        {me?.role === "admin" && (
          <button className="secondary" onClick={onDelete}
                  style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>
            Delete submission
          </button>
        )}
      </div>

      <LeaderboardBanner s={s} />

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label={`k (samples)`} value={String(s.n_samples_per_problem)} />
        <Stat label="pass@1" tip="Chen unbiased estimator at k=1, averaged over ALL supported problems (unattempted problems contribute 0). Single-shot accuracy across the full problem set." value={formatPassAt1(s.pass_at_1)} />
        <Stat label="pass@k" tip="Mean acceptance rate across submitted attempts: total accepted / total submitted. Only divides by attempts that were actually shipped — partial submissions aren't penalised for unattempted problems." value={formatPassAt1(s.pass_at_k)} />
        <Stat label="pass@full" tip="Best-of-k per problem, averaged over all supported problems. Fraction of the full set the submitter solved AT LEAST ONCE across their k samples." value={formatPassAt1(s.pass_at_full)} />
        <Stat label="solved" value={`${s.total_solved} / ${s.total_problems}`} />
        <Stat label="attempts" value={String(s.n_attempts)} />
        <Stat label="total cost"
              tip="Sum of the $ cost of every LLM call in this submission (k samples × problems × rounds). 0 for Ollama / local models; blank if not reported."
              value={s.total_cost_usd == null ? "–" : `$${s.total_cost_usd.toFixed(4)}`} />
        <Stat label="code tokens"
              tip="Sum of code-only response tokens across all attempts (excludes reasoning). Best-effort estimate from the submitting client."
              value={s.total_response_tokens == null ? "–" : s.total_response_tokens.toLocaleString()} />
        <Stat label="total gen time"
              tip="Sum of the generation wall-clock of every solution in this submission (k samples × problems × rounds). Blank if not reported."
              value={formatDuration(s.total_generation_ms)} />
        {s.total_generation_energy_j != null && (
          <Stat label="total gen energy"
                tip="Sum of generation energy across all attempts (Joules). Reported only when the submit client has a power probe; omitted when no data."
                value={formatEnergy(s.total_generation_energy_j)} />
        )}
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

      <h2 style={{ marginBottom: 0 }}>vs. LeetCode baselines</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        Ratio of the LeetCode population baseline to the model metric. <b>&gt; 1.0×</b> means
        the model outperforms the cohort; <b>&lt; 1.0×</b> means it's worse. The cohort
        is everyone who's submitted on LeetCode for that problem in the same language —
        humans <em>and</em> AI-assisted submissions, since LeetCode doesn't separate them.
      </p>
      <BaselineSourceChip summary={d.baselines} isAdmin={me?.role === "admin"}
                          onRefreshed={loadDetail} />
      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="time vs LeetCode avg" value={formatCoefficient(s.time_human_avg)} />
        <Stat label="time vs LeetCode expert" value={formatCoefficient(s.time_human_expert)} />
        <Stat label="memory vs LeetCode avg" value={formatCoefficient(s.mem_human_avg)} />
        <Stat label="memory vs LeetCode expert" value={formatCoefficient(s.mem_human_expert)} />
      </div>

      <div className="card">
        <h2>Model parameters</h2>
        <pre className="code">{JSON.stringify(d.model_params, null, 2)}</pre>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
          <h2 style={{ margin: 0 }}>Prompt template</h2>
          {s.prompt_id != null && s.prompt_name ? (
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              from registry:{" "}
              <Link to={`/prompts/${s.prompt_id}`}><b><code>{s.prompt_name}</code></b></Link>
            </span>
          ) : (
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              ad-hoc (not from registry)
            </span>
          )}
        </div>
        <p className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
          The exact text snapshot persisted at submission time — if the registry's
          template was edited later, this stays the version the model saw.
        </p>
        <pre className="code">{d.prompt_template}</pre>
      </div>

      {attempts === null
        ? <div className="empty">Loading attempts…</div>
        : <AttemptsTable rows={attempts} />}

      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Each row is one attempt. Click <b>N runs</b> to see the per-run
        runtime/memory chart across the dispatch repeats and the full run
        table · <b>Error log</b> surfaces LeetCode's traceback / failing
        testcase when a run failed · <b>Provenance</b> shows the exact prompt,
        the model's full response, and its reasoning trace · <b>Log entry</b>
        is that attempt's append-only transparency-log row (sequence, hashes,
        signature). The entries are hash-chained to the previous one, so any
        tampering is detectable.
      </p>
    </div>
  );
}

function Stat({ label, value, tip }: { label: string; value: string; tip?: string }) {
  return (
    <div className="card" style={{ minWidth: 140 }} title={tip}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
