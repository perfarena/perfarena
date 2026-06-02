import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useRef, useState } from "react";
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
          <Link to="/" className="brand">
            {/* /logo.png is served from public/. onError hides it gracefully
              * so the wordmark still renders if the file is missing in dev. */}
            <img src="/logo.png" alt="PerfArena"
                 onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
            PerfArena
          </Link>
          <nav>
            <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>Home</NavLink>
            <NavLink to="/rankings" className={({ isActive }) => (isActive ? "active" : "")}>Rankings</NavLink>
            {/* Compare hosts both the head-to-head view and the cross-language
                Analysis tab — Analysis used to be a top-level nav entry but
                now lives at /compare?tab=analysis. */}
            <NavLink to="/competitions" className={({ isActive }) => (isActive ? "active" : "")}>Competitions</NavLink>
            <NavLink to="/models" className={({ isActive }) => (isActive ? "active" : "")}>Models</NavLink>
            <NavLink to="/problems" className={({ isActive }) => (isActive ? "active" : "")}>Problems</NavLink>
            <NavLink to="/prompts" className={({ isActive }) => (isActive ? "active" : "")}>Prompts</NavLink>
            <NavLink to="/compare" className={({ isActive }) => (isActive ? "active" : "")}>Compare</NavLink>
            <NavLink to="/submissions" className={({ isActive }) => (isActive ? "active" : "")}>Submissions</NavLink>
            <NavLink to="/snapshots" className={({ isActive }) => (isActive ? "active" : "")}>Snapshots</NavLink>
            <NavLink to="/blog" className={({ isActive }) => (isActive ? "active" : "")}>Blog</NavLink>
            <NavLink to="/about" className={({ isActive }) => (isActive ? "active" : "")}>About</NavLink>
          </nav>
        </div>
        <div className="row" style={{ gap: 12 }}>
          {me ? (
            <UserMenu me={me} onLogout={logout} />
          ) : (
            // Self-signup removed — the platform is curated by the team. Only
            // sign-in stays surfaced; legacy /register stays accessible via
            // direct URL but isn't a public path anymore.
            <NavLink to="/login">Login</NavLink>
          )}
          <ThemeToggle />
        </div>
      </header>
      <main><Outlet /></main>
      <footer className="col" style={{ gap: 4 }}>
        <div>
          Public benchmark · Solutions verified against LeetCode ·
          {" "}<a href="/docs" target="_blank" rel="noreferrer">API docs</a> ·
          {" "}<a href="/redoc" target="_blank" rel="noreferrer">ReDoc</a> ·
          {" "}<a href="/openapi.json" target="_blank" rel="noreferrer">OpenAPI spec</a> ·
          {" "}<a href="https://github.com/anthropics/claude-code" target="_blank" rel="noreferrer">source</a>
        </div>
        {/* Consolidated from the former per-page footers on Home / About. */}
        <div>
          Licensed under the Apache License 2.0. Problem statements © LeetCode;
          this site is not affiliated with LeetCode.
        </div>
      </footer>
    </div>
  );
}

/* Username dropdown — clicking the name reveals the per-user pages (My runs)
 * and, for admins, the Admin console. Logout stays as a sibling button so
 * it's always one click away. Closes on outside-click or Escape. */
function UserMenu({ me, onLogout }: { me: MePayload; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const label = `${me.display_name}${me.org ? ` · ${me.org}` : ""}`;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div className="row" style={{ gap: 8 }}>
        <button
          className="secondary"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen(o => !o)}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <span>{label}</span>
          <span aria-hidden style={{ fontSize: "0.7em" }}>{open ? "▲" : "▼"}</span>
        </button>
        <button className="secondary" onClick={onLogout}>Logout</button>
      </div>
      {open && (
        <div
          role="menu"
          className="card"
          style={{
            position: "absolute", right: 0, top: "calc(100% + 6px)",
            minWidth: 180, padding: 6, zIndex: 50,
            display: "flex", flexDirection: "column", gap: 2,
          }}
        >
          <MenuItem to="/me" onPick={() => setOpen(false)}>My runs</MenuItem>
          <MenuItem to="/me/by-language" onPick={() => setOpen(false)}>By language</MenuItem>
          <MenuItem to="/me/api-key" onPick={() => setOpen(false)}>API key</MenuItem>
          {me.role === "admin" && (
            <>
              <MenuItem to="/admin" onPick={() => setOpen(false)}>Admin</MenuItem>
              <MenuItem to="/admin/export" onPick={() => setOpen(false)}>Export &amp; Import</MenuItem>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MenuItem({
  to, onPick, children,
}: { to: string; onPick: () => void; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      onClick={onPick}
      role="menuitem"
      className={({ isActive }) => (isActive ? "active" : "")}
      style={{ padding: "6px 10px", borderRadius: 6, display: "block" }}
    >
      {children}
    </NavLink>
  );
}
