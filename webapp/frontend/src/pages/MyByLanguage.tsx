import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, MyByLanguagePayload, MyLangProblem } from "../lib/api";
import LevelBadge from "../components/LevelBadge";
import PromptName from "../components/PromptName";
import { formatMemory, formatRuntime } from "../lib/format";

/** My work, separated by language. Across ALL my (non-deleted) submissions
 *  in each language, the union of per-problem best status. */
export default function MyByLanguage() {
  const [data, setData] = useState<MyByLanguagePayload[] | null>(null);
  const [active, setActive] = useState<string>("");
  const [filter, setFilter] = useState<"all" | "accepted" | "rejected" | "queued" | "missing">("all");
  const [err, setErr] = useState("");

  useEffect(() => {
    api.myByLanguage().then(d => {
      setData(d);
      if (d.length > 0 && !active) setActive(d[0].language);
    }).catch((e: unknown) => setErr(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!data) return <div className="empty">Loading…</div>;
  if (data.length === 0) return (
    <div className="col">
      <div className="hero"><h1>My work, by language</h1></div>
      <div className="empty">You don't have any submissions yet. <Link to="/submit">Start one</Link>.</div>
    </div>
  );

  const cur = data.find(d => d.language === active) ?? data[0];

  const bucketOf = (p: MyLangProblem) =>
    p.accepted ? "accepted"
    : p.status === "queued" ? "queued"
    : p.status == null ? "missing"
    : "rejected";

  const visible = cur.problems.filter(p =>
    filter === "all" ? true : bucketOf(p) === filter
  );

  const counts = {
    accepted: cur.problems.filter(p => bucketOf(p) === "accepted").length,
    rejected: cur.problems.filter(p => bucketOf(p) === "rejected").length,
    queued:   cur.problems.filter(p => bucketOf(p) === "queued").length,
    missing:  cur.problems.filter(p => bucketOf(p) === "missing").length,
  };

  return (
    <div className="col">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>My work, by language</h1>
        <p>One row per problem with the best status across all your
          {" "}submissions in the selected language. Click a problem to open
          {" "}it; click <i>details</i> to open the submission that contributed
          {" "}the best attempt.</p>
      </div>

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {data.map(d => (
          <button key={d.language}
                  className={d.language === cur.language ? "" : "secondary"}
                  onClick={() => setActive(d.language)}>
            <b>{d.language}</b>
            <span className="muted"> · {d.n_accepted}/{d.n_supported}</span>
          </button>
        ))}
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="accepted"  value={`${cur.n_accepted}/${cur.n_supported}`} />
        <Stat label="attempted" value={String(cur.n_attempted)} />
        <Stat label="rejected"  value={String(counts.rejected)} />
        <Stat label="queued"    value={String(counts.queued)} />
        <Stat label="missing"   value={String(counts.missing)} />
        <Stat label="submissions" value={String(cur.submissions.length)} />
      </div>

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {(["all", "accepted", "rejected", "queued", "missing"] as const).map(f => (
          <button key={f}
                  className={filter === f ? "" : "secondary"}
                  onClick={() => setFilter(f)}>
            {f}{f !== "all" && ` (${counts[f]})`}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <div className="empty">No problems in this bucket.</div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>Problem</th><th>Level</th><th>Status</th>
              <th title="How many of your attempts on this problem (across all your submissions in this language)">Tries</th>
              <th title="Best accepted attempt's avg runtime, or a placeholder if no accepted attempt">Best avg runtime</th>
              <th title="Best accepted attempt's peak (max resident) memory. Peak on both oracles, never per function call">Peak mem</th>
              <th>From</th>
            </tr></thead>
            <tbody>
              {visible.map(p => (
                <tr key={p.problem_slug}>
                  <td>
                    <Link to={`/problems/${p.problem_slug}`}>
                      <code>{p.problem_slug}</code>
                    </Link>
                  </td>
                  <td><LevelBadge level={p.level} /></td>
                  <td>
                    {p.status === null ? <span className="muted">—</span>
                     : <span className={`badge ${p.accepted ? "badge-easy" : p.status === "queued" ? "badge-medium" : "badge-hard"}`}>{p.status}</span>}
                  </td>
                  <td className="metric">{p.n_attempts || "—"}</td>
                  <td className="metric">{formatRuntime(p.runtime_ms)}</td>
                  <td className="metric">{formatMemory(p.memory_mb)}</td>
                  <td>
                    {p.submission_id != null && (
                      <Link to={`/submissions/${p.submission_id}`}>#{p.submission_id}</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Your submissions in {cur.language}</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Each row is one batch you submitted in this language. The table above
          shows the best problem-level status across all of them.
        </p>
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>When</th><th>Model</th>
              <th title="Registered prompt template used; 'ad-hoc' for inline prompts.">Prompt</th>
              <th>Status</th><th>k</th>
              <th>pass@1</th><th>solved</th><th>attempts</th><th></th>
            </tr></thead>
            <tbody>
              {cur.submissions.map(s => (
                <tr key={s.id}>
                  <td className="muted">{new Date(s.created_at).toLocaleString()}</td>
                  <td><b>{s.model_name}</b> <span className="muted">{s.model_version}</span></td>
                  <td><PromptName id={s.prompt_id} name={s.prompt_name} /></td>
                  <td><span className="badge badge-status">{s.status}</span></td>
                  <td className="metric">{s.n_samples_per_problem}</td>
                  <td className="metric">{(s.pass_at_1 ?? 0).toFixed(2)}</td>
                  <td className="metric">{s.total_solved}/{s.total_problems}</td>
                  <td className="metric">{s.n_attempts}</td>
                  <td>
                    <Link to={`/submissions/${s.id}`}>details →</Link>
                    {" · "}
                    <Link to={`/submit/${s.id}`}>continue →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 130 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
