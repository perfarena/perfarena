import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ProblemDetail } from "../lib/api";
import LevelBadge from "../components/LevelBadge";
import Markdown from "../components/Markdown";
import { formatEnergy, formatMemory, formatPercentile, formatRuntime } from "../lib/format";
import LanguageLink from "../components/LanguageLink";

export default function ProblemDetailPage() {
  const { slug = "" } = useParams();
  const [p, setP] = useState<ProblemDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.problemDetail(slug).then(setP).catch((e: unknown) => setErr(String(e)));
  }, [slug]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!p) return <div className="empty">Loading…</div>;

  const passRate = p.n_attempts > 0 ? (p.n_accepted / p.n_attempts) * 100 : null;

  return (
    <div className="col">
      <div>
        <div className="row" style={{ gap: 16, alignItems: "baseline", flexWrap: "wrap" }}>
          <h1 style={{ marginBottom: 0 }}>{p.title}</h1>
          <LevelBadge level={p.level} />
          {p.original_slug && (
            /* Anti-contamination variant indicator. Visible only to humans
             * loading the SPA — the model being evaluated never sees
             * this; it only sees the variant's content + snippets via
             * the prompt the sweep script builds. The badge links back
             * to the canonical problem so reviewers can compare the
             * rewrite against the source at one click. */
            <Link to={`/problems/${p.original_slug}`}
                  title={`This is a rephrased variant of "${p.original_title}". Click to view the original problem.`}
                  style={{
                    padding: "2px 10px", borderRadius: 999,
                    background: "var(--panel-2)",
                    border: "1px solid var(--accent)",
                    color: "var(--accent)",
                    fontSize: "0.8rem", whiteSpace: "nowrap",
                    textDecoration: "none",
                  }}>
              ↩ variant of <b>{p.original_title}</b>
            </Link>
          )}
        </div>
        <div className="muted"><code>{p.title_slug}</code> · {p.tags.join(", ")}</div>
        {p.original_function_name && p.function_name && (
          <div className="muted" style={{ fontSize: "0.85rem", marginTop: 4 }}>
            Function name <code>{p.function_name}</code> is rewritten back to{" "}
            <code>{p.original_function_name}</code> in submitted code before dispatch to LeetCode.
          </div>
        )}
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="LeetCode tests" value={p.testcases?.toString() ?? "n/a"} />
        <Stat label="Submissions" value={p.n_attempts.toString()} />
        <Stat label="Accepted" value={`${p.n_accepted} / ${p.n_attempts}`} />
        <Stat label="Pass rate" value={passRate == null ? "n/a" : `${passRate.toFixed(1)}%`} />
      </div>

      <div className="card">
        <h2>Problem statement</h2>
        <Markdown>{p.content}</Markdown>
      </div>

      {p.hints && (
        <div className="card">
          <h2>Hints</h2>
          <Markdown>{p.hints}</Markdown>
        </div>
      )}

      <StarterCode snippets={p.code_snippets} />

      <BestPerLanguage p={p} />
      <TopSolutions p={p} />
    </div>
  );
}

/* LeetCode's starter-code stubs per language — the exact class + method
 * signature their judge expects. Populated by `app.fetch_code_snippets`
 * server-side and exposed via /api/problems/{slug}. Renders a tab strip
 * with python3 first when available, an alphabetical-by-langSlug spill for
 * the rest, the selected snippet in a monospace block, and a Copy button
 * — so a submitter can grab the right shape without a roundtrip to LeetCode. */
const LANG_LABEL: Record<string, string> = {
  python3: "Python 3", python: "Python", cpp: "C++", c: "C",
  java: "Java", csharp: "C#", javascript: "JavaScript",
  typescript: "TypeScript", golang: "Go", kotlin: "Kotlin",
  swift: "Swift", scala: "Scala", ruby: "Ruby", php: "PHP",
  rust: "Rust", dart: "Dart", elixir: "Elixir", erlang: "Erlang",
  racket: "Racket",
};
// Preferred sort order — system-supported languages first, then alpha.
const LANG_ORDER = [
  "python3", "cpp", "java", "javascript", "typescript", "golang",
  "rust", "csharp", "kotlin", "swift", "scala", "ruby", "php", "c",
];

