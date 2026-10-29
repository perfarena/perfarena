import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Markdown from "../components/Markdown";
import {
  api, CompetitionPublic, MePayload, ReviewDetail, ReviewQueueResponse, ReviewQueueRow,
  ReviewStatus, ReviewVerdict, SolutionReview,
} from "../lib/api";

/**
 * Admin-only — label suspicious solutions as invalid or ok, one card at a time.
 * The queue is the platform's flags (speed outliers: accepted solutions at least
 * 2× faster than the median of their peers; code flags: code that runs another
 * language) plus every solution that already has a verdict. Swipe the card left
 * (invalid) or right (ok), or use the buttons or the arrow keys.
 */

const SWIPE_PX = 110;                 // drag distance that commits a verdict
const STATUSES: { key: ReviewStatus; label: string }[] = [
  { key: "pending", label: "To review" }, { key: "invalid", label: "Invalid" },
  { key: "ok", label: "OK" }, { key: "all", label: "All" },
];

interface UndoStep {
  row: ReviewQueueRow; index: number;
  before: SolutionReview | null; after: SolutionReview;
}

function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return "–";
  return v >= 100 ? `${Math.round(v).toLocaleString()} ms` : `${Number(v.toPrecision(3))} ms`;
}

function VerdictBadge({ review }: { review: SolutionReview | null }) {
  if (!review) return <span className="badge badge-status">not reviewed</span>;
  return (
    <span className={`badge ${review.verdict === "invalid" ? "badge-hard" : "badge-easy"}`}
          title={`${review.reviewed_by ?? "?"}, ${review.reviewed_at.slice(0, 16).replace("T", " ")}`}>
      {review.verdict === "invalid" ? "invalid" : "ok"}
    </span>
  );
}

