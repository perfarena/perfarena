import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  api, getApiKey, MePayload, PromptTemplateIn, PromptTemplatePublic,
} from "../lib/api";

export default function Prompts() {
  // /prompts/:id deep-links to one prompt — we auto-expand + scroll to it.
  const { id: focusIdParam } = useParams();
  const focusId = focusIdParam ? Number(focusIdParam) : null;
  const [me, setMe] = useState<MePayload | null>(null);
  const [rows, setRows] = useState<PromptTemplatePublic[]>([]);
  const [includeRetired, setIncludeRetired] = useState(false);
  const [editing, setEditing] = useState<PromptTemplatePublic | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [err, setErr] = useState("");

  async function refresh() {
    try { setRows(await api.prompts(includeRetired)); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => {
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeRetired]);

  const isAdmin = me?.role === "admin";

  async function onDelete(id: number) {
    if (!confirm("Delete this prompt?")) return;
    try { await api.deletePrompt(id); await refresh(); }
    catch (e: unknown) { setErr(String(e)); }
  }

  return (
    <div className="col">
      <div className="hero">
        <h1>Prompt templates</h1>
        <p>
          These are the exact prompts that the team sends to the model to
          produce a submission. The system expands the placeholders{" "}
          <code>{"{{slug}}"}</code>, <code>{"{{statement}}"}</code>,{" "}
          <code>{"{{language}}"}</code>, <code>{"{{language_display}}"}</code>,{" "}
          and <code>{"{{language_fence}}"}</code> for each problem at run time.
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
            {showForm && editing == null ? "Cancel" : "Add prompt"}
          </button>
        )}
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {isAdmin && (showForm || editing) && (
        <PromptForm
          existing={editing}
          onCancel={() => { setEditing(null); setShowForm(false); }}
          onSaved={async () => { setEditing(null); setShowForm(false); await refresh(); }}
        />
      )}

      {rows.length === 0
        ? <div className="empty">No prompts yet.</div>
        : (
          <div className="col" style={{ gap: 12 }}>
            {rows.map(p => (
              <PromptCard
                key={p.id}
                p={p}
                isAdmin={isAdmin}
                focused={focusId === p.id}
                onEdit={() => { setEditing(p); setShowForm(true); }}
                onDelete={() => onDelete(p.id)}
              />
            ))}
          </div>
        )}
    </div>
  );
}

/* One prompt in the list. Main info (name, status, created date, notes,
 * language hint) is always visible; the bulky templates — system prompt +
 * user prompt template — are collapsed behind a toggle, hidden by default,
 * so the list stays scannable. */
function PromptCard({
  p, isAdmin, focused = false, onEdit, onDelete,
}: {
  p: PromptTemplatePublic;
  isAdmin: boolean;
  focused?: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  // When deep-linked via /prompts/:id this card is the focus: expand its text
  // by default and scroll it into view so the click lands on the right prompt.
  const [open, setOpen] = useState(focused);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focused && ref.current) {
      ref.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focused]);
  return (
    <div ref={ref} className="card col"
         style={focused ? { borderColor: "var(--accent)", boxShadow: "0 0 0 2px var(--accent-soft)" } : undefined}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h3 style={{ margin: 0 }}>
          {p.name}
          {" "}
          <span className={`badge ${p.status === "active" ? "badge-easy" : "badge-medium"}`}
                style={{ fontSize: "0.7rem", verticalAlign: "middle", marginLeft: 8 }}>
            {p.status}
          </span>
        </h3>
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          created {new Date(p.created_at).toLocaleDateString()}
        </span>
      </div>

      {p.notes && <div className="muted" style={{ fontSize: "0.9rem" }}>{p.notes}</div>}
      {p.language_hint && <div className="muted">Language hint: <code>{p.language_hint}</code></div>}

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <button className="secondary" onClick={() => setOpen(o => !o)} aria-expanded={open}>
          {open ? "Hide prompt text" : "Show prompt text"}
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
          <Field label="System prompt" value={p.system_prompt} />
          <Field label="User prompt template" value={p.user_prompt_template} />
        </div>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="col" style={{ gap: 4 }}>
      <div className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <pre className="code" style={{ margin: 0, whiteSpace: "pre-wrap", maxHeight: 280, overflow: "auto" }}>{value}</pre>
    </div>
  );
}

function PromptForm({
  existing, onSaved, onCancel,
}: {
  existing: PromptTemplatePublic | null;
  onSaved: () => void; onCancel: () => void;
}) {
  const [f, setF] = useState<PromptTemplateIn>(existing ? {
    name: existing.name,
    system_prompt: existing.system_prompt,
    user_prompt_template: existing.user_prompt_template,
    language_hint: existing.language_hint,
    notes: existing.notes,
    status: existing.status,
  } : {
    name: "",
    system_prompt: "",
    user_prompt_template: "",
    language_hint: "",
    notes: "",
    status: "active",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const body: PromptTemplateIn = {
        ...f,
        language_hint: f.language_hint || null,
        notes: f.notes || null,
      };
      if (existing) await api.editPrompt(existing.id, body);
      else await api.addPrompt(body);
      onSaved();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <form className="card col" onSubmit={submit}>
      <h3 style={{ margin: 0 }}>{existing ? `Edit "${existing.name}"` : "Add prompt"}</h3>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label className="col" style={{ flex: "1 1 260px" }}>
          <span className="muted">Name (slug-like, for the trace metadata)</span>
          <input required value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
        </label>
        <label className="col" style={{ flex: "0 0 160px" }}>
          <span className="muted">Status</span>
          <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
            <option value="active">active</option>
            <option value="retired">retired</option>
          </select>
        </label>
        <label className="col" style={{ flex: "0 0 200px" }}>
          <span className="muted">Language hint (optional)</span>
          <input value={f.language_hint ?? ""} onChange={e => setF({ ...f, language_hint: e.target.value })}
                 placeholder="python3 / cpp / …" />
        </label>
      </div>
      <label className="col">
        <span className="muted">System prompt</span>
        <textarea rows={4} required className="code"
                  value={f.system_prompt} onChange={e => setF({ ...f, system_prompt: e.target.value })} />
      </label>
      <label className="col">
        <span className="muted">User prompt template</span>
        <textarea rows={4} required className="code"
                  value={f.user_prompt_template} onChange={e => setF({ ...f, user_prompt_template: e.target.value })}
                  placeholder='Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n{{statement}}' />
      </label>
      <label className="col">
        <span className="muted">Notes (markdown OK)</span>
        <textarea rows={2} value={f.notes ?? ""} onChange={e => setF({ ...f, notes: e.target.value })} />
      </label>
      <div className="row" style={{ gap: 6 }}>
        <button type="submit" disabled={busy}>{busy ? "Please wait…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
    </form>
  );
}
