import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, LanguageDetailPayload } from "../lib/api";
import { formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";

export default function LanguageDetail() {
  const { name = "" } = useParams();
  const [d, setD] = useState<LanguageDetailPayload | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.languageDetail(name).then(setD).catch((e: unknown) => setErr(String(e)));
  }, [name]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;

  const { info, stats, top_models } = d;

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>{info.display_name}</h1>
        <p>{info.category}</p>
      </div>

      <section className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="submissions" value={stats.n_submissions.toString()} />
        <Stat label="fully accepted" value={stats.n_fully_accepted.toString()} />
        <Stat label="distinct models" value={stats.n_distinct_models.toString()} />
        <Stat label="distinct submitters" value={stats.n_distinct_submitters.toString()} />
      </section>

      <section className="row" style={{ gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div className="card col" style={{ flex: "1 1 360px" }}>
          <h2 style={{ marginTop: 0 }}>Language properties</h2>
          <KV label="Paradigms" value={info.paradigms.join(", ") || "–"} />
          <KV label="Typing" value={info.typing} />
          <KV label="Memory model" value={info.gc} />
          <KV label="Runtime" value={info.runtime} />
        </div>

        <div className="card col" style={{ flex: "1 1 280px" }}>
          <h2 style={{ marginTop: 0 }}>Surveys</h2>
          <KV label="Stack Overflow 2024" value={info.so_used_pct_2024 == null ? "–" : `${info.so_used_pct_2024.toFixed(1)}% used`} />
          <KV label="SO admired (2024)" value={info.so_admired_pct_2024 == null ? "–" : `${info.so_admired_pct_2024.toFixed(0)}%`} />
          <KV label="TIOBE 2025 rank" value={info.tiobe_rank_2025 == null ? "–" : `#${info.tiobe_rank_2025}`} />
          <div className="muted" style={{ fontSize: "0.78rem", marginTop: 8 }}>
            Approximate figures from public developer surveys (Stack Overflow 2024, TIOBE 2025).
          </div>
        </div>

        <div className="card col" style={{ flex: "1 1 280px" }}>
          <h2 style={{ marginTop: 0 }}>Performance footprint</h2>
          <KV label="Cold start" value={info.cold_start} />
          <KV label="Peak speed" value={info.peak_speed} />
          <KV label="Memory baseline" value={info.memory_baseline} />
        </div>
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Design notes</h2>
        <p style={{ marginTop: 0, lineHeight: 1.65 }}>{info.design_notes}</p>
        {info.key_traits.length > 0 && (
          <ul style={{ lineHeight: 1.6 }}>
            {info.key_traits.map(t => <li key={t}>{t}</li>)}
          </ul>
        )}
      </section>

      <section>
        <h2>Best of this language on the leaderboard</h2>
        <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
          <Stat label="best pass@1" value={formatPassAt1(stats.best.pass_at_1)} />
          <Stat label="lowest avg runtime" value={formatRuntime(stats.best.avg_runtime_ms)} />
          <Stat label="lowest best runtime" value={formatRuntime(stats.best.best_runtime_ms)} />
          <Stat label="lowest avg memory" value={formatMemory(stats.best.avg_memory_mb)} />
          <Stat label="lowest best memory" value={formatMemory(stats.best.best_memory_mb)} />
          <Stat label="best runtime %ile" value={formatPercentile(stats.best.avg_runtime_percentile)} />
          <Stat label="best memory %ile" value={formatPercentile(stats.best.avg_memory_percentile)} />
        </div>
      </section>

      <section className="col">
        <h2>Top models in {info.display_name}</h2>
        {top_models.length === 0 ? (
          <div className="empty">No judged submissions in this language yet.</div>
        ) : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>#</th><th>Model</th><th>Submitter</th>
                <th>pass@1</th><th>Avg runtime</th><th>Avg memory</th>
                <th>Runtime %ile</th><th>Memory %ile</th><th></th>
              </tr></thead>
              <tbody>
                {top_models.map((m, i) => (
                  <tr key={m.submission_id}>
                    <td>{i + 1}</td>
                    <td>
                      <Link to={`/models/${encodeURIComponent(m.model_name)}`}>
                        <b>{m.model_name}</b>
                      </Link>
                      <span className="muted"> {m.model_version}</span>
                    </td>
                    <td><Link to={`/submitters/${m.submitter_slug}`}>{m.submitter}</Link></td>
                    <td className="metric">{formatPassAt1(m.pass_at_1)}</td>
                    <td className="metric">{formatRuntime(m.avg_runtime_ms)}</td>
                    <td className="metric">{formatMemory(m.avg_memory_mb)}</td>
                    <td className="metric">{formatPercentile(m.avg_runtime_percentile)}</td>
                    <td className="metric">{formatPercentile(m.avg_memory_percentile)}</td>
                    <td><Link to={`/submissions/${m.submission_id}`}>details →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="row" style={{ gap: 12, padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
      <div className="muted" style={{ minWidth: 140 }}>{label}</div>
      <div style={{ flex: 1 }}>{value}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 160 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
