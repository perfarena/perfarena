import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, getApiKey, MePayload, setApiKey, subscribeAuth } from "../lib/api";
import ThemeToggle from "./ThemeToggle";

/* Link lists shared between the desktop dropdowns and the mobile menu, so the
 * two navigations never drift apart. */
const CATALOG_LINKS = [
  { to: "/competitions", label: "Competitions" },
  { to: "/harnesses",    label: "Harnesses" },
  { to: "/machines",     label: "Machines" },
  { to: "/problems",     label: "Problems" },
  { to: "/models",       label: "Models" },
  { to: "/languages",    label: "Languages" },
  { to: "/prompts",      label: "Prompts" },
  { to: "/submissions",  label: "Submissions" },
  { to: "/snapshots",    label: "Snapshots" },
];
const ACCOUNT_LINKS = [
  { to: "/me",             label: "My runs" },
  { to: "/me/by-language", label: "By language" },
  { to: "/me/api-key",     label: "API key" },
  { to: "/cost-estimator", label: "Cost estimator" },
];
const ADMIN_LINKS = [
  { to: "/admin",                   label: "Admin" },
  { to: "/admin/leetcode-accounts", label: "LeetCode accounts" },
  { to: "/admin/export",            label: "Export & Import" },
];

export default function Layout() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();

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

  // Close the mobile menu whenever the route changes (a link was tapped) or
  // Escape is pressed, so it never lingers over the new page.
  useEffect(() => { setMobileOpen(false); }, [loc.pathname]);
  useEffect(() => {
    if (!mobileOpen) return;
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setMobileOpen(false); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  function logout() {
    setApiKey(null);              // emits the auth-changed event → refreshMe → setMe(null)
    setMobileOpen(false);
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
          <nav className="nav-desktop">
            <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>Home</NavLink>
            <NavLink to="/rankings" className={({ isActive }) => (isActive ? "active" : "")}>Rankings</NavLink>
            {/* "Catalog" is the umbrella for the registries that used to be
                separate top tabs; collapsing them keeps the top bar focused on
                the live workflows (Rankings / Compare). */}
            <CatalogMenu />
            <NavLink to="/compare" className={({ isActive }) => (isActive ? "active" : "")}>Compare</NavLink>
            <NavLink to="/blog" className={({ isActive }) => (isActive ? "active" : "")}>Blog</NavLink>
            <NavLink to="/about" className={({ isActive }) => (isActive ? "active" : "")}>About</NavLink>
          </nav>
        </div>
        <div className="row" style={{ gap: 12 }}>
          <div className="row auth-desktop" style={{ gap: 12 }}>
            {me ? (
              <UserMenu me={me} onLogout={logout} />
            ) : (
              // Self-signup removed — the platform is curated by the team. Only
              // sign-in stays surfaced; legacy /register stays reachable by URL.
              <NavLink to="/login">Login</NavLink>
            )}
          </div>
          <ThemeToggle />
          <button
            className="nav-toggle"
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav"
            onClick={() => setMobileOpen(o => !o)}
          >
            {mobileOpen ? "✕" : "☰"}
          </button>
        </div>

        {mobileOpen && (
          <MobileNav me={me} onLogout={logout} onClose={() => setMobileOpen(false)} />
        )}
      </header>
      <main><Outlet /></main>
      <footer className="col" style={{ gap: 4 }}>
        <div>
          Open, replicable coding-model rankings · every result verified and auditable ·
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

/* Full-screen-width menu shown under the top bar on narrow viewports. It
 * flattens the desktop dropdowns (Catalog, user menu) into plain sections so
 * there are no nested pop-ups to fight with touch. */
function MobileNav({
  me, onLogout, onClose,
}: { me: MePayload | null; onLogout: () => void; onClose: () => void }) {
  const link = (to: string, label: string, end = false) => (
    <NavLink key={to} to={to} end={end} onClick={onClose}
             className={({ isActive }) => (isActive ? "active" : "")}>
      {label}
    </NavLink>
  );
  return (
    <nav id="mobile-nav" className="mobile-nav">
      {link("/", "Home", true)}
      {link("/rankings", "Rankings")}
      {link("/compare", "Compare")}
      {link("/blog", "Blog")}
      {link("/about", "About")}

      <div className="group-label">Catalog</div>
      {CATALOG_LINKS.map(l => link(l.to, l.label))}

      <hr />
      {me ? (
        <>
          <div className="group-label">{me.display_name}{me.org ? ` · ${me.org}` : ""}</div>
          {ACCOUNT_LINKS.map(l => link(l.to, l.label))}
          {me.role === "admin" && ADMIN_LINKS.map(l => link(l.to, l.label))}
          <button className="m-item" onClick={onLogout}>Logout</button>
        </>
      ) : (
        link("/login", "Login")
      )}
    </nav>
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
          {ACCOUNT_LINKS.map(l => (
            <MenuItem key={l.to} to={l.to} onPick={() => setOpen(false)}>{l.label}</MenuItem>
          ))}
          {me.role === "admin" && ADMIN_LINKS.map(l => (
            <MenuItem key={l.to} to={l.to} onPick={() => setOpen(false)}>{l.label}</MenuItem>
          ))}
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


/* "Catalog" header dropdown — umbrella for the registries we used to surface
 * as separate top-level tabs. Mirrors UserMenu's outside-click + Escape
 * behaviour. The trigger styles itself as `active` whenever the route is on
 * any sub-page so the user can tell which tab the current page belongs to. */
const CATALOG_PATHS = [
  "/competitions", "/harnesses", "/correctness-oracles", "/problems",
  "/models", "/languages", "/prompts", "/submissions", "/snapshots",
];
// /correctness-oracles stays in the highlight set because the DETAIL route
// (/correctness-oracles/:slug) is still reachable directly — the LIST route
// now redirects to /harnesses?tab=correctness, but a user landing on a
// detail deep link should still see the Catalog menu highlighted.

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
          {CATALOG_LINKS.map(l => (
            <MenuItem key={l.to} to={l.to} onPick={() => setOpen(false)}>{l.label}</MenuItem>
          ))}
        </div>
      )}
    </span>
  );
}
