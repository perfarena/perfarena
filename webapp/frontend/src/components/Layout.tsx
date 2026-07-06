import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
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
            {/* "Catalog" is the umbrella for the six registries — Competitions,
                Problems, Models, Prompts, Submissions, Snapshots — that used
                to be separate top tabs. They share the family resemblance
                "browse named things inside the arena", so collapsing them
                keeps the top bar focused on the live workflows
                (Rankings / Compare) rather than indexes. */}
            <CatalogMenu />
            <NavLink to="/compare" className={({ isActive }) => (isActive ? "active" : "")}>Compare</NavLink>
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
          {" "}<a href="https://github.com/sanadlab/perfarena-leetcode" target="_blank" rel="noreferrer">source</a>
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
          {/* Sweep-planning tool — relevant to anyone running their own
              populations, not just admins. Lives in the user menu rather
              than the top bar because it's a tool, not a primary view. */}
          <MenuItem to="/cost-estimator" onPick={() => setOpen(false)}>Cost estimator</MenuItem>
          {me.role === "admin" && (
            <>
              <MenuItem to="/admin" onPick={() => setOpen(false)}>Admin</MenuItem>
              <MenuItem to="/admin/leetcode-accounts" onPick={() => setOpen(false)}>LeetCode accounts</MenuItem>
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


/* "Catalog" header dropdown — umbrella for the three registries we used to
 * surface as separate top-level tabs. Mirrors UserMenu's outside-click +
 * Escape behaviour so both menus feel consistent. The trigger styles
 * itself as `active` whenever the route is on any sub-page so the user
 * can tell at a glance which tab the current page belongs to. */
const CATALOG_PATHS = [
  "/competitions", "/harnesses", "/problems", "/models", "/languages",
  "/prompts", "/submissions", "/snapshots",
];

function CatalogMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const loc = useLocation();
  const active = CATALOG_PATHS.some(p => loc.pathname.startsWith(p));

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

  // Auto-close on navigation — when the route changes after a click we
  // want the menu to dismiss, not stay pinned open over the new page.
  useEffect(() => { setOpen(false); }, [loc.pathname]);

  // The wrapper is inline-block so the trigger flows in the nav row like a
  // NavLink, AND position:relative so the absolutely-positioned dropdown
  // anchors off the button rather than the viewport.
  return (
    <span ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        className={active ? "active" : ""}
        /* Only the bits that DON'T belong in the shared header.top nav rule:
         * align the caret next to the label. Spacing, font, colour all come
         * from the stylesheet so this button sits flush with the NavLinks. */
        style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
      >
        Catalog
        <span aria-hidden style={{ fontSize: "0.7em" }}>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div
          role="menu"
          className="card"
          style={{
            position: "absolute", left: 0, top: "calc(100% + 6px)",
            minWidth: 180, padding: 6, zIndex: 50,
            display: "flex", flexDirection: "column", gap: 2,
          }}
        >
          <MenuItem to="/competitions" onPick={() => setOpen(false)}>Competitions</MenuItem>
          <MenuItem to="/harnesses"    onPick={() => setOpen(false)}>Harnesses</MenuItem>
          <MenuItem to="/problems"     onPick={() => setOpen(false)}>Problems</MenuItem>
          <MenuItem to="/models"       onPick={() => setOpen(false)}>Models</MenuItem>
          <MenuItem to="/languages"    onPick={() => setOpen(false)}>Languages</MenuItem>
          <MenuItem to="/prompts"      onPick={() => setOpen(false)}>Prompts</MenuItem>
          <MenuItem to="/submissions"  onPick={() => setOpen(false)}>Submissions</MenuItem>
          <MenuItem to="/snapshots"    onPick={() => setOpen(false)}>Snapshots</MenuItem>
        </div>
      )}
    </span>
  );
}
