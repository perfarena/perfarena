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
  // Opt-in for shipping session cookies in the leetcode_accounts slot.
  // Default OFF — when the user picks that table without this flag, the
  // bundle includes account names + cooldowns but redacts cookies. Setting
  // this makes the bundle a secret (full session JWTs), so we render a
  // banner whenever it's true.
  const [includeCreds, setIncludeCreds] = useState(false);
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
      // Only request credentials when leetcode_accounts is actually selected;
      // otherwise the flag is a no-op and confuses the audit log.
      const wantCreds = includeCreds && ordered.includes("leetcode_accounts");
      const url = api.adminExportUrl(ordered, format, wantCreds);
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
        <h1>Export and Import</h1>
        <p>
          Download the content of the platform as a bundle, or merge the
          export from another instance into this database. This helps when
          several replicas run at the same time, each with a different
          LeetCode account. Merge the replicas into one database.
          {" "}<Link to="/admin">Back to admin</Link>
        </p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {/* ---------- Export ---------- */}
      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Export</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
          Select the tables and a format. Then download the bundle. You can
          import the JSON bundle into another instance with the Import section
          below. Use the CSV format for spreadsheet inspection only.
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

          {picked.has("leetcode_accounts") && (
            <div className="col" style={{ gap: 6, padding: "8px 10px",
                                          background: "rgba(250, 204, 21, 0.08)",
                                          border: "1px solid rgba(250, 204, 21, 0.4)",
                                          borderRadius: 6 }}>
              <label className="row" style={{ gap: 6 }}>
                <input type="checkbox"
                       checked={includeCreds}
                       onChange={e => setIncludeCreds(e.target.checked)} />
                <span><b>Include session cookies</b> in <code>leetcode_accounts</code></span>
              </label>
              <div className="muted" style={{ fontSize: "0.8rem", lineHeight: 1.5 }}>
                {includeCreds ? (
                  <>
                    ⚠ The bundle includes the raw{" "}
                    <code>LEETCODE_SESSION</code> JWT of every account. Keep the
                    download secret. Encrypt the download. Do not commit the
                    download to git. Change the cookies on the target instance
                    after import.
                  </>
                ) : (
                  <>
                    The export includes account names and cooldown timestamps.{" "}
                    The cookies appear as <code>&lt;head&gt;…&lt;redacted&gt;</code>.
                    Select the box only when you move a real credential pool
                    to another instance.
                  </>
                )}
              </div>
            </div>
          )}

          <div className="row" style={{ gap: 16, alignItems: "center", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: "0.85rem" }}>Format:</span>
            <label className="row" style={{ gap: 6 }}>
              <input type="radio" name="format" value="json"
                     checked={format === "json"} onChange={() => setFormat("json")} />
              JSON <span className="muted" style={{ fontSize: "0.8rem" }}>(you can import it back)</span>
            </label>
            <label className="row" style={{ gap: 6 }}>
              <input type="radio" name="format" value="csv"
                     checked={format === "csv"} onChange={() => setFormat("csv")} />
              CSV zip <span className="muted" style={{ fontSize: "0.8rem" }}>(spreadsheets)</span>
            </label>
            <button onClick={downloadExport} disabled={busy || selectedCount === 0}>
              {busy ? "Please wait…" : `Download ${format.toUpperCase()}`}
            </button>
          </div>
        </div>
      </section>

      {/* ---------- Import ---------- */}
      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Import (merge)</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
          Select a JSON bundle that another instance exported. The merge works
          like this:
        </p>
        <ul className="muted" style={{ marginTop: 0, lineHeight: 1.6, fontSize: "0.9rem" }}>
          <li>
            Natural-key tables (<code>users</code>, <code>problems</code>,{" "}
            <code>prompt_templates</code>, <code>registered_models</code>,{" "}
            <code>competitions</code>, <code>blog_posts</code>,{" "}
            <code>baselines</code>): the merge <b>skips</b> an incoming row when
            its key already exists in this database. The local data wins.
          </li>
          <li>
            Surrogate-key tables (<code>submissions</code>,{" "}
            <code>attempts</code>, <code>runs</code>): the merge <b>always
            inserts</b> the rows with new local ids. It remaps the foreign keys.
            Replicas with the same integer ids then merge without a collision.
          </li>
          <li>
            <code>leetcode_accounts</code> (the credential pool in a file): the
            merge writes into <code>data/leetcode_accounts.json</code> by name.
            The merge <b>skips</b> a name that already exists in this database
            (the local cookies win). The merge also skips a row when its{" "}
            <code>cookie_header</code> looks redacted (it has no{" "}
            <code>LEETCODE_SESSION</code>, or it has the mark{" "}
            <code>&lt;redacted&gt;</code>). A sanitized bundle is therefore safe
            to import. It brings only the shape of the account list.
          </li>
          <li>
            The merge <b>excludes</b> the append-only hash chain for transparency
            (<code>TraceLogEntry</code>). Each instance builds its own hash chain.
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
              {importing ? "Please wait…" : "Import and merge"}
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
