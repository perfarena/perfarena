import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, getApiKey } from "../lib/api";

/* Admin-only Export / Import.
 *
 * Export picks tables via checkbox and downloads either a JSON envelope
 * (round-trippable through Import) or a CSV zip (human inspection only).
 *
 * Import accepts the JSON envelope from another instance and merges its
 * content into THIS database — natural-key tables skip dupes, surrogate-
 * key tables (submissions/attempts/runs) get fresh local ids so the merge
 * never collides. Useful when several replicas run in parallel (different
 * LeetCode accounts) and we want to converge them. */
export default function AdminExport() {
  const [tables, setTables] = useState<string[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [format, setFormat] = useState<"json" | "csv">("json");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Import state.
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<{
    counts: Record<string, { inserted: number; skipped: number; errored: number }>;
  } | null>(null);

  useEffect(() => {
    api.adminExportTables()
      .then(r => {
        setTables(r.tables);
        setPicked(new Set(r.tables));   // default = all selected
      })
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  function toggle(t: string) {
    setPicked(p => {
      const next = new Set(p);
      if (next.has(t)) next.delete(t); else next.add(t);
      return next;
    });
  }
  function pickAll()  { setPicked(new Set(tables)); }
  function pickNone() { setPicked(new Set()); }

  async function downloadExport() {
    setErr(""); setBusy(true);
    try {
      const key = getApiKey();
      if (!key) throw new Error("not authenticated");
      const ordered = tables.filter(t => picked.has(t));   // keep server's order
      const url = api.adminExportUrl(ordered, format);
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${key}` },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status} ${res.statusText}: ${text.slice(0, 200)}`);
      }
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      // Honour the server-suggested filename when present.
      const cd = res.headers.get("content-disposition") ?? "";
      const m = /filename=("?)([^";]+)\1/i.exec(cd);
      a.download = m ? m[2] : `perfarena-export.${format === "csv" ? "zip" : "json"}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (e: unknown) {
      setErr(String(e));
    } finally { setBusy(false); }
  }

  async function runImport() {
    if (!importFile) return;
    setErr(""); setImporting(true); setResult(null);
    try {
      const text = await importFile.text();
      let parsed: unknown;
      try { parsed = JSON.parse(text); }
      catch { throw new Error("Selected file isn't valid JSON."); }
      const r = await api.adminImport(parsed);
      setResult(r);
    } catch (e: unknown) {
      setErr(String(e));
    } finally { setImporting(false); }
  }

  const selectedCount = picked.size;

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero">
        <h1>Export & Import</h1>
        <p>
          Snapshot the platform's content into a downloadable bundle, or
          merge another instance's export into this database. Useful when
          several replicas run in parallel (different LeetCode accounts)
          and you want to converge them into one.
          {" "}<Link to="/admin">Back to admin</Link>
        </p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {/* ---------- Export ---------- */}
      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Export</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
          Pick tables, pick a format, then download. JSON can be replayed
          back into another instance via Import below; CSV is for spreadsheet
          inspection only.
        </p>

        <div className="card col" style={{ gap: 10 }}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              Tables ({selectedCount} of {tables.length} selected)
            </span>
            <button className="secondary" type="button" onClick={pickAll}>all</button>
            <button className="secondary" type="button" onClick={pickNone}>none</button>
          </div>
          <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
            {tables.map(t => (
              <label key={t} className="row" style={{ gap: 6 }}>
                <input type="checkbox"
                       checked={picked.has(t)}
                       onChange={() => toggle(t)} />
                <code>{t}</code>
              </label>
            ))}
          </div>

          <div className="row" style={{ gap: 16, alignItems: "center", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: "0.85rem" }}>Format:</span>
            <label className="row" style={{ gap: 6 }}>
              <input type="radio" name="format" value="json"
                     checked={format === "json"} onChange={() => setFormat("json")} />
              JSON <span className="muted" style={{ fontSize: "0.8rem" }}>(round-trippable)</span>
            </label>
            <label className="row" style={{ gap: 6 }}>
              <input type="radio" name="format" value="csv"
                     checked={format === "csv"} onChange={() => setFormat("csv")} />
              CSV zip <span className="muted" style={{ fontSize: "0.8rem" }}>(spreadsheets)</span>
            </label>
            <button onClick={downloadExport} disabled={busy || selectedCount === 0}>
              {busy ? "Building…" : `Download ${format.toUpperCase()}`}
            </button>
          </div>
        </div>
      </section>

      {/* ---------- Import ---------- */}
      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Import (merge)</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
          Drop a JSON bundle produced by another instance's export. Merge
          semantics:
        </p>
        <ul className="muted" style={{ marginTop: 0, lineHeight: 1.6, fontSize: "0.9rem" }}>
          <li>
            Natural-key tables (<code>users</code>, <code>problems</code>,{" "}
            <code>prompt_templates</code>, <code>registered_models</code>,{" "}
            <code>competitions</code>, <code>blog_posts</code>,{" "}
            <code>baselines</code>) — incoming rows whose key already exists
            locally are <b>skipped</b>; admin curation here wins.
          </li>
          <li>
            Surrogate-key tables (<code>submissions</code>,{" "}
            <code>attempts</code>, <code>runs</code>) — rows are <b>always
            inserted</b> with new local ids, FKs remapped so replicas with
            overlapping integer ids merge cleanly.
          </li>
          <li>
            The append-only transparency hash chain (<code>TraceLogEntry</code>)
            is <b>not</b> imported — it's per-instance by construction.
          </li>
        </ul>

        <div className="card col" style={{ gap: 10 }}>
          <input type="file" accept="application/json,.json"
                 onChange={e => setImportFile(e.target.files?.[0] ?? null)} />
          {importFile && (
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              Selected: <code>{importFile.name}</code>
              {" "}({Math.round(importFile.size / 1024)} KB)
            </div>
          )}
          <div className="row" style={{ gap: 8 }}>
            <button onClick={runImport} disabled={!importFile || importing}>
              {importing ? "Merging…" : "Import & merge"}
            </button>
            {importFile && (
              <button className="secondary"
                      onClick={() => { setImportFile(null); setResult(null); }}>
                Clear
              </button>
            )}
          </div>
        </div>

        {result && (
          <div className="card col" style={{ gap: 6, borderColor: "var(--easy)", background: "rgba(74,222,128,0.06)" }}>
            <b>Merge complete.</b>
            <div className="card tbl-scroll" style={{ padding: 0, background: "var(--panel)" }}>
              <table className="tbl">
                <thead><tr>
                  <th>Table</th><th>Inserted</th><th>Skipped</th><th>Errored</th>
                </tr></thead>
                <tbody>
                  {Object.entries(result.counts).map(([t, c]) => (
                    <tr key={t}>
                      <td><code>{t}</code></td>
                      <td className="metric">{c.inserted}</td>
                      <td className="metric">{c.skipped}</td>
                      <td className="metric"
                          style={c.errored > 0 ? { color: "var(--bad)" } : undefined}>
                        {c.errored}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
