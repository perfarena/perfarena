import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, CompetitionDetail } from "../lib/api";
import { formatPassAt1 } from "../lib/format";
import LanguageLink from "../components/LanguageLink";

/* /competitions/:slug — the membership list (which problems are in this
 * competition) plus the per-competition leaderboard top-5 by best pass@1. */
export default function CompetitionDetailPage() {
  const { slug = "" } = useParams();
  const [d, setD] = useState<CompetitionDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.competition(slug).then(setD).catch((e: unknown) => setErr(String(e)));
  }, [slug]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;

  const byLevel = { Easy: 0, Medium: 0, Hard: 0 } as Record<string, number>;
  for (const p of d.problems) byLevel[p.level] = (byLevel[p.level] ?? 0) + 1;

  return (
    <div className="col">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1 style={{ marginBottom: 4 }}>{d.name}</h1>
        <div className="muted">
          <code>{d.slug}</code> · {d.kind} ·{" "}
          <span className={`badge ${d.status === "active" ? "badge-easy" : "badge-medium"}`}
                style={{ fontSize: "0.75rem" }}>{d.status}</span>
        </div>
        {d.description && (
          <p style={{ marginTop: 12, whiteSpace: "pre-wrap", maxWidth: 760 }}>{d.description}</p>
        )}
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="problems"    value={String(d.n_problems)}
              tip={`Easy ${byLevel.Easy} · Medium ${byLevel.Medium} · Hard ${byLevel.Hard}`} />
        <Stat label="submissions" value={String(d.n_submissions)} />
        <Stat label="models"      value={String(d.n_models)} />
        <Stat label="languages"   value={String(d.languages.length)}
              tip={d.languages.join(", ") || "no submissions yet"} />
      </div>

      <h2 style={{ marginBottom: 4 }}>Top models</h2>
      {d.top_models.length === 0 ? (
        <div className="empty">No judged submissions for this competition yet.</div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>#</th><th>Model</th><th>Version</th>
              <th title="best pass@1 across this competition's problems">pass@1</th>
              <th>submissions</th>
            </tr></thead>
            <tbody>
              {d.top_models.map((m, i) => (
                <tr key={`${m.model_name}-${m.model_version}`}>
                  <td className="metric">{i + 1}</td>
                  <td><Link to={`/models/${encodeURIComponent(m.model_name)}`}><b>{m.model_name}</b></Link></td>
                  <td className="muted">{m.model_version}</td>
                  <td className="metric">{formatPassAt1(m.best_pass_at_1)}</td>
                  <td className="metric">{m.n_submissions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 style={{ marginBottom: 4 }}>Problems in this competition</h2>
      {d.problems.length === 0 ? (
        <div className="empty">No problems linked yet.</div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>Slug</th><th>Title</th><th>Level</th><th>Tags</th>
            </tr></thead>
            <tbody>
              {d.problems.map(p => (
                <tr key={p.id}>
                  <td><Link to={`/problems/${p.title_slug}`}><code>{p.title_slug}</code></Link></td>
                  <td>{p.title}</td>
                  <td>
                    <span className={`badge badge-${p.level.toLowerCase()}`}>{p.level}</span>
                  </td>
                  <td className="muted">{p.tags.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d.languages.length > 0 && (
        <div className="muted" style={{ fontSize: "0.9rem" }}>
          Languages with submissions in this competition:{" "}
          {d.languages.map((l, i) => (
            <span key={l}>{i > 0 && ", "}<LanguageLink name={l} /></span>
          ))}
        </div>
      )}
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