function StarterCode({ snippets }: { snippets: Record<string, string> }) {
  const langs = Object.keys(snippets || {});
  // Sort: known languages in LANG_ORDER first, then alphabetical for the rest.
  langs.sort((a, b) => {
    const ia = LANG_ORDER.indexOf(a); const ib = LANG_ORDER.indexOf(b);
    if (ia >= 0 && ib >= 0) return ia - ib;
    if (ia >= 0) return -1;
    if (ib >= 0) return 1;
    return a.localeCompare(b);
  });
  const [active, setActive] = useState(langs[0] || "");
  const [copied, setCopied] = useState(false);

  if (langs.length === 0) {
    return (
      <div className="card">
        <h2>Starter code</h2>
        <p className="muted" style={{ marginTop: 4 }}>
          No starter snippet on file yet. Run
          {" "}<code>python -m app.fetch_code_snippets --sampled-only</code>{" "}
          to populate per-language stubs.
        </p>
      </div>
    );
  }

  const code = snippets[active] ?? "";

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — fail silent */
    }
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>Starter code</h2>
        <button className="secondary" onClick={copy}>
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>
        The exact class + method shape LeetCode's judge expects. A model that
        ignores this and renames the method (or drops the <code>Solution</code>
        wrapper) will fail every test case with "method not found" regardless
        of how correct its algorithm is. Prompts for this problem should embed
        the snippet for the target language.
      </p>
      <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 8, marginBottom: 8 }}>
        {langs.map(l => (
          <button
            key={l}
            className={l === active ? "" : "secondary"}
            onClick={() => setActive(l)}
            style={{ padding: "4px 10px", fontSize: "0.85rem" }}
          >
            {LANG_LABEL[l] ?? l}
          </button>
        ))}
      </div>
      <pre className="code" style={{ margin: 0, padding: 12, overflowX: "auto" }}>
        {code}
      </pre>
    </div>
  );
}

