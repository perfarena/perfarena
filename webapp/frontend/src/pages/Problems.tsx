import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ProblemPublic } from "../lib/api";
import LevelBadge from "../components/LevelBadge";

export default function Problems() {
  const [rows, setRows] = useState<ProblemPublic[]>([]);
  const [q, setQ] = useState("");
  const [level, setLevel] = useState("");
  const [err, setErr] = useState("");
  const [methodologyOpen, setMethodologyOpen] = useState(false);

  useEffect(() => {
    api.problems({ level })
      .then(setRows).catch((e: unknown) => setErr(String(e)));
  }, [level]);

  const filtered = useMemo(() => {
    const needle = q.toLowerCase();
    if (!needle) return rows;
    return rows.filter(r =>
      r.title.toLowerCase().includes(needle) ||
      r.title_slug.includes(needle) ||
      r.tags.some(t => t.toLowerCase().includes(needle)),
    );
  }, [rows, q]);

  return (
    <div>
      <div className="hero">
        <h1>Supported problems</h1>
        <p>{rows.length.toLocaleString()} problems available. Click a row for the full statement, stats, and per-problem leaderboard.</p>
      </div>

      <MethodologyCard open={methodologyOpen} onToggle={() => setMethodologyOpen(o => !o)} />

      <div className="filter-bar">
        <input placeholder="Search title, slug, or tag…" value={q} onChange={e => setQ(e.target.value)} className="grow" />
        <select value={level} onChange={e => setLevel(e.target.value)}>
          <option value="">All difficulties</option>
          <option>Easy</option><option>Medium</option><option>Hard</option>
        </select>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Title</th><th>Slug</th><th>Level</th><th>Tags</th>
          </tr></thead>
          <tbody>
            {filtered.slice(0, 500).map(p => (
              <tr key={p.id}>
                <td><Link to={`/problems/${p.title_slug}`}>{p.title}</Link></td>
                <td className="muted">
                  <Link to={`/problems/${p.title_slug}`}><code>{p.title_slug}</code></Link>
                </td>
                <td><LevelBadge level={p.level} /></td>
                <td className="muted">{p.tags.slice(0, 4).join(", ")}{p.tags.length > 4 ? " …" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 500 && <div className="empty">Showing first 500 of {filtered.length} matches.</div>}
      </div>
    </div>
  );
}


/* How the problem set was chosen.
 *
 * The set isn't a random sample — we deliberately filter for problems whose
 * LeetCode runtime distribution has *room to discriminate* (a meaningful gap
 * between fast and slow solutions). Without this filter we'd be benchmarking
 * models on problems where every accepted submission lands at the judge's
 * ~5 ms noise floor — runtime numbers would be dominated by LeetCode's
 * per-shot timing wobble, not by algorithmic merit.
 *
 * This card documents the procedure transparently so the curation isn't a
 * black box. The source of truth is `app/rank_problems_by_runtime.py`.
 */
function MethodologyCard({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 600 }}>How these problems were chosen</div>
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            Top 33 per difficulty, ranked by runtime discrimination on LeetCode's real submission histograms.
          </div>
        </div>
        <button className="secondary" onClick={onToggle} aria-expanded={open}>
          {open ? "Hide details" : "Show details"}
        </button>
      </div>

      {open && (
        <div className="col" style={{ gap: 12, marginTop: 14 }}>
          <p style={{ marginTop: 0, lineHeight: 1.55 }}>
            Across 1,973 LeetCode problems in our parquet dataset, 1,730 currently
            have an anchor submission_id (a previous submission that grants access to
            LeetCode's <code>submissionDistribution</code> endpoint — the histogram of
            every accepted submission's runtime/memory for that problem). Premium-only
            problems aren't anchorable without a LeetCode Premium subscription
            and are excluded; renamed slugs are auto-resolved via the stable
            <code> questionFrontendId</code>.
          </p>

          <p style={{ lineHeight: 1.55 }}>
            For every anchored problem we fetch the python3 runtime histogram and
            compute four signals:
          </p>
          <ul style={{ lineHeight: 1.55, marginTop: -8 }}>
            <li><b>p10 / p50 / p95</b> — the 10th, 50th, and 95th percentile runtimes of LeetCode's accepted submissions.</li>
            <li><b>spread_ms = p95 − p10</b> — absolute room (in ms) between the 90th-percentile-from-fastest and 90th-percentile-from-slowest.</li>
            <li><b>spread_ratio = p95 / p10</b> — relative room (test-count-invariant; fairer when problems use different harness sizes).</li>
            <li><b>n_buckets</b> — how many distinct runtime values the histogram covers (a proxy for how many submissions LeetCode has on file).</li>
          </ul>

          <p style={{ lineHeight: 1.55 }}>
            Any problem failing <em>any</em> of the three noise-floor filters is
            dropped from the candidate pool:
          </p>
          <ul style={{ lineHeight: 1.55, marginTop: -8 }}>
            <li><code>p50 ≥ 10 ms</code> — median submission takes long enough that the timer matters.</li>
            <li><code>spread_ms ≥ 10 ms</code> — there's a real gap between fast and slow.</li>
            <li><code>n_buckets ≥ 15</code> — the histogram is rich enough for percentile estimates to be stable.</li>
          </ul>

          <p style={{ lineHeight: 1.55 }}>
            Of the 1,730 anchored problems: 886 survive all three filters, 610 are
            dropped as noise-floor cases, 234 don't return a usable histogram, and
            22 of the survivors are LeetCode-Premium-only and removed from the
            candidate pool. That leaves 864 performance-discriminative problems
            partitioned across difficulty levels (151 Easy / 486 Medium / 227 Hard).
          </p>

          <p style={{ lineHeight: 1.55 }}>
            We take the <b>top 33 per difficulty</b> ranked by{" "}
            <code>spread_ratio</code> — the test-count-invariant metric — for a final
            seed of 99 problems with an even 33 / 33 / 33 split. The ranking and
            every signal value used to pick each problem is dumped to{" "}
            <code>data/runtime_ranking.json</code> for auditability; the final pick
            is in <code>data/sampled_problems.json</code>.
          </p>

          <p style={{ lineHeight: 1.55 }}>
            Current top picks per level (spread_ratio shown):
          </p>
          <ul style={{ lineHeight: 1.5, marginTop: -8, fontSize: "0.9rem" }}>
            <li><b>Easy:</b> <code>number-of-rectangles-that-can-form-the-largest-square</code> (2,818×),
              <code> di-string-match</code> (2,453×), <code>maximum-number-of-words-you-can-type</code> (347×)</li>
            <li><b>Medium:</b> <code>number-of-ways-where-square-of-number-is-equal-to-product-of-two-numbers</code> (1,072×),
              <code> restore-the-array-from-adjacent-pairs</code> (725×), <code>maximum-binary-string-after-change</code> (391×)</li>
            <li><b>Hard:</b> <code>minimum-number-of-days-to-eat-n-oranges</code> (2,407×),
              <code> wildcard-matching</code> (184×), <code>merge-k-sorted-lists</code> (164×)</li>
          </ul>

          <p className="muted" style={{ lineHeight: 1.55, fontSize: "0.9rem" }}>
            <b>Honest caveats.</b> (1) The LeetCode "population" the histograms reflect
            includes both human and AI-assisted submissions — LeetCode doesn't separate
            them. (2) Anchored runtimes drift over time as the population shifts; we
            refresh baselines after every real submission. (3) The 10 ms / 15-bucket
            thresholds are conservative defaults — see
            <code> app/rank_problems_by_runtime.py</code> for the exact filter logic
            and to tune them. (4) The source parquet shipped with{" "}
            <code>title_slug</code> / <code>level</code> / <code>tag</code> columns
            decoupled from <code>id</code> + content (46% drift). That was repaired by{" "}
            <code>app/fix_parquet.py</code> using LeetCode's own catalog as ground
            truth — the original parquet is preserved as a timestamped backup, and
            <code> anchors.json</code> was rekeyed in lock-step.
          </p>

          <p className="muted" style={{ lineHeight: 1.55, fontSize: "0.9rem" }}>
            <b>Want more detail?</b> See <code>docs/problem-selection.md</code> in the
            repo — a plain-English walkthrough of every step with live data
            (real GraphQL query, raw <code>submissionDistribution</code> payload,
            and the exact spread numbers for the top picks per level).
          </p>
        </div>
      )}
    </div>
  );
}
