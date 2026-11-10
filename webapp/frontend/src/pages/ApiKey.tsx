import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, getApiKey, setApiKey } from "../lib/api";

/**
 * Personal access token management. Logged-in users land here to copy their
 * current key, rotate it (revoking the previous one), or discard it entirely
 * (which logs them out — next login auto-issues a fresh one).
 */
export default function ApiKey() {
  const [k, setK] = useState<string | null>(getApiKey());
  const [busy, setBusy] = useState<"regen" | "discard" | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  useEffect(() => { setK(getApiKey()); }, []);

  async function regenerate() {
    if (!confirm("Issue a new key and revoke the current one? Any script that uses the old key will stop working.")) return;
    setBusy("regen"); setErr("");
    try {
      const r = await api.regenerateApiKey();
      setApiKey(r.api_key);    // updates localStorage + fires arena.auth-changed
      setK(r.api_key);
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(null); }
  }

  async function discard() {
    if (!confirm("Discard your API key entirely? You will be signed out. Sign in again to get a new one.")) return;
    setBusy("discard"); setErr("");
    try {
      await api.discardApiKey();
      setApiKey(null);
      nav("/login");
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(null); }
  }

  function copy() {
    if (!k) return;
    navigator.clipboard.writeText(k);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div className="col" style={{ maxWidth: 720 }}>
      <div className="hero">
        <h1>API key</h1>
        <p>
          A personal access token for programmatic use of the PerfArena API.
          Send it as <code>Authorization: Bearer &lt;key&gt;</code>. You do not
          need it for the web UI. After you sign in, the browser uses it
          automatically.
        </p>
      </div>

      <div className="card col">
        <div className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>
          Your key
        </div>
        <pre className="code" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {k ?? "(no key: discarded, sign in to issue a new one)"}
        </pre>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <button className="secondary" onClick={copy} disabled={!k}>
            {copied ? "Copied ✓" : "Copy"}
          </button>
          <button onClick={regenerate} disabled={busy !== null}>
            {busy === "regen" ? "Regenerating…" : "Regenerate"}
          </button>
          <button className="secondary"
                  style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                  onClick={discard} disabled={busy !== null || !k}>
            {busy === "discard" ? "Discarding…" : "Discard"}
          </button>
        </div>
        {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
      </div>

      <div className="card col">
        <h2 style={{ margin: 0 }}>What these buttons do</h2>
        <ul>
          <li><b>Copy</b> copies the key to your clipboard. Otherwise, the key never leaves the server in plaintext.</li>
          <li><b>Regenerate</b> issues a new key and revokes the previous one. Scripts that use the old key start to get 401 errors. This browser switches to the new key automatically.</li>
          <li><b>Discard</b> clears the key entirely. You will be signed out. The next time you sign in, you get a new key automatically.</li>
        </ul>
      </div>
    </div>
  );
}
