import { useEffect, useState, useCallback } from "react";
import { api, type AppSettingRow, type HarnessHealthRow } from "../lib/api";

/* Admin-only "Variables" tab.
 *
 * Two things:
 *  1. Runtime-editable dispatch knobs (repeats, TLE retries, etc.). An override
 *     wins over the env var / code default at USE time, so changes take effect
 *     without an env change or a restart. Clearing a field reverts to env/default.
 *  2. Live health of every harness (probes each runner's /healthz), so you can
 *     see at a glance which harnesses are reachable. */
export default function AdminSettings() {
  const [rows, setRows] = useState<AppSettingRow[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string>("");   // key currently saving
  const [msg, setMsg] = useState<string>("");
  const [err, setErr] = useState<string>("");

  const [health, setHealth] = useState<HarnessHealthRow[]>([]);
  const [healthBusy, setHealthBusy] = useState(false);

  const load = useCallback(() => {
    api.adminGetSettings()
      .then(r => setRows(r.settings))
      .catch(e => setErr(String(e)));
  }, []);

  const loadHealth = useCallback(() => {
    setHealthBusy(true);
    api.adminHarnessesHealth()
      .then(r => setHealth(r.harnesses))
      .catch(e => setErr(String(e)))
      .finally(() => setHealthBusy(false));
  }, []);

  useEffect(() => { load(); loadHealth(); }, [load, loadHealth]);

  async function save(key: string, clear = false) {
    setBusy(key); setMsg(""); setErr("");
    const value = clear ? null : (edits[key] ?? "");
    try {
      const r = await api.adminPutSettings([{ key, value }]);
      const res = r.results[0];
      if (res?.ok) {
        setMsg(`${key} = ${String(res.value)}`);
        setEdits(e => { const n = { ...e }; delete n[key]; return n; });
        load();
      } else {
        setErr(`${key}: ${res?.error ?? "failed"}`);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  }

  const badge = (source: string) => {
    const color = source === "override" ? "#2563eb"
      : source === "env" ? "#9333ea" : "#6b7280";
    return (
      <span style={{
        fontSize: "0.72rem", padding: "1px 7px", borderRadius: 999,
        background: color, color: "white", marginLeft: 8,
      }}>{source}</span>
    );
  };

  const statusDot = (h: HarnessHealthRow) => {
    const c = h.status === "up" ? "#16a34a"
      : h.status === "n/a" ? "#9ca3af"
      : h.status === "unconfigured" ? "#d97706" : "#dc2626";
    return <span style={{
      display: "inline-block", width: 10, height: 10, borderRadius: 999,
      background: c, marginRight: 8,
    }} />;
  };

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "1rem" }}>
      <h1>Variables</h1>
      <p style={{ color: "#6b7280" }}>
        Runtime dispatch settings and live harness health. An override takes
        effect without a restart. Clear a field to go back to the environment
        value or the default.
      </p>
      {msg && <p style={{ color: "#16a34a" }}>Saved: {msg}</p>}
      {err && <p style={{ color: "#dc2626" }}>{err}</p>}

      <h2 style={{ marginTop: "1.5rem" }}>Dispatch settings</h2>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
            <th style={{ padding: "6px 4px" }}>Setting</th>
            <th style={{ padding: "6px 4px" }}>Current</th>
            <th style={{ padding: "6px 4px" }}>New value</th>
            <th style={{ padding: "6px 4px" }}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.key} style={{ borderBottom: "1px solid #f3f4f6" }}>
              <td style={{ padding: "8px 4px", verticalAlign: "top" }}>
                <div style={{ fontWeight: 600 }}>{r.label}</div>
                <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>{r.key}</div>
                <div style={{ fontSize: "0.78rem", color: "#6b7280", marginTop: 4 }}>
                  {r.help}
                </div>
                {(r.min != null || r.max != null) && (
                  <div style={{ fontSize: "0.72rem", color: "#9ca3af" }}>
                    range {r.min ?? "-"}…{r.max ?? "-"} · default {String(r.default)}
                  </div>
                )}
              </td>
              <td style={{ padding: "8px 4px", verticalAlign: "top", whiteSpace: "nowrap" }}>
                <strong>{String(r.value)}</strong>{badge(r.source)}
              </td>
              <td style={{ padding: "8px 4px", verticalAlign: "top" }}>
                <input
                  type={r.type === "int" || r.type === "float" ? "number" : "text"}
                  value={edits[r.key] ?? ""}
                  placeholder={String(r.value)}
                  onChange={e => setEdits(s => ({ ...s, [r.key]: e.target.value }))}
                  style={{ width: 110, padding: "4px 6px" }}
                />
              </td>
              <td style={{ padding: "8px 4px", verticalAlign: "top", whiteSpace: "nowrap" }}>
                <button disabled={busy === r.key || (edits[r.key] ?? "") === ""}
                        onClick={() => save(r.key)}>Save</button>{" "}
                <button disabled={busy === r.key || r.source !== "override"}
                        title="Go back to the environment value or the default"
                        onClick={() => save(r.key, true)}>Reset</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ marginTop: "2rem" }}>
        Harness health{" "}
        <button disabled={healthBusy} onClick={loadHealth}
                style={{ fontSize: "0.8rem" }}>
          {healthBusy ? "Probing…" : "Refresh"}
        </button>
      </h2>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
            <th style={{ padding: "6px 4px" }}>Harness</th>
            <th style={{ padding: "6px 4px" }}>Endpoint</th>
            <th style={{ padding: "6px 4px" }}>Status</th>
            <th style={{ padding: "6px 4px" }}>Detail</th>
          </tr>
        </thead>
        <tbody>
          {health.map(h => (
            <tr key={h.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
              <td style={{ padding: "8px 4px" }}>
                <div style={{ fontWeight: 600 }}>{h.slug}</div>
                <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>{h.kind}</div>
              </td>
              <td style={{ padding: "8px 4px", fontSize: "0.8rem", color: "#6b7280" }}>
                {h.endpoint ?? "—"}
              </td>
              <td style={{ padding: "8px 4px", whiteSpace: "nowrap" }}>
                {statusDot(h)}{h.status}
              </td>
              <td style={{ padding: "8px 4px", fontSize: "0.78rem", color: "#6b7280" }}>
                {h.message}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
