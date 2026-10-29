import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, HarnessErrorRow, MePayload, PendingUser } from "../lib/api";

/** Admin-only — grant or deny access to pending registrants. */
export default function Admin() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [pending, setPending] = useState<PendingUser[]>([]);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  // Surfaced once after approval — the fresh API key the user can use to log
  // in (or hand over to legacy users who didn't set a password at register).
  const [issued, setIssued] = useState<{ user: PendingUser; api_key: string } | null>(null);
  const [harnessErrors, setHarnessErrors] = useState<HarnessErrorRow[]>([]);

  async function refresh() {
    try { setPending(await api.adminPending()); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
    refresh();
    api.adminHarnessErrors().then(r => setHarnessErrors(r.items)).catch(() => setHarnessErrors([]));
  }, []);

  async function approve(u: PendingUser) {
    setBusyId(u.id); setErr("");
    try {
      const { api_key } = await api.adminApprove(u.id);
      setIssued({ user: u, api_key });
      await refresh();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusyId(null); }
  }
  async function reject(u: PendingUser) {
    if (!confirm(`Reject ${u.email}? This cannot be undone.`)) return;
    setBusyId(u.id); setErr("");
    try {
      await api.adminReject(u.id);
      await refresh();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusyId(null); }
  }

  if (me && me.role !== "admin") {
    return <div className="card" style={{ borderColor: "var(--bad)" }}>Admins only.</div>;
  }

  return (
    <div className="col">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>Admin</h1>
        <p>
          Review pending registrations and grant access — approved users
          get an API key and can sign in with email + password (if they set
          one during registration). For replica work, the{" "}
          <Link to="/admin/export">Export &amp; Import</Link> page lets you
          download a JSON bundle of the platform's content and merge it back
          into another instance. The{" "}
          <Link to="/admin/speed-outliers">Too-fast outliers</Link> page flags
          accepted attempts that ran implausibly faster than their peers — a
          measurement-integrity check for solutions that skipped the workload —
          and the <Link to="/admin/review">Review</Link> page lets you label each
          of them invalid or OK.
        </p>
      </div>
      {harnessErrors.length > 0 && (
        <div className="card" style={{ borderColor: "var(--bad)" }}>
          <b style={{ color: "var(--bad)" }}>
            {harnessErrors.length} harness error{harnessErrors.length === 1 ? "" : "s"}
          </b>
          <div className="muted" style={{ marginTop: 4 }}>
            The correctness oracle accepted these solutions, but the harness failed
            to measure them. They count for pass@k but enter no ranking. Fix the
            harness, then re-measure them.
          </div>
          <div className="tbl-scroll" style={{ marginTop: 8 }}>
            <table className="tbl">
              <thead><tr>
                <th>Problem</th><th>Lang</th><th>Model</th><th>Competition</th>
                <th>Error</th><th>Submission</th>
              </tr></thead>
              <tbody>
                {harnessErrors.map(r => (
                  <tr key={r.attempt_id}>
                    <td><Link to={`/problems/${r.problem_slug}`}>{r.problem_slug}</Link></td>
                    <td><code>{r.language}</code></td>
                    <td>{r.model_name}</td>
                    <td className="muted">{r.competition ?? "–"}</td>
                    <td style={{ maxWidth: 420, whiteSpace: "pre-wrap" }}><code>{r.harness_error}</code></td>
                    <td><Link to={`/submissions/${r.submission_id}`}>#{r.submission_id}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <h2 style={{ marginBottom: 4 }}>Pending registrations</h2>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {issued && (
        <div className="card" style={{ borderColor: "var(--easy)", background: "rgba(120,200,120,0.08)" }}>
          <b>Approved {issued.user.display_name}</b> ({issued.user.email}).
          <div className="muted" style={{ marginTop: 4 }}>
            They can log in with the password they chose at registration. If
            they didn't set one (legacy flow), share this API key with them —
            it won't be shown again:
          </div>
          <pre className="code" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{issued.api_key}</pre>
          <div className="row">
            <button className="secondary" onClick={() => navigator.clipboard.writeText(issued.api_key)}>Copy key</button>
            <button className="secondary" onClick={() => setIssued(null)}>Dismiss</button>
          </div>
        </div>
      )}

      {pending.length === 0
        ? <div className="empty">No pending registrations.</div>
        : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>Registered</th><th>Email</th><th>Display name</th>
                <th>Org</th><th>Motivation</th><th></th>
              </tr></thead>
              <tbody>
                {pending.map(u => (
                  <tr key={u.id}>
                    <td className="muted">{new Date(u.created_at).toLocaleString()}</td>
                    <td><code>{u.email}</code></td>
                    <td>{u.display_name}</td>
                    <td>{u.org ?? "–"}</td>
                    <td style={{ maxWidth: 360, whiteSpace: "pre-wrap" }}>{u.motivation ?? "–"}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button onClick={() => approve(u)} disabled={busyId === u.id}>
                          {busyId === u.id ? "…" : "Approve"}
                        </button>
                        <button className="secondary" onClick={() => reject(u)} disabled={busyId === u.id}
                                style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>
                          Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}
