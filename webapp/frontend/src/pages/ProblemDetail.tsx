import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ProblemDetail } from "../lib/api";
import LevelBadge from "../components/LevelBadge";
import Markdown from "../components/Markdown";
import { formatMemory, formatPercentile, formatRuntime } from "../lib/format";
import LanguageLink from "../components/LanguageLink";

export default function ProblemDetailPage() {
  const { slug = "" } = useParams();
  const [p, setP] = useState<ProblemDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.problemDetail(slug).then(setP).catch((e: unknown) => setErr(String(e)));
  }, [slug]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!p) return <div className="empty">Loading…</div>;

  const passRate = p.n_attempts > 0 ? (p.n_accepted / p.n_attempts) * 100 : null;

  return (
    <div className="col">
      <div>
        <div className="row" style={{ gap: 16, alignItems: "baseline" }}>
          <h1 style={{ marginBottom: 0 }}>{p.title}</h1>
          <LevelBadge level={p.level} />
        </div>
        <div className="muted"><code>{p.title_slug}</code> · {p.tags.join(", ")}</div>
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="LeetCode tests" value={p.testcases?.toString() ?? "–"} />
        <Stat label="Submissions" value={p.n_attempts.toString()} />
        <Stat label="Accepted" value={`${p.n_accepted} / ${p.n_attempts}`} />
        <Stat label="Pass rate" value={passRate == null ? "–" : `${passRate.toFixed(1)}%`} />
      </div>

      <div className="card">
        <h2>Problem statement</h2>
        <Markdown>{p.content}</Markdown>
      </div>

      {p.hints && (
        <div className="card">
          <h2>Hints</h2>
          <Markdown>{p.hints}</Markdown>
        </div>
      )}

      <BestPerLanguage p={p} />
      <TopSolutions p={p} />
    </div>
  );
}

function BestPerLanguage({ p }: { p: ProblemDetail }) {
  if (p.best_per_language.length === 0) return null;
  return (
    <div>
      <h2>Best per language</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        Best judged solution in each language for this problem. Counts on the
        right are total judged attempts (any status) / accepted attempts in
        that language.
      </p>
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Lang</th><th>Model</th><th>Submitter</th>
            <th>Status</th>
            <th title="LeetCode-reported runtime (ms)">Runtime</th>
            <th title="LeetCode-reported memory (MB)">Memory</th>
            <th title="Runtime percentile in that language">RT %ile</th>
            <th title="Memory percentile in that language">Mem %ile</th>
            <th title="Accepted / total judged attempts in this language">A/T</th>
            <th></th>
          </tr></thead>
          <tbody>
            {p.best_per_language.map(s => (
              <tr key={s.submission_id + ":" + s.language}>
                <td><LanguageLink name={s.language} /></td>
                <td>
                  <Link to={`/models/${encodeURIComponent(s.model_name)}`}>
                    <b>{s.model_name}</b>
                  </Link>
                  <span className="muted"> {s.model_version}</span>
                </td>
                <td><Link to={`/submitters/${s.submitter_slug}`}>{s.submitter}</Link></td>
                <td><span className={`badge ${s.status_msg === "Accepted" ? "badge-easy" : "badge-hard"}`}>{s.status_msg}</span></td>
                <td className="metric">{formatRuntime(s.runtime_ms)}</td>
                <td className="metric">{formatMemory(s.memory_mb)}</td>
                <td className="metric">{formatPercentile(s.runtime_percentile)}</td>
                <td className="metric">{formatPercentile(s.memory_percentile)}</td>
                <td className="metric">
                  {p.n_accepted_by_language[s.language] ?? 0}/{p.n_attempts_by_language[s.language] ?? 0}
                </td>
                <td><Link to={`/submissions/${s.submission_id}`}>batch →</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TopSolutions({ p }: { p: ProblemDetail }) {
  const [lang, setLang] = useState<string>("");
  const languages = Object.keys(p.n_attempts_by_language).sort();
  const visible = lang
    ? p.top_solutions.filter(s => s.language === lang)
    : p.top_solutions;
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>Top solutions on this problem</h2>
        {languages.length > 1 && (
          <select value={lang} onChange={e => setLang(e.target.value)}>
            <option value="">All languages</option>
            {languages.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        )}
      </div>
      {visible.length === 0 ? (
        <div className="empty">No judged solutions {lang ? `in ${lang}` : ""} for this problem yet.</div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>#</th><th>Model</th><th>Submitter</th><th>Lang</th>
              <th>Status</th>
              <th title="LeetCode-reported runtime (ms)">Runtime</th>
              <th title="LeetCode-reported memory (MB)">Memory</th>
              <th title="Runtime percentile vs other accepted submissions">Runtime %ile</th>
              <th title="Memory percentile vs other accepted submissions">Memory %ile</th><th></th>
            </tr></thead>
            <tbody>
              {visible.map((s, i) => (
                <tr key={s.submission_id + ":" + i}>
                  <td>{i + 1}</td>
                  <td>
                    <Link to={`/models/${encodeURIComponent(s.model_name)}`}>
                      <b>{s.model_name}</b>
                    </Link>
                    <span className="muted"> {s.model_version}</span>
                  </td>
                  <td><Link to={`/submitters/${s.submitter_slug}`}>{s.submitter}</Link></td>
                  <td><LanguageLink name={s.language} /></td>
                  <td><span className={`badge ${s.status_msg === "Accepted" ? "badge-easy" : "badge-hard"}`}>{s.status_msg}</span></td>
                  <td className="metric">{formatRuntime(s.runtime_ms)}</td>
                  <td className="metric">{formatMemory(s.memory_mb)}</td>
                  <td className="metric">{formatPercentile(s.runtime_percentile)}</td>
                  <td className="metric">{formatPercentile(s.memory_percentile)}</td>
                  <td><Link to={`/submissions/${s.submission_id}`}>batch →</Link></td>
                </tr>
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
    <div className="card" style={{ minWidth: 140 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
