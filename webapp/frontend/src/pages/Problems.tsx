import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, CompetitionPublic, ProblemPublic } from "../lib/api";
import LevelBadge from "../components/LevelBadge";

export default function Problems() {
  const [rows, setRows] = useState<ProblemPublic[]>([]);
  // Competition list for the filter dropdown — loaded once, then cached
  // in component state. We don't need n_* counts here, just slug + name,
  // but we get the full CompetitionPublic for free from the list endpoint.
  const [competitions, setCompetitions] = useState<CompetitionPublic[]>([]);
  const [q, setQ] = useState("");
  const [level, setLevel] = useState("");
  // Competition slug currently filtered on. "" means no filter. Passed
  // straight through to /api/problems?competition=<slug> so the server
  // intersects with the CompetitionProblem join before returning.
  const [competition, setCompetition] = useState("");
  const [err, setErr] = useState("");
  const [methodologyOpen, setMethodologyOpen] = useState(false);

  // Re-fetch whenever level / competition change. The search-box `q`
  // stays client-side (no round-trip on every keystroke).
  useEffect(() => {
    api.problems({ level, competition })
      .then(setRows).catch((e: unknown) => setErr(String(e)));
  }, [level, competition]);

  // Load the competition list ONCE for the filter dropdown. Hide retired
  // competitions from the picker — same default as the Competitions page.
  useEffect(() => {
    api.competitions(false).then(setCompetitions).catch(() => {
      // Soft-fail: an empty dropdown is fine, the table still works.
    });
  }, []);

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
        <select value={level} onChange={e => setLevel(e.target.value)}
                aria-label="Filter by difficulty">
          <option value="">All difficulties</option>
          <option>Easy</option><option>Medium</option><option>Hard</option>
        </select>
        <select value={competition} onChange={e => setCompetition(e.target.value)}
                aria-label="Filter by competition">
          <option value="">All competitions</option>
          {competitions.map(c => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Title</th><th>Slug</th><th>Level</th><th>Competition</th><th>Tags</th>
          </tr></thead>
          <tbody>
            {filtered.slice(0, 500).map(p => (
              <tr key={p.id}>
                <td><Link to={`/problems/${p.title_slug}`}>{p.title}</Link></td>
                <td className="muted">
                  <Link to={`/problems/${p.title_slug}`}><code>{p.title_slug}</code></Link>
                </td>
                <td><LevelBadge level={p.level} /></td>
                <td><CompetitionBadges comps={p.competitions} /></td>
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


/** Renders each competition this problem belongs to as a small pill
 *  linking to /competitions/<slug>. Empty → an em-dash (orphan problem;
 *  legacy data with no competition link). */
function CompetitionBadges({ comps }: { comps: { slug: string; name: string }[] }) {
  if (!comps || comps.length === 0) {
    return <span className="muted">none</span>;
  }
  return (
    <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
      {comps.map(c => (
        <Link key={c.slug} to={`/competitions/${c.slug}`}
              title={`Open ${c.name}`}
              style={{
                padding: "2px 8px", borderRadius: 999,
                background: "var(--panel-2)", border: "1px solid var(--border)",
                fontSize: "0.8rem", whiteSpace: "nowrap", textDecoration: "none",
              }}>
          {c.name}
        </Link>
      ))}
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
          <div style={{ fontWeight: 600 }}>How we chose these problems</div>
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
            Our parquet dataset has 1,973 LeetCode problems. Of these, 1,730
            currently have an anchor submission_id. An anchor is a previous
            submission that gives access to LeetCode's{" "}
            <code>submissionDistribution</code> endpoint. This endpoint returns
            the histogram of the runtime and memory of every accepted submission
            for that problem. We cannot anchor Premium-only problems without a
            LeetCode Premium subscription, so we exclude them. We resolve renamed
            slugs automatically through the stable
            <code> questionFrontendId</code>.
          </p>

          <p style={{ lineHeight: 1.55 }}>
            For every anchored problem we fetch the python3 runtime histogram and
            compute four signals:
          </p>
          <ul style={{ lineHeight: 1.55, marginTop: -8 }}>
            <li><b>p10 / p50 / p95</b>: the 10th, 50th, and 95th percentile runtimes of LeetCode's accepted submissions.</li>
            <li><b>spread_ms = p95 − p10</b>: absolute room (in ms) between the 90th-percentile-from-fastest and 90th-percentile-from-slowest.</li>
            <li><b>spread_ratio = p95 / p10</b>: relative room. It does not depend on the test count, so it is fairer when problems use test sets of different sizes.</li>
            <li><b>n_buckets</b>: how many distinct runtime values the histogram covers (a proxy for how many submissions LeetCode has on file).</li>
          </ul>

          <p style={{ lineHeight: 1.55 }}>
            We drop any problem that fails <em>any</em> of the three noise-floor
            filters:
          </p>
          <ul style={{ lineHeight: 1.55, marginTop: -8 }}>
            <li><code>p50 ≥ 10 ms</code>: the median submission takes long enough for the timer to matter.</li>
            <li><code>spread_ms ≥ 10 ms</code>: there is a real gap between fast and slow submissions.</li>
            <li><code>n_buckets ≥ 15</code>: the histogram has enough data for stable percentile estimates.</li>
          </ul>

          <p style={{ lineHeight: 1.55 }}>
            Of the 1,730 anchored problems, 886 pass all three filters. We drop
            610 as noise-floor cases, and 234 do not return a usable histogram.
            Of the 886 that pass, 22 are LeetCode Premium only, so we remove them
            from the candidate pool. That leaves 864 performance-discriminative
            problems: 151 Easy, 486 Medium, and 227 Hard.
          </p>

          <p style={{ lineHeight: 1.55 }}>
            We take the <b>top 33 per difficulty</b>, ranked by{" "}
            <code>spread_ratio</code> (the test-count-invariant metric). The final
            seed has 99 problems, with an even 33 / 33 / 33 split. We write the
            ranking and every signal value that we used to pick each problem
            to <code>data/runtime_ranking.json</code>, so that anyone can audit
            them. The final pick is in <code>data/sampled_problems.json</code>.
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
            <b>Caveats.</b> (1) The histograms show the LeetCode "population",
            which includes both human and AI-assisted submissions. LeetCode does
            not separate them. (2) Anchored runtimes drift over time as the
            population changes. We refresh the baselines after every real
            submission. (3) The 10 ms and 15-bucket thresholds are conservative
            defaults. See
            <code> app/rank_problems_by_runtime.py</code> for the exact filter logic
            and to tune them. (4) In the source parquet, the{" "}
            <code>title_slug</code>, <code>level</code>, and <code>tag</code>{" "}
            columns did not match the <code>id</code> and content (46% drift). We
            repaired this with <code>app/fix_parquet.py</code>, with LeetCode's
            own catalog as ground truth. We keep the original parquet as a
            timestamped backup, and we rekeyed
            <code> anchors.json</code> at the same time.
          </p>

          <p className="muted" style={{ lineHeight: 1.55, fontSize: "0.9rem" }}>
            <b>More detail.</b> See <code>docs/problem-selection.md</code> in the
            repo. It walks through every step in plain English, with live data:
            a real GraphQL query, a raw <code>submissionDistribution</code> payload,
            and the exact spread numbers for the top picks per level.
          </p>
        </div>
      )}
    </div>
  );
}
