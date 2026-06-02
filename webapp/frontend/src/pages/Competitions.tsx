import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, CompetitionIn, CompetitionPublic, getApiKey, MePayload,
} from "../lib/api";

const KINDS = ["leetcode", "humaneval", "mbpp", "custom"];

/* /competitions — the platform can host multiple contests (sets of problems
 * evaluated under one regime). The 99 LeetCode problems are the seed
 * competition; admins can add HumanEval, MBPP, a custom curated set, etc.
 * via the inline form. */
export default function Competitions() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [rows, setRows] = useState<CompetitionPublic[]>([]);
  const [includeRetired, setIncludeRetired] = useState(false);
  const [editing, setEditing] = useState<CompetitionPublic | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [err, setErr] = useState("");

  async function refresh() {
    try { setRows(await api.competitions(includeRetired)); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => {
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeRetired]);

  const isAdmin = me?.role === "admin";

  async function onDelete(id: number) {
    if (!confirm("Delete this competition? Existing submissions and problems are NOT touched.")) return;
    try { await api.deleteCompetition(id); await refresh(); }
    catch (e: unknown) { setErr(String(e)); }
  }

  return (
    <div className="col">
      <div className="hero">
        <h1>Competitions</h1>
        <p>
          PerfArena tracks one or more competitions in parallel — the original
          curated 99 LeetCode problems, plus any sets admins add later
          (HumanEval, MBPP, custom). A problem can belong to multiple
          competitions; submissions reference problems by slug, so each
          competition's leaderboard is a filter over the same submission data.
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
            {showForm && editing == null ? "Cancel" : "Add competition"}
          </button>
        )}
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {isAdmin && (showForm || editing) && (
        <CompetitionForm
          existing={editing}
          onCancel={() => { setEditing(null); setShowForm(false); }}
          onSaved={async () => { setEditing(null); setShowForm(false); await refresh(); }}
        />
      )}

      {rows.length === 0
        ? <div className="empty">No competitions yet.{isAdmin && " Add one above."}</div>
        : (
          <div className="col" style={{ gap: 12 }}>
            {rows.map(c => (
              <CompetitionCard key={c.id} c={c} isAdmin={isAdmin}
                               onEdit={() => { setEditing(c); setShowForm(true); }}
                               onDelete={() => onDelete(c.id)} />
            ))}
          </div>
        )}
    </div>
  );
}

function CompetitionCard({
  c, isAdmin, onEdit, onDelete,
}: {
  c: CompetitionPublic; isAdmin: boolean;
  onEdit: () => void; onDelete: () => void;
}) {
  const isRetired = c.status === "retired";
  return (
    <div className="card col"
         style={isRetired ? { borderColor: "var(--medium)" } : undefined}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div className="col" style={{ gap: 4 }}>
          <h3 style={{ margin: 0 }}>
            <Link to={`/competitions/${c.slug}`}>{c.name}</Link>
            <span className={`badge ${isRetired ? "badge-medium" : "badge-easy"}`}
                  style={{ fontSize: "0.7rem", marginLeft: 8, verticalAlign: "middle" }}>
              {c.status}
            </span>
            <span className="muted" style={{ fontSize: "0.85rem", marginLeft: 8 }}>
              {c.kind}
            </span>
          </h3>
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            <code>{c.slug}</code>
          </div>
        </div>
        <div className="row" style={{ gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
          <Mini label="problems"    value={String(c.n_problems)} />
          <Mini label="submissions" value={String(c.n_submissions)} />
          <Mini label="models"      value={String(c.n_models)} />
        </div>
      </div>

      {c.description && (
        <div className="muted" style={{ fontSize: "0.9rem", whiteSpace: "pre-wrap" }}>
          {c.description}
        </div>
      )}

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <Link to={`/competitions/${c.slug}`}>
          <button className="secondary">Open</button>
        </Link>
        {isAdmin && (
          <>
            <button className="secondary" onClick={onEdit}>edit</button>
            <button className="secondary" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                    onClick={onDelete}>delete</button>
          </>
        )}
      </div>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="col" style={{ gap: 2 }}>
      <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function CompetitionForm({
  existing, onSaved, onCancel,
}: {
  existing: CompetitionPublic | null;
  onSaved: () => void; onCancel: () => void;
}) {
  const [f, setF] = useState<CompetitionIn>(existing ? {
    slug: existing.slug, name: existing.name,
    description: existing.description, kind: existing.kind,
    status: existing.status,
  } : {
    slug: "", name: "", description: "", kind: "custom", status: "active",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const body: CompetitionIn = {
        ...f, description: f.description || null,
      };
      if (existing) await api.editCompetition(existing.id, body);
      else await api.addCompetition(body);
      onSaved();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <form className="card col" onSubmit={submit}>
      <h3 style={{ margin: 0 }}>
        {existing ? `Edit "${existing.name}"` : "Add competition"}
      </h3>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label className="col" style={{ flex: "1 1 200px" }}>
          <span className="muted">Slug (URL id, lowercase-kebab)</span>
          <input required value={f.slug} onChange={e => setF({ ...f, slug: e.target.value })}
                 placeholder="humaneval-v1" />
        </label>
        <label className="col" style={{ flex: "1 1 240px" }}>
          <span className="muted">Display name</span>
          <input required value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
        </label>
        <label className="col" style={{ flex: "0 0 160px" }}>
          <span className="muted">Kind</span>
          <select value={f.kind} onChange={e => setF({ ...f, kind: e.target.value })}>
            {KINDS.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <label className="col" style={{ flex: "0 0 140px" }}>
          <span className="muted">Status</span>
          <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
            <option value="active">active</option>
            <option value="retired">retired</option>
          </select>
        </label>
      </div>
      <label className="col">
        <span className="muted">Description (markdown)</span>
        <textarea rows={3} value={f.description ?? ""}
                  onChange={e => setF({ ...f, description: e.target.value })}
                  placeholder="What this competition evaluates, where the problems come from, how the leaderboard is interpreted." />
      </label>
      <div className="row" style={{ gap: 6 }}>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
    </form>
  );
}

