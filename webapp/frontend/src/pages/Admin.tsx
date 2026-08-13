import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, MePayload, PendingUser } from "../lib/api";

/** Admin-only — grant or deny access to pending registrants. */
export default function Admin() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [pending, setPending] = useState<PendingUser[]>([]);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  // Surfaced once after approval — the fresh API key the user can use to log
  // in (or hand over to legacy users who didn't set a password at register).
  const [issued, setIssued] = useState<{ user: PendingUser; api_key: string } | null>(null);

  async function refresh() {
    try { setPending(await api.adminPending()); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
    refresh();
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
    if (!confirm(`Reject ${u.email}? This is permanent.`)) return;
    setBusyId(u.id); setErr("");
    try {
      await api.adminReject(u.id);
      await refresh();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusyId(null); }
  }

  if (me && me.role !== "admin") {
    return <div className="card" style={{ borderColor: "var(--bad)" }}>This page is for administrators only.</div>;
  }

  return (
    <div className="col">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>Admin</h1>
        <p>
          Review each pending registration and grant access. An approved
          user gets an API key. The user can sign in with an email address
          and a password, if the user set a password at registration. The{" "}
          <Link to="/admin/export">Export and Import</Link> page downloads a
          JSON bundle of the content of the platform. Use it to merge the
          content into another instance.
        </p>
      </div>
      <h2 style={{ marginBottom: 4 }}>Pending registrations</h2>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {issued && (
        <div className="card" style={{ borderColor: "var(--easy)", background: "rgba(120,200,120,0.08)" }}>
          <b>Approved {issued.user.display_name}</b> ({issued.user.email}).
          <div className="muted" style={{ marginTop: 4 }}>
            The user can sign in with the password that the user chose at
            registration. If the user did not set a password, give this API
            key to the user. The system shows this API key one time only:
          </div>
          <pre className="code" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{issued.api_key}</pre>
          <div className="row">
            <button className="secondary" onClick={() => navigator.clipboard.writeText(issued.api_key)}>Copy key</button>
            <button className="secondary" onClick={() => setIssued(null)}>Dismiss</button>
          </div>
        </div>
      )}

      {pending.length === 0
        ? <div className="empty">There are no pending registrations.</div>
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
