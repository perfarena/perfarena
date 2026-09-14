import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, CorrectnessOracleDetail } from "../lib/api";
import { formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";
import { SHOW_RELATIVE_METRICS } from "../lib/flags";
import LanguageLink from "../components/LanguageLink";

/* /correctness-oracles/:slug — the correctness half of the two-oracle
 * model. Identity + workflow (how it decides Accepted vs the failure
 * verdicts) + which competitions default to it + top-5 models judged
 * under it. Mirrors /harnesses/:slug (perf side) so the two feel like
 * siblings. */
export default function CorrectnessOracleDetailPage() {
  const { slug = "" } = useParams();
  const [d, setD] = useState<CorrectnessOracleDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.correctnessOracle(slug).then(setD).catch((e: unknown) => setErr(String(e)));
  }, [slug]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;

  return (
    <div className="col">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1 style={{ marginBottom: 4 }}>{d.name}</h1>
        <div className="muted">
          <code>{d.slug}</code> · {d.kind} ·{" "}
          <span className={`badge ${d.status === "active" ? "badge-easy" : "badge-medium"}`}
                style={{ fontSize: "0.75rem" }}>{d.status}</span>
        </div>
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="competitions" value={String(d.n_competitions)}
              tip={d.competitions.map(c => c.slug).join(", ") || "no competitions bound to this correctness oracle yet"} />
        <Stat label="submissions" value={String(d.n_submissions)} />
        <Stat label="runs"        value={String(d.n_runs)}
              tip="Total Run rows tagged with this correctness oracle. Counts every dispatched attempt, including repeated k-samples." />
        <Stat label="languages"   value={String(d.languages.length)}
              tip={d.languages.join(", ") || "no submissions on this correctness oracle yet"} />
      </div>

      {/* Workflow — the description is where the operator explains HOW this
          harness scores a solution (what it measures, where the code runs,
          what the numbers mean). Config JSON is the machine-readable side. */}
      <div className="card col" style={{ gap: 12 }}>
        <h2 style={{ margin: 0 }}>Workflow</h2>
        {d.description
          ? <div style={{ whiteSpace: "pre-wrap", maxWidth: 760 }}>{d.description}</div>
          : <div className="muted" style={{ fontStyle: "italic" }}>
              No description yet. An admin can use the edit form to document
              what this correctness oracle checks and how it decides a verdict.
            </div>}
        {d.config && Object.keys(d.config).length > 0 && (
          <div className="col" style={{ gap: 6 }}>
            <div className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>
              Config
            </div>
            <pre style={{
              margin: 0, padding: 12, background: "var(--panel-2)",
              border: "1px solid var(--border)", borderRadius: 4,
              fontSize: "0.82rem", overflowX: "auto",
            }}><code>{JSON.stringify(d.config, null, 2)}</code></pre>
          </div>
        )}
      </div>

      <h2 style={{ marginBottom: 4 }}>Competitions on this correctness oracle</h2>
      {d.competitions.length === 0 ? (
        <div className="empty">No competitions default to this correctness oracle yet.</div>
      ) : (
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {d.competitions.map(c => (
            <Link key={c.slug} to={`/competitions/${c.slug}`}
                  className="badge" style={{ fontSize: "0.85rem" }}>
              {c.name}
            </Link>
          ))}
        </div>
      )}

      <h2 style={{ marginBottom: 4 }}>Top models</h2>
      {d.top_models.length === 0 ? (
        <div className="empty">No scored submissions on this correctness oracle yet.</div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>#</th><th>Model</th><th>Version</th>
              <th title="Best pass@1 this model achieved on any submission scored by this correctness oracle.">pass@1</th>
              <th title="Best pass@k across this model's submissions on this correctness oracle.">pass@k</th>
              <th title="Average runtime across accepted attempts scored by this correctness oracle.">Avg runtime</th>
              <th title="Average peak (max resident) memory across accepted attempts scored by this correctness oracle. Peak on both oracles, never per function call.">Peak mem</th>
              {SHOW_RELATIVE_METRICS && <th title="Average runtime percentile across accepted attempts.">Runtime %ile</th>}
              {SHOW_RELATIVE_METRICS && <th title="Average memory percentile across accepted attempts.">Memory %ile</th>}
              <th title="Total submissions this model has on this correctness oracle.">subs</th>
            </tr></thead>
            <tbody>
              {d.top_models.map((m, i) => (
                <tr key={`${m.model_name}-${m.model_version}`}>
                  <td className="metric">{i + 1}</td>
                  <td><Link to={`/models/${encodeURIComponent(m.model_name)}`}><b>{m.model_name}</b></Link></td>
                  <td className="muted">{m.model_version}</td>
                  <td className="metric">{formatPassAt1(m.best_pass_at_1)}</td>
                  <td className="metric">{formatPassAt1(m.pass_at_k)}</td>
                  <td className="metric">{formatRuntime(m.avg_runtime_ms ?? null)}</td>
                  <td className="metric">{formatMemory(m.avg_memory_mb ?? null)}</td>
                  {SHOW_RELATIVE_METRICS && <td className="metric">{formatPercentile(m.avg_runtime_percentile ?? null)}</td>}
                  {SHOW_RELATIVE_METRICS && <td className="metric">{formatPercentile(m.avg_memory_percentile ?? null)}</td>}
                  <td className="metric">{m.n_submissions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d.languages.length > 0 && (
        <div className="muted" style={{ fontSize: "0.9rem" }}>
          Languages with submissions on this correctness oracle:{" "}
          {d.languages.map((l, i) => (
            <span key={l}>{i > 0 && ", "}<LanguageLink name={l} /></span>
          ))}
        </div>
      )}

      {/* Cross-link to the filtered leaderboard so the user can drill from
          the harness's headline stats into the ranked list. */}
      <div className="row" style={{ gap: 6, marginTop: 4 }}>
        <Link to={`/rankings?correctness_oracle=${encodeURIComponent(d.slug)}`}>
          <button className="secondary">See full leaderboard on this correctness oracle →</button>
        </Link>
      </div>
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
