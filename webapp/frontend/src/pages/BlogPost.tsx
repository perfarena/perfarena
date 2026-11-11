import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, BlogPostPublic } from "../lib/api";
import Markdown from "../components/Markdown";

/* /blog/:slug — single post. The header uses the same hero as Home.tsx
 * (logo, large title, lead text), and the markdown body is rendered with the
 * shared Markdown component in a readable column. */
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
    <article className="col" style={{ gap: 28 }}>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <img src={`${import.meta.env.BASE_URL}logo.png`} alt=""
             onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
             style={{ height: 96, width: 96, display: "block", marginBottom: 12 }} />
        <h1 style={{ fontSize: "2.4rem", lineHeight: 1.1 }}>{p.title}</h1>
        <div className="muted" style={{ fontSize: "0.9rem" }}>
          {new Date(date).toLocaleDateString()}
          {p.author_name && <> · {p.author_name}</>}
        </div>
        {p.summary && (
          <p style={{ maxWidth: 760, fontSize: "1.1rem", lineHeight: 1.5, marginTop: 12 }}>{p.summary}</p>
        )}
      </section>
      {p.cover_image_url && (
        <img src={p.cover_image_url} alt=""
             onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
             style={{ width: "100%", maxWidth: 760, maxHeight: 360, objectFit: "cover", borderRadius: 12 }} />
      )}
      <div className="blog-body" style={{ maxWidth: 760 }}>
        <Markdown>{p.body}</Markdown>
      </div>
      <div className="row">
        <Link to="/blog">← All posts</Link>
      </div>
    </article>
  );
}
