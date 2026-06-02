import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, getApiKey, MePayload,
  RegisteredModelIn, RegisteredModelOverview,
} from "../lib/api";
import { formatPassAt1 } from "../lib/format";

const PROVIDERS = ["ollama", "openai", "anthropic", "hf", "custom", "unknown"];
const STATUSES = ["active", "unverified", "retired"];
const TYPES = ["chat", "instruct", "reasoning", "coder", "embedding", "other"];

/* /models — every model the platform has ever seen (admin-curated + auto-
 * stubbed from first-submission discovery). One card per model with a header
 * (identity + activity stats) and an expandable details section revealing
 * the full metadata: description, type, family, provenance, params, notes,
 * endpoint. Admins fill in metadata on auto-stubbed rows via the edit form. */
export default function Catalog() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [rows, setRows] = useState<RegisteredModelOverview[]>([]);
  const [includeRetired, setIncludeRetired] = useState(false);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<RegisteredModelOverview | null>(null);
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
    if (!confirm("Delete this model from the registry?")) return;
    try { await api.deleteRegisteredModel(id); await refresh(); }
    catch (e: unknown) { setErr(String(e)); }
  }

  const unverified = rows.filter(r => r.status === "unverified").length;

  return (
    <div className="col">
      <div className="hero">
        <h1>Models</h1>
        <p>
          Every model the platform has seen submit answers. Admin-curated rows
          carry the full provenance and description; rows auto-stubbed on first
          submission start as <code>unverified</code> until an admin fills in
          the details. Drill into a row for the model's full metadata, or
          click the name to see its submissions, languages, and rankings.
          See <Link to="/prompts">Prompts</Link> for the prompts we run and
          {" "}<Link to="/rankings">Rankings</Link> for outcomes.
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

      {isAdmin && unverified > 0 && (
        <div className="card" style={{ borderColor: "var(--medium)", background: "rgba(220,180,80,0.08)", padding: 10, fontSize: "0.9rem" }}>
          <b>{unverified} model{unverified === 1 ? "" : "s"} awaiting curation.</b>{" "}
          Auto-stubbed from submissions — click <em>Edit</em> on any to fill in
          the description, type, family, and provenance.
        </div>
      )}

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
          <div className="col" style={{ gap: 12 }}>
            {rows.map(m => (
              <ModelCard key={m.id} m={m} isAdmin={isAdmin}
                         onEdit={() => { setEditing(m); setShowForm(true); }}
                         onDelete={() => onDelete(m.id)} />
            ))}
          </div>
        )}
    </div>
  );
}

