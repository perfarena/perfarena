import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, setApiKey } from "../lib/api";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = await api.login({ email, password });
      setApiKey(r.api_key);
      nav("/me");
    } catch (e: unknown) {
      setErr(String(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="col" style={{ maxWidth: 480 }}>
      <div className="hero">
        <h1>Sign in</h1>
        <p>Log in with your email and password.</p>
      </div>
      <form className="card col" onSubmit={submit}>
        <label className="col" style={{ gap: 4 }}>
          <span className="muted">Email</span>
          <input required type="email" autoComplete="username"
                 value={email} onChange={e => setEmail(e.target.value)} />
        </label>
        <label className="col" style={{ gap: 4 }}>
          <span className="muted">Password</span>
          <input required type="password" autoComplete="current-password"
                 value={password} onChange={e => setPassword(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
      </form>

      <div className="card col">
        <h2 style={{ margin: 0 }}>No account?</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          <Link to="/register">Request access</Link>. Once approved, you can sign in with the email and password you set.
        </p>
      </div>
    </div>
  );
}