function BestPerLanguage({ p }: { p: ProblemDetail }) {
  if (p.best_per_language.length === 0) return null;
  // Energy column is only meaningful when at least one row in scope reports
  // it (i.e. a submission scored by an execution-instrumented harness like
  // arena-local). LeetCode-only problems leave every row null; the column
  // stays hidden. Same show/hide pattern as SubmissionsTable / Rankings.
  const showEnergy = p.best_per_language.some(
    s => s.runtime_energy_j != null || s.avg_runtime_energy_j != null,
  );
  return (
    <div>
      <h2>Best per language</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        Best judged solution in each language for this problem. Counts on the
        right are total judged attempts (any status) / accepted attempts in
        that language.
      </p>
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Lang</th><th>Model</th><th>Submitter</th>
            <th>Status</th>
            <th title="LeetCode-reported runtime (ms)">Runtime</th>
            <th title="Peak (max resident) memory (MB). Peak on both oracles, never per function call">Peak mem</th>
            {showEnergy && <th title="Runtime energy (Joules). Reported by execution-instrumented harnesses (e.g. arena-local). Lower = less energy consumed executing this solution.">Energy</th>}
            <th title="Runtime percentile in that language">RT %ile</th>
            <th title="Memory percentile in that language">Mem %ile</th>
            <th title="Accepted / total judged attempts in this language">A/T</th>
            <th></th>
          </tr></thead>
          <tbody>
            {p.best_per_language.map(s => (
              <tr key={s.submission_id + ":" + s.language}>
                <td><LanguageLink name={s.language} /></td>
                <td>
                  <Link to={`/models/${encodeURIComponent(s.model_name)}`}>
                    <b>{s.model_name}</b>
                  </Link>
                  <span className="muted"> {s.model_version}</span>
                </td>
                <td><Link to={`/submitters/${s.submitter_slug}`}>{s.submitter}</Link></td>
                <td><span className={`badge ${s.status_msg === "Accepted" ? "badge-easy" : "badge-hard"}`}>{s.status_msg}</span></td>
                <td className="metric">{formatRuntime(s.runtime_ms)}</td>
                <td className="metric">{formatMemory(s.memory_mb)}</td>
                {showEnergy && (
                  <td className="metric">{formatEnergy(s.runtime_energy_j)}</td>
                )}
                <td className="metric">{formatPercentile(s.runtime_percentile)}</td>
                <td className="metric">{formatPercentile(s.memory_percentile)}</td>
                <td className="metric">
                  {p.n_accepted_by_language[s.language] ?? 0}/{p.n_attempts_by_language[s.language] ?? 0}
                </td>
                <td><Link to={`/submissions/${s.submission_id}`}>batch →</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TopSolutions({ p }: { p: ProblemDetail }) {
  const [lang, setLang] = useState<string>("");
  const languages = Object.keys(p.n_attempts_by_language).sort();
  const visible = lang
    ? p.top_solutions.filter(s => s.language === lang)
    : p.top_solutions;
  // Same show/hide rule as "Best per language" above.
  const showEnergy = visible.some(
    s => s.runtime_energy_j != null || s.avg_runtime_energy_j != null,
  );
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>Top solutions on this problem</h2>
        {languages.length > 1 && (
          <select value={lang} onChange={e => setLang(e.target.value)}>
            <option value="">All languages</option>
            {languages.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        )}
      </div>
      {visible.length === 0 ? (
        <div className="empty">No judged solutions {lang ? `in ${lang}` : ""} for this problem yet.</div>
      ) : (
        <>
          <div className="muted" style={{ fontSize: "0.82rem", marginBottom: 8, lineHeight: 1.45 }}>
            Each row reports the <b>best of 5 dispatch repeats</b> for that
            attempt (headline columns), with the <b>mean across all 5</b> in
            parentheses for context. LeetCode's per-run runtime is observably
            noisy (~4 pp percentile spread within a single attempt in our
            data; see <code>docs/leetcode-percentile-semantics.md</code>).
            Best-of-N gives the most stable read on what the code can do.
          </div>
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>#</th><th>Model</th><th>Submitter</th><th>Lang</th>
                <th>Status</th>
                <th title="Best LeetCode runtime across the 5 dispatch repeats (min ms); the mean is shown in parens. Lower is better.">Runtime (best · avg)</th>
                <th title="Best peak (max resident) memory across the 5 dispatch repeats (min MB); the mean is shown in parens. Peak on both oracles, never per function call. Lower is better.">Peak mem (best · avg)</th>
                {showEnergy && <th title="Runtime energy in Joules. Best (least energy) across dispatch repeats, mean in parens. Reported by execution-instrumented harnesses (e.g. arena-local). Lower is better.">Energy (best · avg)</th>}
                <th title="Highest LeetCode runtime percentile across the 5 dispatch repeats; the mean is shown in parens. Higher = faster than more of the LeetCode population AT THE TIME OF SUBMISSION (LC's percentile field is itself noisy; see docs/leetcode-percentile-semantics.md).">RT %ile (best · avg)</th>
                <th title="Highest LeetCode memory percentile across the 5 dispatch repeats; mean in parens.">Mem %ile (best · avg)</th>
                <th></th>
              </tr></thead>
              <tbody>
                {visible.map((s, i) => (
                  <tr key={s.submission_id + ":" + i}>
                    <td>{i + 1}</td>
                    <td>
                      <Link to={`/models/${encodeURIComponent(s.model_name)}`}>
                        <b>{s.model_name}</b>
                      </Link>
                      <span className="muted"> {s.model_version}</span>
                    </td>
                    <td><Link to={`/submitters/${s.submitter_slug}`}>{s.submitter}</Link></td>
                    <td><LanguageLink name={s.language} /></td>
                    <td><span className={`badge ${s.status_msg === "Accepted" ? "badge-easy" : "badge-hard"}`}>{s.status_msg}</span></td>
                    <td className="metric">
                      {formatRuntime(s.runtime_ms)}
                      {s.avg_runtime_ms != null && s.avg_runtime_ms !== s.runtime_ms && (
                        <span className="muted" style={{ marginLeft: 4 }}>· {formatRuntime(s.avg_runtime_ms)}</span>
                      )}
                    </td>
                    <td className="metric">
                      {formatMemory(s.memory_mb)}
                      {s.avg_memory_mb != null && s.avg_memory_mb !== s.memory_mb && (
                        <span className="muted" style={{ marginLeft: 4 }}>· {formatMemory(s.avg_memory_mb)}</span>
                      )}
                    </td>
                    {showEnergy && (
                      <td className="metric">
                        {formatEnergy(s.runtime_energy_j)}
                        {s.avg_runtime_energy_j != null && s.avg_runtime_energy_j !== s.runtime_energy_j && (
                          <span className="muted" style={{ marginLeft: 4 }}>· {formatEnergy(s.avg_runtime_energy_j)}</span>
                        )}
                      </td>
                    )}
                    <td className="metric">
                      {formatPercentile(s.runtime_percentile)}
                      {s.avg_runtime_percentile != null && s.avg_runtime_percentile !== s.runtime_percentile && (
                        <span className="muted" style={{ marginLeft: 4 }}>· {formatPercentile(s.avg_runtime_percentile)}</span>
                      )}
                    </td>
                    <td className="metric">
                      {formatPercentile(s.memory_percentile)}
                      {s.avg_memory_percentile != null && s.avg_memory_percentile !== s.memory_percentile && (
                        <span className="muted" style={{ marginLeft: 4 }}>· {formatPercentile(s.avg_memory_percentile)}</span>
                      )}
                    </td>
                    <td><Link to={`/submissions/${s.submission_id}`}>batch →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 140 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
