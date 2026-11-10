import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, BlogPostPublic } from "../lib/api";

/* /blog/:slug — single post. Body is rendered as plain markdown text (no
 * external markdown lib pulled in; whitespace + headings are preserved via
 * `white-space: pre-wrap`). */
export default function BlogPostPage() {
  const { slug = "" } = useParams();
  const [p, setP] = useState<BlogPostPublic | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.blogPost(slug).then(setP).catch((e: unknown) => setErr(String(e)));
  }, [slug]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!p) return <div className="empty">Loading…</div>;

  const date = p.published_at ?? p.created_at;
  return (
    <article className="col" style={{ gap: 16, maxWidth: 760 }}>
      <Link to="/blog" className="muted">← All posts</Link>
      {p.cover_image_url && (
        <img src={p.cover_image_url} alt=""
             onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
             style={{ width: "100%", maxHeight: 360, objectFit: "cover", borderRadius: 8 }} />
      )}
      <header className="col" style={{ gap: 6 }}>
        <h1 style={{ marginBottom: 0 }}>{p.title}</h1>
        <div className="muted" style={{ fontSize: "0.9rem" }}>
          {new Date(date).toLocaleDateString()}
          {p.author_name && <> · {p.author_name}</>}
        </div>
      </header>
      {p.summary && (
        <p style={{ fontSize: "1.1rem", lineHeight: 1.5, fontStyle: "italic" }}>{p.summary}</p>
      )}
      <div className="blog-body" style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
        {p.body}
      </div>
    </article>
  );
}
