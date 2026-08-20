import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, MePayload, setApiKey, SubmissionPublic } from "../lib/api";
import SubmissionsTable from "../components/SubmissionsTable";

export default function Me() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [subs, setSubs] = useState<SubmissionPublic[]>([]);
  const [err, setErr] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [dlLang, setDlLang] = useState("");
  const [dlLevel, setDlLevel] = useState("");
  const [dlOnlyAccepted, setDlOnlyAccepted] = useState(false);

  useEffect(() => {
    Promise.all([api.me(), api.mySubmissions()])
      .then(([m, s]) => { setMe(m); setSubs(s); })
      .catch(e => setErr(String(e)));
  }, []);

  function signOut() {
    setApiKey(null);
    location.href = "/";
  }

  async function download() {
    setDownloading(true);
    try {
      await api.downloadDataset({
        language: dlLang || undefined,
        level: dlLevel || undefined,
        only_accepted: dlOnlyAccepted || undefined,
      });
    } catch (e) {
      setErr(String(e));
    } finally {
      setDownloading(false);
    }
  }

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!me) return <div className="empty">Loading…</div>;

  return (
    <div className="col">
      <div className="hero">
        <h1>{me.display_name}</h1>
        <p>{me.email}{me.org ? ` · ${me.org}` : ""} · role: {me.role}</p>
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <Link to="/me/by-language"><button className="secondary">By language</button></Link>
        <Link to="/me/api-key"><button className="secondary">Manage API key</button></Link>
        <button className="secondary" onClick={signOut}>Sign out</button>
      </div>

      <div className="card col">
        <h2>Download solutions dataset</h2>
        <p className="muted" style={{ margin: 0 }}>
          One JSONL record per attempt: code, inference trace, model params, prompt template, and
          per-problem oracle metrics (runtime, memory, percentiles). Download it to reproduce any
          result yourself.
        </p>
        <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>
          <select value={dlLang} onChange={e => setDlLang(e.target.value)}>
            <option value="">All languages</option>
            {["python3", "java", "cpp", "javascript", "typescript", "csharp", "c", "golang", "rust", "kotlin", "ruby", "swift", "scala", "php"].map(l =>
              <option key={l} value={l}>{l}</option>)}
          </select>
          <select value={dlLevel} onChange={e => setDlLevel(e.target.value)}>
            <option value="">All difficulties</option>
            <option>Easy</option><option>Medium</option><option>Hard</option>
          </select>
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={dlOnlyAccepted} onChange={e => setDlOnlyAccepted(e.target.checked)} />
            <span className="muted">Only accepted</span>
          </label>
          <button onClick={download} disabled={downloading}>
            {downloading ? "Preparing…" : "Download .jsonl"}
          </button>
        </div>
      </div>

      <h2>Your submissions</h2>
      {subs.length === 0
        ? <div className="empty">No submissions yet. <Link to="/submit">Submit a batch →</Link></div>
        : <SubmissionsTable rows={subs} />}
    </div>
  );
}
