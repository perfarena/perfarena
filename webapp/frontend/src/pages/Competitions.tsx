import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, CompetitionIn, CompetitionPublic, HarnessPublic,
  getApiKey, MePayload,
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
    if (!confirm("Delete this competition? Existing submissions and problems stay as they are.")) return;
    try { await api.deleteCompetition(id); await refresh(); }
    catch (e: unknown) { setErr(String(e)); }
  }

  return (
    <div className="col">
      <div className="hero">
        <h1>Competitions</h1>
        <p>
          Each competition pairs a problem set with its own oracles. The
          correctness oracle decides whether a solution is correct. The
          performance oracle measures how the solution runs. Every competition
          has its own leaderboard. The seed set is LeetCode's 99 problems
          (LC-99). Admins can add more sets later, such as HumanEval, MBPP, or
          a custom set. A problem can belong to more than one competition.
          Submissions refer to problems by slug, so the leaderboard of each
          competition is a view of the same submission data.
        </p>
      </div>

      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={includeRetired}
                 onChange={e => setIncludeRetired(e.target.checked)} />
          Show retired
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

      <div className="muted" style={{ fontSize: "0.82rem" }}>
        Compatible harnesses:{" "}
        {c.compatible_harness_slugs.length === 0 ? (
          <span style={{ color: "var(--bad, #c33)" }}>
            none. The server rejects submissions (HTTP 400) until an admin adds at least one on the edit form
          </span>
        ) : (
          c.compatible_harness_slugs.map((s, i) => (
            <span key={s}>
              {i > 0 && ", "}
              <code>{s}</code>
            </span>
          ))
        )}
      </div>

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <Link to={`/competitions/${c.slug}`}>
          <button className="secondary">Open</button>
        </Link>
        {isAdmin && (
          <>
            <button className="secondary" onClick={onEdit}>Edit</button>
            <button className="secondary" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                    onClick={onDelete}>Delete</button>
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
    compatible_harness_slugs: [...(existing.compatible_harness_slugs ?? [])],
  } : {
    slug: "", name: "", description: "", kind: "custom", status: "active",
    // New competitions start with an EMPTY compat list — admin explicitly
    // opts harnesses in. Matches server-side default. Until the admin
    // adds at least one, POST /api/submissions returns 400 for any
    // submission against this competition.
    compatible_harness_slugs: [],
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // Fetch every registered harness (non-retired) so the multi-select can
  // offer the full set. Filter retired so admins don't accidentally
  // pin a stale entry — the list rarely changes so one fetch on mount
  // is fine.
  const [harnesses, setHarnesses] = useState<HarnessPublic[]>([]);
  useEffect(() => {
    api.harnesses(false).then(setHarnesses).catch(() => setHarnesses([]));
  }, []);

  function toggleCompat(slug: string) {
    setF(prev => {
      const cur = new Set(prev.compatible_harness_slugs ?? []);
      if (cur.has(slug)) cur.delete(slug); else cur.add(slug);
      return { ...prev, compatible_harness_slugs: Array.from(cur) };
    });
  }

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
      <div className="col" style={{ gap: 6 }}>
        <span className="muted" style={{ fontSize: "0.9rem" }}>
          <b>Compatible harnesses</b>: the allow-list of harnesses that
          can score submissions to this competition. The server rejects
          any submission whose <code>harness_slug</code> is not checked
          here (HTTP 400 with a clear error). Leave the list empty to block
          new submissions until an admin decides.
        </span>
        {harnesses.length === 0 ? (
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            No harnesses registered. Add one on <code>/harnesses</code>.
          </div>
        ) : (
          <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
            {harnesses.map(h => {
              const checked = (f.compatible_harness_slugs ?? []).includes(h.slug);
              return (
                <label key={h.slug}
                       style={{
                         display: "flex", alignItems: "center", gap: 6,
                         padding: "4px 8px", borderRadius: 4,
                         border: "1px solid var(--border, #ddd)",
                         background: checked ? "var(--surface-3, rgba(0,120,255,0.08))" : "transparent",
                         cursor: "pointer", fontSize: "0.88rem",
                       }}
                       title={`kind=${h.kind}${h.description ? ` · ${h.description.slice(0,80)}` : ""}`}>
                  <input type="checkbox" checked={checked}
                         onChange={() => toggleCompat(h.slug)} />
                  <span>{h.name}</span>
                  <code className="muted" style={{ fontSize: "0.78rem" }}>{h.slug}</code>
                </label>
              );
            })}
          </div>
        )}
      </div>
      <label className="col">
        <span className="muted">Description (Markdown)</span>
        <textarea rows={3} value={f.description ?? ""}
                  onChange={e => setF({ ...f, description: e.target.value })}
                  placeholder="What this competition evaluates, where the problems come from, and how to read the leaderboard." />
      </label>
      <div className="row" style={{ gap: 6 }}>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
    </form>
  );
}

