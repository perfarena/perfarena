import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, AttemptResultPayload, SubmissionDetail } from "../lib/api";
import AttemptsTable, { StatusFilter, bucket } from "../components/AttemptsTable";

export default function AttemptResults() {
  const { id = "" } = useParams();
  const [rows, setRows] = useState<AttemptResultPayload[] | null>(null);
  const [meta, setMeta] = useState<SubmissionDetail | null>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [q, setQ] = useState("");
  const [admin, setAdmin] = useState(false);

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
  useEffect(() => {
    // Admin gate for the per-attempt delete controls. Silent on failure
    // (not signed in / not admin) — the delete buttons just stay hidden.
    api.me().then(m => setAdmin(m.role === "admin")).catch(() => setAdmin(false));
  }, []);

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
        <p className="muted" style={{ marginTop: 0 }}>
          Every attempt below was dispatched to LeetCode and scored: accepted, rejected, or still queued.
        </p>
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

      {visible.length === 0
        ? <div className="empty">No attempts match.</div>
        : <AttemptsTable rows={visible}
                         perOp={s?.energy_tool != null || s?.machine_name != null}
                         admin={admin} onChanged={refresh} />}
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
