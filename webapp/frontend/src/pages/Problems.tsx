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
        <p>There are {rows.length.toLocaleString()} problems available. Click a row to see the full statement, the statistics, and the per-problem leaderboard.</p>
      </div>

      <MethodologyCard open={methodologyOpen} onToggle={() => setMethodologyOpen(o => !o)} />

      <div className="filter-bar">
        <input placeholder="Search by title, slug, or tag…" value={q} onChange={e => setQ(e.target.value)} className="grow" />
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
        {filtered.length > 500 && <div className="empty">This table shows the first 500 of {filtered.length} matches.</div>}
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
          <div style={{ fontWeight: 600 }}>How the team chose these problems</div>
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            The top 33 problems for each difficulty. The team ranks them by runtime discrimination on the real submission histograms of LeetCode.
          </div>
        </div>
        <button className="secondary" onClick={onToggle} aria-expanded={open}>
          {open ? "Hide details" : "Show details"}
        </button>
      </div>

      {open && (
        <div className="col" style={{ gap: 12, marginTop: 14 }}>
          <p style={{ marginTop: 0, lineHeight: 1.55 }}>
            The parquet dataset has 1,973 LeetCode problems. 1,730 of them
            now have an anchor submission_id. An anchor is an earlier
            submission. It gives access to the LeetCode{" "}
            <code>submissionDistribution</code> endpoint. That endpoint gives
            the histogram of the runtime and memory of every accepted
            submission for that problem. Premium-only problems need a
            LeetCode Premium subscription. Therefore the team excludes them.
            The team resolves renamed slugs automatically with the stable
            <code> questionFrontendId</code>.
          </p>

          <p style={{ lineHeight: 1.55 }}>
            For each anchored problem, the team gets the python3 runtime
            histogram. The team then computes four signals:
          </p>
          <ul style={{ lineHeight: 1.55, marginTop: -8 }}>
            <li><b>p10 / p50 / p95</b>: the 10th, 50th, and 95th percentile runtimes of the accepted submissions on LeetCode.</li>
            <li><b>spread_ms = p95 − p10</b>: the absolute gap (in ms) between the 90th-percentile-from-fastest and the 90th-percentile-from-slowest.</li>
            <li><b>spread_ratio = p95 / p10</b>: the relative gap. This value does not depend on the number of tests. It is fairer when problems use different test-set sizes.</li>
            <li><b>n_buckets</b>: the number of distinct runtime values in the histogram. This is a proxy for the number of submissions that LeetCode has on file.</li>
          </ul>

          <p style={{ lineHeight: 1.55 }}>
            The team removes any problem that fails <em>any</em> of the three
            noise-floor filters:
          </p>
          <ul style={{ lineHeight: 1.55, marginTop: -8 }}>
            <li><code>p50 ≥ 10 ms</code>: the median submission takes enough time that the timer matters.</li>
            <li><code>spread_ms ≥ 10 ms</code>: there is a real gap between fast and slow solutions.</li>
            <li><code>n_buckets ≥ 15</code>: the histogram has enough data for stable percentile estimates.</li>
          </ul>

          <p style={{ lineHeight: 1.55 }}>
            The dataset has 1,730 anchored problems. 886 pass all three filters.
            The team drops 610 as noise-floor cases. 234 do not return a usable
            histogram. 22 of the problems that pass are LeetCode-Premium-only, so
            the team removes them from the candidate pool. This leaves 864
            performance-discriminative problems. The team divides them across the
            difficulty levels: 151 Easy, 486 Medium, and 227 Hard.
          </p>

          <p style={{ lineHeight: 1.55 }}>
            The team takes the <b>top 33 problems for each difficulty</b>. The
            team ranks them by <code>spread_ratio</code>. This metric does not
            depend on the number of tests. The result is a final seed of 99
            problems with an even 33 / 33 / 33 split. The team writes the ranking
            and every signal value for each problem to{" "}
            <code>data/runtime_ranking.json</code>. Thus anyone can examine the
            record. The final choice is in{" "}
            <code>data/sampled_problems.json</code>.
          </p>

          <p style={{ lineHeight: 1.55 }}>
            The current top picks for each level (spread_ratio shown):
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
            <b>Honest caveats.</b> (1) The LeetCode population in the histograms
            includes both human and AI-assisted submissions. LeetCode does not
            separate them. (2) Anchored runtimes drift over time because the
            population changes. The team refreshes the baselines after every real
            submission. (3) The 10 ms and 15-bucket thresholds are conservative
            defaults. Read
            <code> app/rank_problems_by_runtime.py</code> for the exact filter logic
            and to change them. (4) The source parquet shipped with{" "}
            <code>title_slug</code>, <code>level</code>, and <code>tag</code> columns
            that did not match <code>id</code> and content (46% drift). The team
            repaired this with{" "}
            <code>app/fix_parquet.py</code>. The team used the LeetCode catalog as
            the ground truth. The team keeps the original parquet as a backup with a
            timestamp. The team rekeyed
            <code> anchors.json</code> at the same time.
          </p>

          <p className="muted" style={{ lineHeight: 1.55, fontSize: "0.9rem" }}>
            <b>More detail.</b> Read <code>docs/problem-selection.md</code> in the
            repo. It is a plain-language walkthrough of every step with live data.
            It shows the real GraphQL query, the raw{" "}
            <code>submissionDistribution</code> payload, and the exact spread
            numbers for the top picks for each level.
          </p>
        </div>
      )}
    </div>
  );
}
