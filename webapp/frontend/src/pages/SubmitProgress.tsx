import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, AttemptIn, SubmissionDetail, SubmissionProgressPayload } from "../lib/api";

const APPEND_SAMPLE = `[
  {
    "title_slug": "two-sum",
    "code": "class Solution:\\n    def twoSum(self, nums, target): ...",
    "inference_trace": "Hash map of value -> index; on each n check if (target - n) is in the map."
  }
]`;

export default function SubmitProgress() {
  const { id = "" } = useParams();
  const [meta, setMeta] = useState<SubmissionDetail | null>(null);
  const [progress, setProgress] = useState<SubmissionProgressPayload | null>(null);
  const [raw, setRaw] = useState(APPEND_SAMPLE);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [err, setErr] = useState("");
  const [missingFilter, setMissingFilter] = useState("");

  async function refresh() {
    try {
      const [d, p] = await Promise.all([
        api.submissionDetail(id),
        api.submissionProgress(Number(id)),
      ]);
      setMeta(d); setProgress(p);
    } catch (e: unknown) {
      setErr(String(e));
    }
  }
  useEffect(() => { refresh(); }, [id]);

  async function append() {
    setErr(""); setResult(""); setBusy(true);
    try {
      const parsed = JSON.parse(raw);
      const attempts: AttemptIn[] = Array.isArray(parsed) ? parsed : parsed.attempts;
      if (!attempts?.length) throw new Error("payload must be an array of attempts (or {attempts: [...]})");
      const out = await api.appendAttempts(Number(id), attempts);
      setResult(`Appended ${out.appended} attempt(s); dispatch queued.`);
      await refresh();
    } catch (e: unknown) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (err && !progress) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!progress || !meta) return <div className="empty">Loading…</div>;

  const pct = progress.total_supported > 0
    ? (progress.accepted / progress.total_supported) * 100 : 0;
  const filteredMissing = missingFilter
    ? progress.missing.filter(s => s.includes(missingFilter.toLowerCase()))
    : progress.missing;
  const s = meta.submission;

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>
          {s.model_name} <span className="muted">{s.model_version}</span>
          {progress.fully_accepted && (
            <span className="badge badge-easy" style={{ marginLeft: 12, fontSize: "0.8rem" }}>
              fully accepted
            </span>
          )}
        </h1>
        <p className="muted">
          Language: <b>{progress.language}</b> · submission #{progress.submission_id} ·
          {" "}<Link to={`/submit/${progress.submission_id}/attempts`}>attempt results →</Link> ·
          {" "}<Link to={`/submissions/${progress.submission_id}`}>public view →</Link>
        </p>
      </div>

      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Progress</h2>
        <div className="card col">
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <span className="metric"><b>{progress.accepted}</b> accepted · <b>{progress.queued}</b> queued · <b>{progress.rejected.length}</b> rejected · <b>{progress.missing.length}</b> missing</span>
            <span className="muted metric">{pct.toFixed(1)}% complete ({progress.accepted}/{progress.total_supported})</span>
          </div>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
      </section>

      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Append attempts</h2>
        <div className="card col">
          <p className="muted" style={{ marginTop: 0 }}>
            Paste an array of attempts (or <code>{"{attempts: [...]}"}</code>). Each attempt re-runs through the LeetCode dispatch pipeline and updates this submission's totals. Inference trace is required (≥ 20 chars).
          </p>
          <textarea rows={12} value={raw} onChange={e => setRaw(e.target.value)} className="code" />
          <div className="row">
            <button onClick={append} disabled={busy}>{busy ? "Appending…" : "Append attempts"}</button>
            <button className="secondary" onClick={() => setRaw(APPEND_SAMPLE)} disabled={busy}>Reset to sample</button>
          </div>
          {result && <div className="muted" style={{ color: "var(--accent-2)" }}>{result}</div>}
          {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
        </div>
      </section>

      <section className="col">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <h2 style={{ marginBottom: 0 }}>Missing problems ({progress.missing.length})</h2>
          <input
            placeholder="Filter slugs…"
            value={missingFilter}
            onChange={e => setMissingFilter(e.target.value)}
            style={{ minWidth: 220 }}
          />
        </div>
        {progress.missing.length === 0 ? (
          <div className="empty">Every supported problem has at least one attempt for this submission.</div>
        ) : (
          <div className="card" style={{ padding: 12, maxHeight: 360, overflowY: "auto" }}>
            <ul style={{ margin: 0, paddingLeft: 18, columns: 2, columnGap: 24 }}>
              {filteredMissing.slice(0, 500).map(slug => (
                <li key={slug} style={{ breakInside: "avoid", marginBottom: 4 }}>
                  <Link to={`/problems/${slug}`}><code>{slug}</code></Link>
                </li>
              ))}
            </ul>
            {filteredMissing.length > 500 && (
              <div className="muted" style={{ marginTop: 8 }}>Showing first 500.</div>
            )}
          </div>
        )}
      </section>

      {progress.rejected.length > 0 && (
        <section className="col">
          <h2 style={{ marginBottom: 4 }}>Rejected ({progress.rejected.length})</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            These problems were attempted but didn't pass LeetCode. Re-submit a corrected solution to overwrite the latest run.
          </p>
          <div className="card" style={{ padding: 12, maxHeight: 240, overflowY: "auto" }}>
            <ul style={{ margin: 0, paddingLeft: 18, columns: 2, columnGap: 24 }}>
              {progress.rejected.map(slug => (
                <li key={slug} style={{ breakInside: "avoid", marginBottom: 4 }}>
                  <Link to={`/problems/${slug}`}><code>{slug}</code></Link>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}
