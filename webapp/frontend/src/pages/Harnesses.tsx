import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, HarnessIn, HarnessPublic, getApiKey, MePayload,
} from "../lib/api";

const KINDS = ["leetcode", "arena-local", "custom"];

/* /harnesses — the platform can score submissions with more than one perf
 * oracle. LeetCode's online judge is the seed harness (that's what produced
 * every legacy run); admins can add an in-house harness (with its own
 * runtime / memory / percentile methodology) or any other custom oracle
 * via the inline form. Every submission resolves to exactly one harness
 * (own override → competition default). */
export default function Harnesses() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [rows, setRows] = useState<HarnessPublic[]>([]);
  const [includeRetired, setIncludeRetired] = useState(false);
  const [editing, setEditing] = useState<HarnessPublic | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [err, setErr] = useState("");

  async function refresh() {
    try { setRows(await api.harnesses(includeRetired)); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => {
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeRetired]);

  const isAdmin = me?.role === "admin";

  async function onDelete(id: number) {
    if (!confirm("Delete this harness? Refuses if any competitions / submissions / runs still reference it.")) return;
    try { await api.deleteHarness(id); await refresh(); }
    catch (e: unknown) { setErr(String(e)); }
  }

  return (
    <div className="col">
      <div className="hero">
        <h1>Harnesses</h1>
        <p>
          A harness is the performance oracle that judges a submission's
          runs. Historically PerfArena scored everything against LeetCode's
          online judge — that harness is still here as the default. New
          harnesses (an in-house local runner, a HumanEval unittest harness,
          any custom oracle) can be added below; each competition points at
          exactly one harness by default, and every submission carries the
          harness that actually scored it.
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
            {showForm && editing == null ? "Cancel" : "Add harness"}
          </button>
        )}
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {isAdmin && (showForm || editing) && (
        <HarnessForm
          existing={editing}
          onCancel={() => { setEditing(null); setShowForm(false); }}
          onSaved={async () => { setEditing(null); setShowForm(false); await refresh(); }}
        />
      )}

      {rows.length === 0
        ? <div className="empty">No harnesses yet.{isAdmin && " Add one above."}</div>
        : (
          <div className="col" style={{ gap: 12 }}>
            {rows.map(h => (
              <HarnessCard key={h.id} h={h} isAdmin={isAdmin}
                           onEdit={() => { setEditing(h); setShowForm(true); }}
                           onDelete={() => onDelete(h.id)} />
            ))}
          </div>
        )}
    </div>
  );
}

function HarnessCard({
  h, isAdmin, onEdit, onDelete,
}: {
  h: HarnessPublic; isAdmin: boolean;
  onEdit: () => void; onDelete: () => void;
}) {
  const isRetired = h.status === "retired";
  return (
    <div className="card col"
         style={isRetired ? { borderColor: "var(--medium)" } : undefined}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div className="col" style={{ gap: 4 }}>
          <h3 style={{ margin: 0 }}>
            <Link to={`/harnesses/${h.slug}`}>{h.name}</Link>
            <span className={`badge ${isRetired ? "badge-medium" : "badge-easy"}`}
                  style={{ fontSize: "0.7rem", marginLeft: 8, verticalAlign: "middle" }}>
              {h.status}
            </span>
            <span className="muted" style={{ fontSize: "0.85rem", marginLeft: 8 }}>
              {h.kind}
            </span>
          </h3>
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            <code>{h.slug}</code>
          </div>
        </div>
        <div className="row" style={{ gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
          <Mini label="competitions" value={String(h.n_competitions)} />
          <Mini label="submissions"  value={String(h.n_submissions)} />
          <Mini label="runs"         value={String(h.n_runs)} />
        </div>
      </div>

      {h.description && (
        <div className="muted" style={{ fontSize: "0.9rem", whiteSpace: "pre-wrap" }}>
          {h.description}
        </div>
      )}

      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <Link to={`/harnesses/${h.slug}`}>
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

function HarnessForm({
  existing, onSaved, onCancel,
}: {
  existing: HarnessPublic | null;
  onSaved: () => void; onCancel: () => void;
}) {
  const [f, setF] = useState<HarnessIn>(existing ? {
    slug: existing.slug, name: existing.name,
    description: existing.description, kind: existing.kind,
    status: existing.status,
    config: existing.config ?? null,
  } : {
    slug: "", name: "", description: "", kind: "custom", status: "active",
    config: null,
  });
  const [configText, setConfigText] = useState<string>(
    existing?.config ? JSON.stringify(existing.config, null, 2) : "",
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    let cfg: Record<string, unknown> | null = null;
    if (configText.trim()) {
      try { cfg = JSON.parse(configText); }
      catch { setErr("config must be valid JSON"); setBusy(false); return; }
    }
    try {
      const body: HarnessIn = {
        ...f, description: f.description || null, config: cfg,
      };
      if (existing) await api.editHarness(existing.id, body);
      else await api.addHarness(body);
      onSaved();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <form className="card col" onSubmit={submit}>
      <h3 style={{ margin: 0 }}>
        {existing ? `Edit "${existing.name}"` : "Add harness"}
      </h3>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label className="col" style={{ flex: "1 1 200px" }}>
          <span className="muted">Slug (URL id, lowercase-kebab)</span>
          <input required value={f.slug} onChange={e => setF({ ...f, slug: e.target.value })}
                 placeholder="arena-local" />
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
        <span className="muted">Description (markdown) — how does this harness judge a solution? What does it measure? Where does the code run?</span>
        <textarea rows={4} value={f.description ?? ""}
                  onChange={e => setF({ ...f, description: e.target.value })}
                  placeholder="e.g. Runs each submission inside a firecracker VM on our local runner. Reports wall-clock ns via clock_monotonic and peak RSS via getrusage. See docs/harness/arena-local.md for the full pipeline." />
      </label>
      <label className="col">
        <span className="muted">Config (JSON, optional) — endpoint URL, docker image tag, resource limits, whatever the harness needs to reproduce a run.</span>
        <textarea rows={3} value={configText}
                  onChange={e => setConfigText(e.target.value)}
                  placeholder='{"endpoint": "https://runner.internal", "image": "perfarena/local:sha256-…", "timeout_s": 30}'
                  style={{ fontFamily: "var(--mono, monospace)" }} />
      </label>
      <div className="row" style={{ gap: 6 }}>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
    </form>
  );
}
