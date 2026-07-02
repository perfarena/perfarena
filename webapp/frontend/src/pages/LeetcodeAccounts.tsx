import { useEffect, useState } from "react";
import { api, LeetCodeAccountsResponse, LeetCodeAccountStatus } from "../lib/api";

/* Admin: manage the LeetCode credential pool.
 *
 * Wraps the same operations as `python -m app.leetcode_accounts`:
 *   * list pool (fast, no LC roundtrip)
 *   * probe each account against LC's userStatus (slow, opt-in button)
 *   * add / overwrite an account by name (paste the Cookie header)
 *   * remove an account
 *   * clear the 24h exhaustion timer (per account or pool-wide)
 *
 * When the dispatcher hits a 429 it auto-exhausts the responsible account
 * for 24h. If you've waited (or have reason to believe the limit rolled
 * over), use the Clear button so the pool starts picking the account
 * again without a full-day wait.
 *
 * The probe is gated behind a button because each account costs one LC
 * GraphQL round-trip (~5s); probing the whole pool sequentially can
 * easily hit 30+ seconds on a 5-account setup.
 */

export default function LeetcodeAccounts() {
  const [pool, setPool] = useState<LeetCodeAccountsResponse | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // Probing happens on demand. We keep the probed state separate so a
  // routine refresh doesn't blow away the freshly-probed flags.
  const [lastProbedAt, setLastProbedAt] = useState<string | null>(null);
  // Add-form state. The two cookies are entered separately because that's
  // easier to grab from DevTools → Application → Cookies (two single-click
  // copies) than picking the LEETCODE_SESSION / csrftoken pair out of a
  // concatenated Cookie request header.
  const [name, setName] = useState("");
  const [sessionToken, setSessionToken] = useState("");
  const [csrfToken, setCsrfToken] = useState("");

  const refresh = async () => {
    setErr("");
    try { setPool(await api.leetcodeAccounts()); }
    catch (e) { setErr(String(e)); }
  };

  useEffect(() => { void refresh(); }, []);

  const probe = async () => {
    setErr(""); setBusy(true);
    try {
      const r = await api.leetcodeAccountsProbe();
      setPool(r);
      setLastProbedAt(new Date().toLocaleTimeString());
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  const add = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = await api.addLeetcodeAccount({
        name: name.trim(),
        session_token: sessionToken.trim(),
        csrf_token: csrfToken.trim(),
      });
      setPool(r);
      setName(""); setSessionToken(""); setCsrfToken("");
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  const remove = async (n: string) => {
    if (!confirm(`Remove account '${n}' from the pool? This can't be undone (you'll need the cookie to re-add it).`)) return;
    setErr(""); setBusy(true);
    try {
      await api.removeLeetcodeAccount(n);
      await refresh();
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  const clearExhaustion = async (n: string) => {
    setErr(""); setBusy(true);
    try {
      const r = await api.clearLeetcodeAccountExhaustion(n);
      setPool(r);
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <div className="hero">
        <h1>LeetCode accounts</h1>
        <p>
          The credential pool that dispatch uses to submit solutions to LeetCode. Each
          account rotates after a 429 (24h cooldown by default). If the pool runs dry,
          new dispatches synthesize "Auth failed" runs until at least one account is
          available again.
        </p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", padding: 12, marginBottom: 12 }}>{err}</div>}

      {/* Pool summary banner */}
      {pool && (
        <div className="card" style={{ padding: 12, marginBottom: 12, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "center" }}>
          <SummaryStat label="Total" value={pool.total} />
          <SummaryStat label="Available" value={pool.available} good={pool.available > 0} />
          <SummaryStat label="Exhausted" value={pool.exhausted} bad={pool.exhausted > 0} />
          {pool.earliest_available_after && (
            <SummaryStat
              label="Earliest rollover"
              value={new Date(pool.earliest_available_after).toLocaleString()}
              compact
            />
          )}
          <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            <button onClick={refresh} disabled={busy} className="secondary">Refresh</button>
            <button onClick={probe} disabled={busy} className="secondary"
                    title="Probes LC's userStatus once per account. Takes ~5s per account.">
              Probe LC {lastProbedAt && <span className="muted" style={{ fontSize: "0.8em" }}>(last {lastProbedAt})</span>}
            </button>
            {pool.exhausted > 0 && (
              <button onClick={() => clearExhaustion("*")} disabled={busy}
                      title="Clears the exhaustion timer on every account at once.">
                Clear all exhaustions
              </button>
            )}
          </div>
        </div>
      )}

      {/* Accounts table */}
      <div className="card tbl-scroll" style={{ padding: 0, marginBottom: 12 }}>
        <table className="tbl">
          <thead><tr>
            <th>Name</th>
            <th>State</th>
            <th>Exhausted until</th>
            <th title="Populated by the Probe button.">LC sign-in</th>
            <th></th>
          </tr></thead>
          <tbody>
            {pool && pool.accounts.length === 0 && (
              <tr><td colSpan={5} className="empty" style={{ padding: 16 }}>
                Pool is empty. Add an account using the form below.
              </td></tr>
            )}
            {pool?.accounts.map(a => (
              <AccountRow key={a.name} a={a} busy={busy}
                onRemove={() => remove(a.name)}
                onClear={() => clearExhaustion(a.name)} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Add form */}
      <div className="card" style={{ padding: 16 }}>
        <h3 style={{ marginTop: 0 }}>Add or refresh an account</h3>
        <p className="muted" style={{ lineHeight: 1.5, fontSize: "0.9rem" }}>
          In a browser logged into leetcode.com, open DevTools →{" "}
          <b>Application</b> → <b>Cookies</b> → <code>https://leetcode.com</code>.
          Copy the value of <code>LEETCODE_SESSION</code> into the Session field
          and the value of <code>csrftoken</code> into the CSRF field. Reusing an
          existing name overwrites the stored credentials and clears that
          account's exhaustion timer.
        </p>
        <form onSubmit={add} style={{ display: "grid", gap: 10 }}>
          <label>
            <div className="muted" style={{ fontSize: "0.8rem" }}>Name</div>
            <input type="text" required value={name} onChange={e => setName(e.target.value)}
                   placeholder="e.g. primary, alt1, …"
                   style={{ width: 240 }}
                   autoComplete="off" />
          </label>
          <label>
            <div className="muted" style={{ fontSize: "0.8rem" }}>
              <code>LEETCODE_SESSION</code> token
            </div>
            <textarea required value={sessionToken}
                      onChange={e => setSessionToken(e.target.value)}
                      rows={3}
                      placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ…"
                      spellCheck={false}
                      autoComplete="off"
                      style={{
                        width: "100%", fontFamily: "ui-monospace, monospace",
                        fontSize: "0.8rem",
                      }} />
            <div className="muted" style={{ fontSize: "0.75rem", marginTop: 2 }}>
              Long JWT (~600–900 chars, two dots). Don't include the
              "<code>LEETCODE_SESSION=</code>" prefix.
            </div>
          </label>
          <label>
            <div className="muted" style={{ fontSize: "0.8rem" }}>
              <code>csrftoken</code>
            </div>
            <input type="text" required value={csrfToken}
                   onChange={e => setCsrfToken(e.target.value)}
                   placeholder="fIDlbYpcjfdG5pEOFN0T…"
                   spellCheck={false}
                   autoComplete="off"
                   style={{
                     width: "100%", fontFamily: "ui-monospace, monospace",
                     fontSize: "0.85rem",
                   }} />
            <div className="muted" style={{ fontSize: "0.75rem", marginTop: 2 }}>
              ~32–64 hex characters. Don't include the
              "<code>csrftoken=</code>" prefix.
            </div>
          </label>
          <div>
            <button type="submit"
                    disabled={busy || !name.trim() || !sessionToken.trim() || !csrfToken.trim()}>
              Save
            </button>
          </div>
        </form>
      </div>

      <div className="muted" style={{ fontSize: "0.85rem", marginTop: 12, lineHeight: 1.5 }}>
        <b>Tip.</b> Multiple accounts give the dispatcher room to rotate around 429s.
        With one account, every 429 kills the sweep until the cooldown rolls over.
      </div>
    </div>
  );
}


function SummaryStat({
  label, value, good, bad, compact,
}: {
  label: string; value: number | string;
  good?: boolean; bad?: boolean; compact?: boolean;
}) {
  const colour = good ? "var(--good)" : bad ? "var(--bad)" : undefined;
  return (
    <div>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</div>
      <div style={{
        fontSize: compact ? "0.95rem" : "1.4rem",
        fontWeight: 600, color: colour,
        fontVariantNumeric: "tabular-nums",
      }}>{value}</div>
    </div>
  );
}


function AccountRow({
  a, busy, onRemove, onClear,
}: {
  a: LeetCodeAccountStatus; busy: boolean;
  onRemove: () => void; onClear: () => void;
}) {
  return (
    <tr>
      <td><code>{a.name}</code></td>
      <td>
        {a.is_exhausted
          ? <span style={{ color: "var(--bad)" }}>Exhausted</span>
          : <span style={{ color: "var(--good)" }}>Available</span>}
      </td>
      <td className="muted">
        {a.exhausted_until
          ? new Date(a.exhausted_until).toLocaleString()
          : "—"}
      </td>
      <td>
        {a.is_signed_in === null ? (
          <span className="muted">not probed</span>
        ) : a.is_signed_in ? (
          <span style={{ color: "var(--good)" }}>
            ✓ {a.username ?? "signed in"}
          </span>
        ) : (
          <span style={{ color: "var(--bad)" }}
                title={a.probe_message ?? ""}>
            ✗ {a.probe_message ?? "not signed in"}
          </span>
        )}
      </td>
      <td>
        <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
          {a.is_exhausted && (
            <button onClick={onClear} disabled={busy} className="secondary">
              Clear timer
            </button>
          )}
          <button onClick={onRemove} disabled={busy} className="secondary">
            Remove
          </button>
        </div>
      </td>
    </tr>
  );
}
