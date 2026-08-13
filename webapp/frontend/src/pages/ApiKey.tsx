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
    if (!confirm("Issue a new key and revoke the current key? Any script that uses the old key will stop.")) return;
    setBusy("regen"); setErr("");
    try {
      const r = await api.regenerateApiKey();
      setApiKey(r.api_key);    // updates localStorage + fires arena.auth-changed
      setK(r.api_key);
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(null); }
  }

  async function discard() {
    if (!confirm("Discard your API key? The platform signs you out. Sign in again to get a new key.")) return;
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
          This is a personal access token for the PerfArena API. Programs use it
          to call the API. Send it as <code>Authorization: Bearer &lt;key&gt;</code>. You
          do not need it for the web UI. The browser uses it automatically after you
          sign in.
        </p>
      </div>

      <div className="card col">
        <div className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>
          Your key
        </div>
        <pre className="code" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {k ?? "(No key. You discarded it. Sign in to get a new key.)"}
        </pre>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <button className="secondary" onClick={copy} disabled={!k}>
            {copied ? "Copied ✓" : "Copy"}
          </button>
          <button onClick={regenerate} disabled={busy !== null}>
            {busy === "regen" ? "Please wait…" : "Regenerate"}
          </button>
          <button className="secondary"
                  style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                  onClick={discard} disabled={busy !== null || !k}>
            {busy === "discard" ? "Please wait…" : "Discard"}
          </button>
        </div>
        {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
      </div>

      <div className="card col">
        <h2 style={{ margin: 0 }}>What these buttons do</h2>
        <ul>
          <li><b>Copy</b> puts the key on your clipboard. At other times the server does not send the key as plaintext.</li>
          <li><b>Regenerate</b> issues a new key and revokes the previous key. A script that uses the old key then gets a 401 error. The browser gets the new key automatically.</li>
          <li><b>Discard</b> removes the key. The platform signs you out. When you sign in again, you get a new key automatically.</li>
        </ul>
      </div>
    </div>
  );
}