/* One model — header always visible, details collapsed by default. */
function ModelCard({
  m, isAdmin, onEdit, onDelete,
}: {
  m: RegisteredModelOverview; isAdmin: boolean;
  onEdit: () => void; onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const isUnverified = m.status === "unverified";
  const isRetired = m.status === "retired";
  const statusCls = isUnverified ? "badge-medium"
                  : isRetired ? "badge-hard" : "badge-easy";
  const last = m.last_submitted_at
    ? new Date(m.last_submitted_at).toLocaleDateString()
    : "—";

  return (
    <div className="card col" style={isUnverified
      ? { borderColor: "var(--medium)" } : undefined}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div className="col" style={{ gap: 4, minWidth: 220 }}>
          <h3 style={{ margin: 0 }}>
            <Link to={`/models/${encodeURIComponent(m.name)}`}>{m.name}</Link>
            <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>{m.version}</span>
            <span className={`badge ${statusCls}`} style={{ fontSize: "0.7rem", marginLeft: 8, verticalAlign: "middle" }}>
              {m.status}
            </span>
          </h3>
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            {m.family ? <><b>{m.family}</b> · </> : null}
            {m.model_type ? <>{m.model_type} · </> : null}
            {m.provider}
            {m.endpoint ? <> · <code>{m.endpoint}</code></> : null}
          </div>
        </div>
        <div className="row" style={{ gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
          <Mini label="submissions" value={String(m.n_submissions)} />
          <Mini label="judged"      value={String(m.n_judged)} />
          <Mini label="best pass@1" value={formatPassAt1(m.best_pass_at_1)} />
          <Mini label="languages"   value={m.languages.length === 0 ? "—" : String(m.languages.length)}
                tip={m.languages.join(", ") || "no submissions yet"} />
          <Mini label="last seen"   value={last} />
        </div>
      </div>

      {m.description && !open && (
        <div className="muted" style={{ fontSize: "0.9rem" }}>{m.description}</div>
      )}

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <button className="secondary" onClick={() => setOpen(o => !o)} aria-expanded={open}>
          {open ? "Hide details" : "Show details"}
        </button>
        {isAdmin && (
          <>
            <button className="secondary" onClick={onEdit}>edit</button>
            <button className="secondary" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                    onClick={onDelete}>delete</button>
          </>
        )}
      </div>

      {open && (
        <div className="col" style={{ gap: 10, marginTop: 4 }}>
          <DetailField label="Description"  value={m.description} markdown />
          <DetailField label="Provenance"   value={m.provenance}   markdown />
          <DetailField label="Notes"        value={m.notes}        markdown />
          <DetailField label="Default params (decoding)"
                       value={m.params ? JSON.stringify(m.params, null, 2) : null}
                       code />
          {m.languages.length > 0 && (
            <DetailField label="Languages submitted in"
                         value={m.languages.join(", ")} />
          )}
        </div>
      )}
    </div>
  );
}

function Mini({ label, value, tip }: { label: string; value: string; tip?: string }) {
  return (
    <div className="col" style={{ gap: 2 }} title={tip}>
      <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function DetailField({ label, value, code = false, markdown = false }: {
  label: string; value: string | null; code?: boolean; markdown?: boolean;
}) {
  return (
    <div className="col" style={{ gap: 4 }}>
      <div className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      {value
        ? code
          ? <pre className="code" style={{ margin: 0, whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}>{value}</pre>
          : <div style={{ whiteSpace: "pre-wrap", fontSize: markdown ? "0.92rem" : undefined }}>{value}</div>
        : <div className="muted" style={{ fontStyle: "italic", fontSize: "0.85rem" }}>not set</div>}
    </div>
  );
}

function ModelForm({
  existing, onSaved, onCancel,
}: {
  existing: RegisteredModelOverview | null;
  onSaved: () => void; onCancel: () => void;
}) {
  const [f, setF] = useState<RegisteredModelIn>(existing ? {
    name: existing.name, version: existing.version, provider: existing.provider,
    endpoint: existing.endpoint, params: existing.params,
    notes: existing.notes,
    description: existing.description,
    model_type: existing.model_type,
    family: existing.family,
    provenance: existing.provenance,
    // Auto-promote unverified → active on first save, so editing a stub
    // counts as "this admin has curated it". Admin can still pick retired.
    status: existing.status === "unverified" ? "active" : existing.status,
  } : {
    name: "", version: "", provider: "ollama",
    endpoint: "", params: null, notes: "", status: "active",
    description: "", model_type: "", family: "", provenance: "",
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
      const body: RegisteredModelIn = {
        ...f, params,
        endpoint: f.endpoint || null,
        notes: f.notes || null,
        description: f.description || null,
        model_type: f.model_type || null,
        family: f.family || null,
        provenance: f.provenance || null,
      };
      if (existing) await api.editRegisteredModel(existing.id, body);
      else await api.addRegisteredModel(body);
      onSaved();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <form className="card col" onSubmit={submit}>
      <h3 style={{ margin: 0 }}>
        {existing
          ? `Edit "${existing.name}"${existing.status === "unverified" ? " (auto-stubbed)" : ""}`
          : "Add model"}
      </h3>
      {existing?.status === "unverified" && (
        <div className="muted" style={{ fontSize: "0.85rem" }}>
          Auto-stubbed from a submission. Fill in the description, type, family,
          and provenance — saving promotes it to <code>active</code>.
        </div>
      )}

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
        <label className="col" style={{ flex: "1 1 140px" }}>
          <span className="muted">Type</span>
          <select value={f.model_type ?? ""} onChange={e => setF({ ...f, model_type: e.target.value })}>
            <option value="">—</option>
            {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="col" style={{ flex: "1 1 140px" }}>
          <span className="muted">Family</span>
          <input value={f.family ?? ""}
                 onChange={e => setF({ ...f, family: e.target.value })}
                 placeholder="qwen / llama / gpt / …" />
        </label>
        <label className="col" style={{ flex: "1 1 220px" }}>
          <span className="muted">Endpoint (URL or provider id)</span>
          <input value={f.endpoint ?? ""} onChange={e => setF({ ...f, endpoint: e.target.value })} />
        </label>
        <label className="col" style={{ flex: "1 1 140px" }}>
          <span className="muted">Status</span>
          <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
            {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      </div>

      <label className="col">
        <span className="muted">Description (markdown, surfaced on the card header)</span>
        <textarea rows={2} value={f.description ?? ""}
                  onChange={e => setF({ ...f, description: e.target.value })}
                  placeholder="One-paragraph summary of the model." />
      </label>
      <label className="col">
        <span className="muted">Provenance (markdown — model card URL, citation, source)</span>
        <textarea rows={3} value={f.provenance ?? ""}
                  onChange={e => setF({ ...f, provenance: e.target.value })}
                  placeholder="https://huggingface.co/…  ·  Paper: …" />
      </label>
      <label className="col">
        <span className="muted">Default decoding params (JSON, optional)</span>
        <textarea rows={3} value={paramsRaw} onChange={e => setParamsRaw(e.target.value)}
                  placeholder='{"temperature": 0.2}' className="code" />
      </label>
      <label className="col">
        <span className="muted">Notes (markdown, operator-facing)</span>
        <textarea rows={2} value={f.notes ?? ""} onChange={e => setF({ ...f, notes: e.target.value })} />
      </label>
      <div className="row" style={{ gap: 6 }}>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
    </form>
  );
}
