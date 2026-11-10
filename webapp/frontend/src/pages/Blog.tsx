import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, BlogPostCard, BlogPostIn, BlogPostPublic, getApiKey, MePayload,
} from "../lib/api";

/* /blog — public card grid of published posts. Admins also see a "Manage
 * posts" panel that lists drafts/retired and lets them author new ones
 * inline (same pattern as Models / Competitions). */
export default function Blog() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [rows, setRows] = useState<BlogPostCard[]>([]);
  const [err, setErr] = useState("");
  const [showAdmin, setShowAdmin] = useState(false);

  async function refresh() {
    try { setRows(await api.blogPosts()); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => {
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
    refresh();
  }, []);

  const isAdmin = me?.role === "admin";

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero">
        <h1>Blog</h1>
        <p>
          Updates from the team: methodology notes, model deep-dives, dataset
          changes, and leaderboard commentary. Click a card to read the full post.
        </p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {rows.length === 0 ? (
        <div className="empty">
          No posts yet.
          {isAdmin && " Use the admin panel below to publish the first one."}
        </div>
      ) : (
        <div className="blog-grid">
          {rows.map(p => <PostCard key={p.id} p={p} />)}
        </div>
      )}

      {isAdmin && (
        <div className="col" style={{ gap: 10, marginTop: 16 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ margin: 0 }}>Manage posts (admin)</h2>
            <button className="secondary" onClick={() => setShowAdmin(s => !s)}>
              {showAdmin ? "Hide admin panel" : "Show admin panel"}
            </button>
          </div>
          {showAdmin && <AdminPanel onChange={refresh} />}
        </div>
      )}
    </div>
  );
}

function PostCard({ p }: { p: BlogPostCard }) {
  const date = p.published_at ?? p.created_at;
  return (
    <Link to={`/blog/${p.slug}`} className="blog-card-link">
      <article className="card blog-card">
        {p.cover_image_url && (
          <div className="blog-cover">
            <img src={p.cover_image_url} alt=""
                 onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
          </div>
        )}
        <div className="col" style={{ gap: 6, padding: 14 }}>
          <h3 style={{ margin: 0 }}>{p.title}</h3>
          <div className="muted" style={{ fontSize: "0.8rem" }}>
            {new Date(date).toLocaleDateString()}
            {p.author_name && <> · {p.author_name}</>}
          </div>
          {p.summary && (
            <p style={{ margin: 0, fontSize: "0.95rem" }}>{p.summary}</p>
          )}
        </div>
      </article>
    </Link>
  );
}

function AdminPanel({ onChange }: { onChange: () => void }) {
  const [all, setAll] = useState<BlogPostPublic[]>([]);
  const [editing, setEditing] = useState<BlogPostPublic | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [err, setErr] = useState("");

  async function refresh() {
    try { setAll(await api.adminBlogPosts()); }
    catch (e: unknown) { setErr(String(e)); }
  }
  useEffect(() => { refresh(); }, []);

  async function onDelete(id: number) {
    if (!confirm("Delete this post?")) return;
    try {
      await api.deleteBlogPost(id);
      await refresh();
      onChange();
    } catch (e: unknown) { setErr(String(e)); }
  }

  return (
    <div className="col" style={{ gap: 10 }}>
      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button onClick={() => { setEditing(null); setShowForm(s => !s); }}>
          {showForm && editing == null ? "Cancel" : "Add post"}
        </button>
      </div>
      {(showForm || editing) && (
        <PostForm existing={editing}
                  onCancel={() => { setEditing(null); setShowForm(false); }}
                  onSaved={async () => {
                    setEditing(null); setShowForm(false);
                    await refresh(); onChange();
                  }} />
      )}
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Slug</th><th>Title</th><th>Status</th><th>Published</th><th></th>
          </tr></thead>
          <tbody>
            {all.map(p => (
              <tr key={p.id}>
                <td><code>{p.slug}</code></td>
                <td>{p.title}</td>
                <td>
                  <span className={`badge ${p.status === "published" ? "badge-easy"
                                          : p.status === "draft" ? "badge-medium" : "badge-hard"}`}>
                    {p.status}
                  </span>
                </td>
                <td className="muted">
                  {p.published_at ? new Date(p.published_at).toLocaleDateString() : "—"}
                </td>
                <td>
                  <button className="secondary" onClick={() => { setEditing(p); setShowForm(true); }}>Edit</button>
                  {" "}
                  <button className="secondary" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}
                          onClick={() => onDelete(p.id)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PostForm({
  existing, onSaved, onCancel,
}: {
  existing: BlogPostPublic | null;
  onSaved: () => void; onCancel: () => void;
}) {
  const [f, setF] = useState<BlogPostIn>(existing ? {
    slug: existing.slug, title: existing.title,
    summary: existing.summary, body: existing.body,
    cover_image_url: existing.cover_image_url,
    author_name: existing.author_name, status: existing.status,
  } : {
    slug: "", title: "", summary: "", body: "",
    cover_image_url: "", author_name: "", status: "draft",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const body: BlogPostIn = {
        ...f,
        summary: f.summary || null,
        cover_image_url: f.cover_image_url || null,
        author_name: f.author_name || null,
      };
      if (existing) await api.editBlogPost(existing.id, body);
      else await api.addBlogPost(body);
      onSaved();
    } catch (e: unknown) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <form className="card col" onSubmit={submit}>
      <h3 style={{ margin: 0 }}>{existing ? `Edit "${existing.title}"` : "New post"}</h3>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label className="col" style={{ flex: "1 1 200px" }}>
          <span className="muted">Slug (URL id)</span>
          <input required value={f.slug} onChange={e => setF({ ...f, slug: e.target.value })}
                 placeholder="kebab-case-title" />
        </label>
        <label className="col" style={{ flex: "2 1 300px" }}>
          <span className="muted">Title</span>
          <input required value={f.title} onChange={e => setF({ ...f, title: e.target.value })} />
        </label>
        <label className="col" style={{ flex: "0 0 160px" }}>
          <span className="muted">Status</span>
          <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
            <option value="draft">draft</option>
            <option value="published">published</option>
            <option value="retired">retired</option>
          </select>
        </label>
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label className="col" style={{ flex: "2 1 300px" }}>
          <span className="muted">Cover image URL (optional)</span>
          <input value={f.cover_image_url ?? ""} onChange={e => setF({ ...f, cover_image_url: e.target.value })}
                 placeholder="/img/post-cover.png" />
        </label>
        <label className="col" style={{ flex: "1 1 200px" }}>
          <span className="muted">Author (display name)</span>
          <input value={f.author_name ?? ""} onChange={e => setF({ ...f, author_name: e.target.value })}
                 placeholder="PerfArena team" />
        </label>
      </div>
      <label className="col">
        <span className="muted">Summary (one-paragraph teaser that the card shows)</span>
        <textarea rows={2} value={f.summary ?? ""}
                  onChange={e => setF({ ...f, summary: e.target.value })} />
      </label>
      <label className="col">
        <span className="muted">Body (Markdown)</span>
        <textarea rows={10} className="code" value={f.body ?? ""}
                  onChange={e => setF({ ...f, body: e.target.value })}
                  placeholder="# Heading\n\nFull post body in markdown." />
      </label>
      <div className="row" style={{ gap: 6 }}>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}
    </form>
  );
}
