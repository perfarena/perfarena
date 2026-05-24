import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, getApiKey, MePayload, RegisteredModelIn, RegisteredModelPublic,
} from "../lib/api";

const PROVIDERS = ["ollama", "openai", "anthropic", "hf", "custom"];

export default function Catalog() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [rows, setRows] = useState<RegisteredModelPublic[]>([]);
  const [includeRetired, setIncludeRetired] = useState(false);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<RegisteredModelPublic | null>(null);
  const [showForm, setShowForm] = useState(false);

  async function refresh() {
    try { setRows(await api.registeredModels(includeRetired)); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => {
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeRetired]);

  const isAdmin = me?.role === "admin";

  async function onDelete(id: number) {
    if (!confirm("Delete this entry from the catalog?")) return;
    try { await api.deleteRegisteredModel(id); await refresh(); }
    catch (e: unknown) { setErr(String(e)); }
  }

  return (
    <div className="col">
      <div className="hero">
        <h1>Model catalog</h1>
        <p>
          Models the platform currently runs experiments against. Authors who
          want their model evaluated reach out to the team and grant us access;
          the team registers the model here and produces submissions on the
          author's behalf — no public self-submission. See <Link to="/prompts">Prompts</Link> for
          the prompts we use, <Link to="/rankings">Rankings</Link> for outcomes.
        </p>
      </div>

      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={includeRetired}
                 onChange={e => setIncludeRetired(e.target.checked)} />
          show retired
        </label>
        {isAdmin && (
          <button onClick={() => { setEditing(null); setShowForm(s => !s); }}>
            {showForm && editing == null ? "Cancel" : "Add model"}
          </button>
        )}
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {isAdmin && (showForm || editing) && (
        <ModelForm
          existing={editing}
          onCancel={() => { setEditing(null); setShowForm(false); }}
          onSaved={async () => { setEditing(null); setShowForm(false); await refresh(); }}
        />
      )}

      {rows.length === 0
        ? <div className="empty">No models yet.{isAdmin && " Add one above."}</div>
        : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>Model</th><th>Version</th><th>Provider</th>
                <th>Endpoint</th><th>Status</th><th>Added</th><th>Notes</th>
                {isAdmin && <th></th>}
              </tr></thead>
              <tbody>
                {rows.map(m => (
                  <tr key={m.id}>
                    <td>
                      <Link to={`/models/${encodeURIComponent(m.name)}`}>
                        <b>{m.name}</b>
                      </Link>
                    </td>
                    <td className="muted">{m.version}</td>
                    <td>{m.provider}</td>
                    <td className="muted">{m.endpoint ?? "—"}</td>
                    <td>
                      <span className={`badge ${m.status === "active" ? "badge-easy" : "badge-medium"}`}>
                        {m.status}
                      </span>
                    </td>
                    <td className="muted">{new Date(m.added_at).toLocaleDateString()}</td>
                    <td style={{ maxWidth: 320, whiteSpace: "pre-wrap" }}>{m.notes ?? "—"}</td>
                    {isAdmin && (
                      <td>
                        <button className="secondary" onClick={() => { setEditing(m); setShowForm(true); }}>edit</button>
                        {" "}
                        <button className="secondary" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                                onClick={() => onDelete(m.id)}>del</button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}

function ModelForm({
  existing, onSaved, onCancel,
}: {
  existing: RegisteredModelPublic | null;
  onSaved: () => void; onCancel: () => void;
}) {
  const [f, setF] = useState<RegisteredModelIn>(existing ? {
    name: existing.name, version: existing.version, provider: existing.provider,
    endpoint: existing.endpoint, params: existing.params,
    notes: existing.notes, status: existing.status,
  } : {
    name: "", version: "", provider: "ollama",
    endpoint: "", params: null, notes: "", status: "active",
  });
  const [paramsRaw, setParamsRaw] = useState(
    existing?.params ? JSON.stringify(existing.params, null, 2) : "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      let params: Record<string, unknown> | null = null;
      if (paramsRaw.trim()) {
        try { params = JSON.parse(paramsRaw); }
        catch { throw new Error("params must be valid JSON"); }
      }
      const body: RegisteredModelIn = { ...f, params, endpoint: f.endpoint || null };
      if (existing) await api.editRegisteredModel(existing.id, body);
      else await api.addRegisteredModel(body);
      onSaved();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <form className="card col" onSubmit={submit}>
      <h3 style={{ margin: 0 }}>{existing ? `Edit "${existing.name}"` : "Add model to the catalog"}</h3>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label className="col" style={{ flex: "1 1 200px" }}>
          <span className="muted">Name</span>
          <input required value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
        </label>
        <label className="col" style={{ flex: "1 1 140px" }}>
          <span className="muted">Version</span>
          <input required value={f.version} onChange={e => setF({ ...f, version: e.target.value })} />
        </label>
        <label className="col" style={{ flex: "1 1 140px" }}>
          <span className="muted">Provider</span>
          <select value={f.provider} onChange={e => setF({ ...f, provider: e.target.value })}>
            {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="col" style={{ flex: "1 1 220px" }}>
          <span className="muted">Endpoint (URL or provider id)</span>
          <input value={f.endpoint ?? ""} onChange={e => setF({ ...f, endpoint: e.target.value })} />
        </label>
        <label className="col" style={{ flex: "1 1 140px" }}>
          <span className="muted">Status</span>
          <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
            <option value="active">active</option>
            <option value="retired">retired</option>
          </select>
        </label>
      </div>
      <label className="col">
        <span className="muted">Default decoding params (JSON, optional)</span>
        <textarea rows={3} value={paramsRaw} onChange={e => setParamsRaw(e.target.value)}
                  placeholder='{"temperature": 0.2}' className="code" />
      </label>
      <label className="col">
        <span className="muted">Notes (markdown OK)</span>
        <textarea rows={3} value={f.notes ?? ""} onChange={e => setF({ ...f, notes: e.target.value })} />
      </label>
      <div className="row" style={{ gap: 6 }}>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
    </form>
  );
}
