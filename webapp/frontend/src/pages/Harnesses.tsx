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
    if (!confirm("Delete this harness? The server refuses the delete if any competition, submission, or run still references it.")) return;
    try { await api.deleteHarness(id); await refresh(); }
    catch (e: unknown) { setErr(String(e)); }
  }

  // Hero + heading are owned by the parent HarnessesHub so the two tabs
  // (perf + correctness) share one page-level chrome and this panel can
  // render bare content. When someone renders <Harnesses /> directly (no
  // hub) they still get the list + form — just without the intro.
  return (
    <div className="col">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={includeRetired}
                 onChange={e => setIncludeRetired(e.target.checked)} />
          Show retired
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
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<{ ok: boolean; message: string } | null>(null);
  const hasRunnerConfig = !!((h.config ?? {}) as Record<string, unknown>).endpoint;
  const canProbe = h.kind !== "leetcode" && hasRunnerConfig;

  async function onProbe() {
    setProbing(true); setProbeResult(null);
    try {
      const r = await api.probeHarness(h.id);
      setProbeResult({ ok: r.ok, message: r.message });
    } catch (e: unknown) {
      setProbeResult({ ok: false, message: String(e) });
    } finally { setProbing(false); }
  }
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
        {canProbe && (
          <button className="secondary" disabled={probing} onClick={onProbe}
                  title="POST /api/admin/harnesses/{id}/probe → runner's /healthz">
            {probing ? "Probing…" : "Test connection"}
          </button>
        )}
        {isAdmin && (
          <>
            <button className="secondary" onClick={onEdit}>Edit</button>
            <button className="secondary" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                    onClick={onDelete}>Delete</button>
          </>
        )}
      </div>
      {probeResult && (
        <div className="muted" style={{
          fontSize: "0.82rem", whiteSpace: "pre-wrap",
          color: probeResult.ok ? "var(--easy)" : "var(--bad)",
        }}>
          {probeResult.ok ? "✓ " : "✗ "}{probeResult.message}
        </div>
      )}
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
  const [timeoutS, setTimeoutS] = useState<number>(
    Number((existing?.config as { timeout_s?: number } | null)?.timeout_s) || 1800,
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    let cfg: Record<string, unknown> | null = null;
    if (configText.trim()) {
      try { cfg = JSON.parse(configText); }
      catch { setErr("Config must be valid JSON."); setBusy(false); return; }
    }
    // The dedicated field is the source of truth for the dispatch timeout.
    if (timeoutS > 0) cfg = { ...(cfg ?? {}), timeout_s: timeoutS };
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
        <label className="col" style={{ flex: "0 0 160px" }}>
          <span className="muted">Dispatch timeout (s)</span>
          <input type="number" min={1} value={timeoutS}
                 onChange={e => setTimeoutS(Number(e.target.value))}
                 title="The maximum number of seconds that the backend waits for a /measure dispatch. After this time, the backend records a Dispatch timeout. Set this value higher than the runner's own watchdog (--request-timeout). This field writes config.timeout_s." />
        </label>
      </div>
      <label className="col">
        <span className="muted">Description (Markdown). How does this harness score a solution? What does it measure? Where does the code run?</span>
        <textarea rows={4} value={f.description ?? ""}
                  onChange={e => setF({ ...f, description: e.target.value })}
                  placeholder="e.g. Runs each submission inside a firecracker VM on our local runner. Reports wall-clock ns via clock_monotonic and peak RSS via getrusage. See docs/harness/arena-local.md for the full pipeline." />
      </label>
      <label className="col">
        <span className="muted">Config (JSON, optional). Add the endpoint URL, Docker image tag, resource limits, and anything else that the harness needs to reproduce a run.</span>
        <textarea rows={5} value={configText}
                  onChange={e => setConfigText(e.target.value)}
                  placeholder={`arena-local schema (what the dispatcher reads):
{
  "endpoint":    "http://runner.internal:8787",  // required for remote dispatch
  "auth_token":  "<shared-secret>",              // sent as Bearer on every request
  "timeout_s":   300,                            // per-attempt HTTP timeout
  "repeats":     7                               // perf measure runs per attempt
                                                 //   (overrides ARENA_DISPATCH_REPEATS)
}
See webapp/examples/harness_runner_server.py for the reference server.`}
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