export default function AdminReview() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [comps, setComps] = useState<CompetitionPublic[]>([]);
  const [comp, setComp] = useState<string>("");
  const [status, setStatus] = useState<ReviewStatus>("pending");
  const [queue, setQueue] = useState<ReviewQueueResponse | null>(null);
  const [index, setIndex] = useState(0);
  const [details, setDetails] = useState<Record<number, ReviewDetail>>({});
  const [note, setNote] = useState("");
  const [showTypical, setShowTypical] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [undo, setUndo] = useState<UndoStep[]>([]);
  const [dx, setDx] = useState(0);
  const drag = useRef<{ x: number; y: number; id: number; horizontal: boolean | null } | null>(null);
  const fetching = useRef<Set<number>>(new Set());
  const swiped = useRef(false);          // swallow the click that ends a swipe

  useEffect(() => { api.me().then(setMe).catch(() => setMe(null)); }, []);
  useEffect(() => {
    api.competitions().then(cs => {
      setComps(cs);
      setComp(c => c || (cs.find(x => x.slug === "clbg-10") ?? cs[0])?.slug || "");
    }).catch(e => setErr(String(e)));
  }, []);

  const load = useCallback(async () => {
    if (!comp) return;
    setErr("");
    try {
      setQueue(await api.adminReviewQueue(comp, status));
      setIndex(0); setUndo([]);
    } catch (e: unknown) { setErr(String(e)); }
  }, [comp, status]);
  useEffect(() => { load(); }, [load]);

  const items = queue?.items ?? [];
  const row: ReviewQueueRow | undefined = items[index];
  const detail = row ? details[row.attempt_id] : undefined;

  // Fetch the current card and the next one, so a swipe shows the next card at once.
  useEffect(() => {
    for (const r of [items[index], items[index + 1]]) {
      if (r && !details[r.attempt_id] && !fetching.current.has(r.attempt_id)) {
        fetching.current.add(r.attempt_id);
        api.adminReviewDetail(r.attempt_id)
          .then(d => setDetails(m => ({ ...m, [d.attempt_id]: d })))
          .catch(e => setErr(String(e)))
          .finally(() => fetching.current.delete(r.attempt_id));
      }
    }
  }, [items, index, details]);
  useEffect(() => { setNote(row?.review?.note ?? ""); setShowTypical(false); setDx(0); },
            [row?.attempt_id]);    // eslint-disable-line react-hooks/exhaustive-deps

  const applyLocal = useCallback((target: ReviewQueueRow, review: SolutionReview | null,
                                  at: number, restore = false) => {
    setQueue(q => {
      if (!q) return q;
      const counts = { ...q.counts };
      const key = (r: SolutionReview | null) => (r ? r.verdict : "pending") as keyof typeof counts;
      counts[key(target.review)] -= 1;
      counts[key(review)] += 1;
      const updated = { ...target, review };
      let list = q.items.filter(x => x.attempt_id !== target.attempt_id);
      const keep = status === "all" || (review ? review.verdict === status : status === "pending");
      if (keep || restore) list = [...list.slice(0, at), updated, ...list.slice(at)];
      return { ...q, counts, items: list };
    });
    setDetails(m => (m[target.attempt_id]
      ? { ...m, [target.attempt_id]: { ...m[target.attempt_id], review } } : m));
  }, [status]);

  const decide = useCallback(async (verdict: ReviewVerdict) => {
    if (!row || busy) return;
    setBusy(true); setErr("");
    try {
      const r = await api.adminSetReview(row.attempt_id, verdict, note);
      setUndo(u => [...u, { row, index, before: row.review, after: r.review }]);
      applyLocal(row, r.review, index);
      // In the "to review" list the card leaves, so the same index shows the
      // next one; elsewhere it stays, so move on.
      const stays = status === "all" || status === verdict;
      if (stays) setIndex(i => i + 1);
    } catch (e: unknown) { setErr(String(e)); setDx(0); }
    finally { setBusy(false); }
  }, [row, busy, note, index, status, applyLocal]);

  const skip = useCallback(() => { if (row) setIndex(i => i + 1); }, [row]);

  const undoLast = useCallback(async () => {
    const step = undo[undo.length - 1];
    if (!step || busy) return;
    setBusy(true); setErr("");
    try {
      let back: SolutionReview | null = null;
      if (step.before) {
        back = (await api.adminSetReview(step.row.attempt_id, step.before.verdict,
                                         step.before.note ?? undefined)).review;
      } else {
        await api.adminClearReview(step.row.attempt_id);
      }
      applyLocal({ ...step.row, review: step.after }, back, step.index, true);
      setUndo(u => u.slice(0, -1));
      setIndex(step.index);
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(false); }
  }, [undo, busy, applyLocal]);

  // Keyboard: ← invalid, → ok, ↓ or S skip, U undo (not while typing a note).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); decide("invalid"); }
      else if (e.key === "ArrowRight") { e.preventDefault(); decide("ok"); }
      else if (e.key === "ArrowDown" || e.key === "s") { e.preventDefault(); skip(); }
      else if (e.key === "u") { e.preventDefault(); undoLast(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide, skip, undoLast]);

  // Swipe: a touch drag anywhere on the card, or a mouse drag on its header
  // (so a mouse can still select code). Vertical drags scroll as usual.
  const onPointerDown = (e: React.PointerEvent) => {
    const onHandle = (e.target as HTMLElement).closest(".review-handle");
    swiped.current = false;
    if (busy || (e.pointerType === "mouse" && !onHandle)) return;
    drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId, horizontal: null };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const mx = e.clientX - d.x, my = e.clientY - d.y;
    if (d.horizontal === null && Math.hypot(mx, my) > 8) {
      d.horizontal = Math.abs(mx) > Math.abs(my);
      if (d.horizontal) (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    }
    if (d.horizontal) setDx(mx);
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.id !== e.pointerId || !d.horizontal) { setDx(0); return; }
    swiped.current = true;
    if (dx <= -SWIPE_PX) decide("invalid");
    else if (dx >= SWIPE_PX) decide("ok");
    else setDx(0);
  };

  if (me && me.role !== "admin") {
    return <div className="card" style={{ borderColor: "var(--bad)" }}>Admins only.</div>;
  }

  const lean = Math.max(-1, Math.min(1, dx / SWIPE_PX));
  return (
    <div className="col review-page">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>Review suspicious solutions</h1>
        <p>
          Label each flagged solution <b>invalid</b> or <b>OK</b>. For now, a
          solution is invalid only when it runs code in another language to solve
          the problem (for example, C that it compiles and calls through ctypes).
          Every other solution is OK, also one that skips work or uses another
          algorithm. Swipe left for invalid and right for OK, or use the buttons or the
          keys ← → (↓ skips, U undoes). An invalid solution still counts for
          pass@k, but it leaves the performance rankings and comparisons at once,
          and the golden baseline when the baseline is built again.
        </p>
      </div>

      <div className="row" style={{ gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <select value={comp} onChange={e => setComp(e.target.value)} aria-label="Competition">
          {comps.map(c => <option key={c.slug} value={c.slug}>{c.name}</option>)}
        </select>
        <div className="seg" role="group" aria-label="Status">
          {STATUSES.map(s => (
            <button key={s.key} className={status === s.key ? "on" : ""} aria-pressed={status === s.key}
                    onClick={() => setStatus(s.key)}>
              {s.label}{queue && s.key !== "all" ? ` (${queue.counts[s.key]})` : ""}
            </button>
          ))}
        </div>
        <Link to="/admin/speed-outliers" className="muted">all speed outliers</Link>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {queue && !row && (
        <div className="empty">
          {status === "pending" ? "Nothing left to review in this competition." : "No solutions here."}
          {undo.length > 0 && <div style={{ marginTop: 12 }}>
            <button className="secondary" onClick={undoLast} disabled={busy}>Undo last</button></div>}
        </div>
      )}

      {row && (
        <>
          <div className="muted">{index + 1} of {items.length}</div>
          <div className="card review-card"
               style={{ transform: `translateX(${dx}px) rotate(${lean * 4}deg)`,
                        transition: drag.current ? "none" : "transform 160ms ease",
                        borderColor: lean < -0.3 ? "var(--bad)" : lean > 0.3 ? "var(--easy)" : undefined }}
               onPointerDown={onPointerDown} onPointerMove={onPointerMove}
               onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
               onDragStart={e => e.preventDefault()}
               onClickCapture={e => {
                 if (swiped.current) { e.preventDefault(); e.stopPropagation(); swiped.current = false; }
               }}>
            <div className="review-stamp invalid" style={{ opacity: Math.max(0, -lean) }}>INVALID</div>
            <div className="review-stamp ok" style={{ opacity: Math.max(0, lean) }}>OK</div>

            <div className="review-handle">
              <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <h2 style={{ margin: 0 }}>
                  <Link to={`/problems/${row.problem_slug}`}>{detail?.problem.title ?? row.problem_slug}</Link>
                </h2>
                <code>{row.language}</code>
                <VerdictBadge review={row.review} />
              </div>
              <div className="muted" style={{ marginTop: 4 }}>
                <Link to={`/models/${encodeURIComponent(row.model_name)}`}>{row.model_name}</Link>
                {" · "}<Link to={`/submissions/${row.submission_id}`}>submission #{row.submission_id}</Link>
                {" · "}attempt #{row.attempt_id}
              </div>
              {row.code_flags.length > 0 && (
                <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                  {row.code_flags.map(f => <span key={f} className="badge badge-hard">{f}</span>)}
                </div>
              )}
              <div style={{ marginTop: 6 }}>
                {row.speed_flag
                  ? <><b style={{ color: "var(--bad)" }}>{row.ratio}× faster</b> than the median of {row.n_peers} peers
                      {" "}({ms(row.best_runtime_ms)} vs {ms(row.peer_median_ms)})</>
                  : row.peer_median_ms !== null
                    ? <span className="muted">{ms(row.best_runtime_ms)} against a peer median of
                        {" "}{ms(row.peer_median_ms)} ({row.n_peers} peers){row.flagged ? "" : "; no flag"}</span>
                    : <span className="muted">{ms(row.best_runtime_ms)}; too few peers to compare.</span>}
              </div>
            </div>

            {detail ? (
              <>
                <details style={{ marginTop: 10 }}>
                  <summary>Problem statement</summary>
                  <Markdown>{detail.problem.content}</Markdown>
                </details>
                <div className="review-code">
                  <div className="col" style={{ gap: 4, minWidth: 0 }}>
                    <div className="muted">This solution</div>
                    <pre className="code">{detail.code}</pre>
                  </div>
                  {showTypical && detail.typical && (
                    <div className="col" style={{ gap: 4, minWidth: 0 }}>
                      <div className="muted">
                        Typical solution: {detail.typical.model_name}, {ms(detail.typical.best_runtime_ms)}
                      </div>
                      <pre className="code">{detail.typical.code}</pre>
                    </div>
                  )}
                </div>
                {detail.typical && (
                  <button className="secondary" style={{ alignSelf: "flex-start", marginTop: 8 }}
                          onClick={() => setShowTypical(v => !v)}>
                    {showTypical ? "Hide the typical solution" : "Compare with a typical solution"}
                  </button>
                )}
              </>
            ) : <div className="muted" style={{ marginTop: 10 }}>Loading…</div>}
          </div>

          <input value={note} onChange={e => setNote(e.target.value)} maxLength={2000}
                 placeholder="Note (optional), e.g. compiles C and calls it through ctypes" />
          <div className="review-actions">
            <button className="review-invalid" onClick={() => decide("invalid")} disabled={busy}>✕ Invalid</button>
            <button className="secondary" onClick={skip} disabled={busy}>Skip</button>
            <button className="secondary" onClick={undoLast} disabled={busy || undo.length === 0}>Undo</button>
            <button className="review-ok" onClick={() => decide("ok")} disabled={busy}>✓ OK</button>
          </div>
        </>
      )}
    </div>
  );
}
