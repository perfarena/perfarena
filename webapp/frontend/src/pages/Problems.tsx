import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ProblemPublic } from "../lib/api";
import LevelBadge from "../components/LevelBadge";

export default function Problems() {
  const [rows, setRows] = useState<ProblemPublic[]>([]);
  const [q, setQ] = useState("");
  const [level, setLevel] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    api.problems({ level })
      .then(setRows).catch((e: unknown) => setErr(String(e)));
  }, [level]);

  const filtered = useMemo(() => {
    const needle = q.toLowerCase();
    if (!needle) return rows;
    return rows.filter(r =>
      r.title.toLowerCase().includes(needle) ||
      r.title_slug.includes(needle) ||
      r.tags.some(t => t.toLowerCase().includes(needle)),
    );
  }, [rows, q]);

  return (
    <div>
      <div className="hero">
        <h1>Supported problems</h1>
        <p>{rows.length.toLocaleString()} problems available. Click a row for the full statement, stats, and per-problem leaderboard.</p>
      </div>

      <div className="filter-bar">
        <input placeholder="Search title, slug, or tag…" value={q} onChange={e => setQ(e.target.value)} className="grow" />
        <select value={level} onChange={e => setLevel(e.target.value)}>
          <option value="">All difficulties</option>
          <option>Easy</option><option>Medium</option><option>Hard</option>
        </select>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Title</th><th>Slug</th><th>Level</th><th>Tags</th>
          </tr></thead>
          <tbody>
            {filtered.slice(0, 500).map(p => (
              <tr key={p.id}>
                <td><Link to={`/problems/${p.title_slug}`}>{p.title}</Link></td>
                <td className="muted">
                  <Link to={`/problems/${p.title_slug}`}><code>{p.title_slug}</code></Link>
                </td>
                <td><LevelBadge level={p.level} /></td>
                <td className="muted">{p.tags.slice(0, 4).join(", ")}{p.tags.length > 4 ? " …" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 500 && <div className="empty">Showing first 500 of {filtered.length} matches.</div>}
      </div>
    </div>
  );
}
