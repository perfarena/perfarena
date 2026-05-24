import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";

export default function Register() {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [org, setOrg] = useState("");
  const [motivation, setMotivation] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [captcha, setCaptcha] = useState<{ token: string; prompt: string } | null>(null);
  const [captchaAnswer, setCaptchaAnswer] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState("");

  async function refreshCaptcha() {
    try {
      const c = await api.captcha();
      setCaptcha(c);
      setCaptchaAnswer("");
    } catch (e) { setErr(String(e)); }
  }
  useEffect(() => { refreshCaptcha(); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!captcha) { setErr("captcha not loaded"); return; }
    if (password.length < 8) { setErr("password must be at least 8 chars"); return; }
    if (password !== password2) { setErr("passwords don't match"); return; }
    try {
      await api.register({
        email, display_name: displayName,
        org: org || undefined,
        motivation: motivation || undefined,
        password,
        captcha_token: captcha.token,
        captcha_answer: captchaAnswer,
      });
      setSent(true);
    } catch (e) {
      setErr(String(e));
      // Captcha is single-use — load a fresh one for the retry.
      refreshCaptcha();
    }
  }

  return (
    <div className="col" style={{ maxWidth: 540 }}>
      <div className="hero">
        <h1>Request access</h1>
        <p>Approval is manual. You'll get an API key by email once we review your registration.</p>
      </div>

      {sent
        ? <div className="card">Thanks. Your request is pending review.</div>
        : (
          <form className="card col" onSubmit={submit}>
            <label className="col" style={{ gap: 4 }}>
              <span className="muted">Email</span>
              <input required type="email" value={email} onChange={e => setEmail(e.target.value)} />
            </label>
            <label className="col" style={{ gap: 4 }}>
              <span className="muted">Display name</span>
              <input required value={displayName} onChange={e => setDisplayName(e.target.value)} />
            </label>
            <label className="col" style={{ gap: 4 }}>
              <span className="muted">Organization (optional)</span>
              <input value={org} onChange={e => setOrg(e.target.value)} />
            </label>
            <label className="col" style={{ gap: 4 }}>
              <span className="muted">Motivation (optional)</span>
              <textarea rows={3} value={motivation} onChange={e => setMotivation(e.target.value)} />
            </label>
            <label className="col" style={{ gap: 4 }}>
              <span className="muted">Password (≥ 8 chars)</span>
              <input required type="password" autoComplete="new-password" minLength={8}
                     value={password} onChange={e => setPassword(e.target.value)} />
            </label>
            <label className="col" style={{ gap: 4 }}>
              <span className="muted">Confirm password</span>
              <input required type="password" autoComplete="new-password" minLength={8}
                     value={password2} onChange={e => setPassword2(e.target.value)} />
            </label>
            <div className="col" style={{ gap: 6 }}>
              <span className="muted">Captcha</span>
              <div className="row" style={{ gap: 8 }}>
                <div className="code" style={{ flex: 1 }}>{captcha?.prompt ?? "loading…"}</div>
                <input
                  required type="text" inputMode="numeric" autoComplete="off"
                  value={captchaAnswer} onChange={e => setCaptchaAnswer(e.target.value)}
                  style={{ width: 100 }} placeholder="answer"
                />
                <button type="button" className="secondary" onClick={refreshCaptcha} title="New captcha">↻</button>
              </div>
            </div>
            <button type="submit" disabled={!captcha}>Request access</button>
            {err && <div className="muted" style={{ color: "var(--bad)" }}>{err}</div>}
          </form>
        )}

      <div className="card col">
        <h2>Already have an account?</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          <Link to="/login">Sign in</Link> with your email and password. Your API
          key is managed from your profile page after sign-in — no need to copy
          and paste it anymore.
        </p>
      </div>
    </div>
  );
}
