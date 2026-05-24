import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useState } from "react";
import { api, getApiKey, MePayload, setApiKey, subscribeAuth } from "../lib/api";
import ThemeToggle from "./ThemeToggle";

export default function Layout() {
  const [me, setMe] = useState<MePayload | null>(null);
  const nav = useNavigate();

  // Re-fetches `me` whenever the API key changes — including after Login.tsx
  // calls setApiKey() — so the nav swaps to the signed-in version immediately,
  // without needing a page reload.
  const refreshMe = useCallback(() => {
    if (!getApiKey()) { setMe(null); return; }
    api.me().then(setMe).catch(() => setMe(null));
  }, []);

  useEffect(() => {
    refreshMe();
    return subscribeAuth(refreshMe);
  }, [refreshMe]);

  function logout() {
    setApiKey(null);              // emits the auth-changed event → refreshMe → setMe(null)
    nav("/");
  }

  return (
    <div className="layout">
      <header className="top">
        <div className="row">
          <Link to="/" className="brand">PerfArena</Link>
          <nav>
            <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>Home</NavLink>
            <NavLink to="/rankings" className={({ isActive }) => (isActive ? "active" : "")}>Rankings</NavLink>
            <NavLink to="/compare" className={({ isActive }) => (isActive ? "active" : "")}>Compare</NavLink>
            <NavLink to="/catalog" className={({ isActive }) => (isActive ? "active" : "")}>Catalog</NavLink>
            <NavLink to="/prompts" className={({ isActive }) => (isActive ? "active" : "")}>Prompts</NavLink>
            <NavLink to="/problems" className={({ isActive }) => (isActive ? "active" : "")}>Problems</NavLink>
            <NavLink to="/submissions" className={({ isActive }) => (isActive ? "active" : "")}>Submissions</NavLink>
            <NavLink to="/snapshots" className={({ isActive }) => (isActive ? "active" : "")}>Snapshots</NavLink>
            <NavLink to="/about" className={({ isActive }) => (isActive ? "active" : "")}>About</NavLink>
            {me?.role === "admin" && (
              <>
                {/* Admin-only — the team runs experiments from these pages
                    (submission, progress, audit). Non-admins don't see them. */}
                <NavLink to="/submit" className={({ isActive }) => (isActive ? "active" : "")}>Submit</NavLink>
                <NavLink to="/me" className={({ isActive }) => (isActive ? "active" : "")}>My runs</NavLink>
                <NavLink to="/admin" className={({ isActive }) => (isActive ? "active" : "")}>Admin</NavLink>
              </>
            )}
          </nav>
        </div>
        <div className="row" style={{ gap: 12 }}>
          {me ? (
            <>
              <span className="muted">{me.display_name}{me.org ? ` · ${me.org}` : ""}</span>
              <button className="secondary" onClick={logout}>Logout</button>
            </>
          ) : (
            // Self-signup removed — the platform is curated by the team. Only
            // sign-in stays surfaced for admins; legacy /register stays
            // accessible via direct URL but isn't a public path anymore.
            <NavLink to="/login">Login</NavLink>
          )}
          <ThemeToggle />
        </div>
      </header>
      <main><Outlet /></main>
      <footer>
        Public benchmark · Solutions verified against LeetCode ·
        {" "}<a href="/docs" target="_blank" rel="noreferrer">API docs</a> ·
        {" "}<a href="/redoc" target="_blank" rel="noreferrer">ReDoc</a> ·
        {" "}<a href="/openapi.json" target="_blank" rel="noreferrer">OpenAPI spec</a> ·
        {" "}<a href="https://github.com/anthropics/claude-code" target="_blank" rel="noreferrer">source</a>
      </footer>
    </div>
  );
}
