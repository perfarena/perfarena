import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ProblemPublic, RankingEntry } from "../lib/api";
import { formatPassAt1, formatPercentile } from "../lib/format";
import LanguageLink from "../components/LanguageLink";

export default function Home() {
  const [top, setTop] = useState<RankingEntry[]>([]);
  const [problemCount, setProblemCount] = useState<number | null>(null);

  useEffect(() => {
    api.rankings().then((rows: RankingEntry[]) => setTop(rows.slice(0, 5))).catch(() => {});
    api.problems().then((rows: ProblemPublic[]) => setProblemCount(rows.length)).catch(() => {});
  }, []);

  return (
    <div className="col" style={{ gap: 28 }}>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <h1 style={{ fontSize: "2.4rem", lineHeight: 1.1 }}>A curated LLM coding benchmark.</h1>
        <p style={{ maxWidth: 760, fontSize: "1.1rem", lineHeight: 1.5 }}>
          The team runs every model itself, against the same {problemCount?.toLocaleString() ?? "curated set of"} LeetCode problems and the same prompts.
          LeetCode is the verification + performance oracle; we publish pass@1, pass@k,
          runtime percentile, and memory percentile across languages and difficulty levels —
          all reproducibly, with provenance traces anchored in an append-only hash chain.
        </p>
        <div className="row" style={{ marginTop: 16, gap: 12 }}>
          <Link to="/rankings"><button>View the leaderboard</button></Link>
          <Link to="/catalog"><button className="secondary">Models we run</button></Link>
          <Link to="/prompts"><button className="secondary">Prompts we use</button></Link>
        </div>
      </section>

      <section>
        <h2>Top of the leaderboard</h2>
        {top.length === 0 ? (
          <div className="empty">No judged submissions yet.</div>
        ) : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>#</th><th>Model</th><th>Submitter</th><th>Lang</th>
                <th>pass@1</th><th>Runtime %ile</th><th>Memory %ile</th><th></th>
              </tr></thead>
              <tbody>
                {top.map(r => (
                  <tr key={r.submission_id}>
                    <td>{r.rank}</td>
                    <td>
                      <Link to={`/models/${encodeURIComponent(r.model_name)}`}>
                        <b>{r.model_name}</b>
                      </Link>
                      <span className="muted"> {r.model_version}</span>
                    </td>
                    <td><Link to={`/submitters/${r.submitter_slug}`}>{r.submitter}</Link></td>
                    <td><LanguageLink name={r.language} /></td>
                    <td className="metric">{formatPassAt1(r.pass_at_1)}</td>
                    <td className="metric">{formatPercentile(r.avg_runtime_percentile)}</td>
                    <td className="metric">{formatPercentile(r.avg_memory_percentile)}</td>
                    <td><Link to={`/submissions/${r.submission_id}`}>details →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="row" style={{ marginTop: 12 }}>
          <Link to="/rankings">See the full leaderboard →</Link>
        </div>
      </section>

      <section>
        <h2>How the benchmark works</h2>
        <div className="row" style={{ gap: 16, alignItems: "stretch", flexWrap: "wrap" }}>
          <Step n={1} title="Model authors reach out">
            Authors who want their model evaluated grant the team access (an API key,
            a hosted endpoint, or weights). No public self-submission — the team controls
            the evaluation environment end-to-end.
          </Step>
          <Step n={2} title="Registered + prompted">
            The model joins the <Link to="/catalog">catalog</Link>; experiments use the
            published <Link to="/prompts">prompts</Link> as-is, so every run is reproducible
            and the prompt is part of the public record.
          </Step>
          <Step n={3} title="LeetCode verifies">
            Each solution is dispatched to LeetCode for judging — 5× per attempt to smooth
            the timing noise. Accepted answers count toward pass@1 / pass@k; runtime and
            memory percentiles come straight from LeetCode's own distributions.
          </Step>
          <Step n={4} title="Auditable rankings">
            The leaderboard updates as soon as a batch lands. Every attempt carries a
            structured provenance trace (prompt, response, reasoning, signature, model
            digest) anchored in an append-only hash chain — so no result can be silently
            rewritten after the fact.
          </Step>
        </div>
      </section>

      <section>
        <h2>Ground rules</h2>
        <ul className="muted" style={{ lineHeight: 1.7 }}>
          <li>Each model uses one of the published <Link to="/prompts">prompt templates</Link>; the exact prompt is part of every submission's record.</li>
          <li>Model identity is captured with a provider-agnostic digest (Ollama sha256, HF revision SHA, OpenAI system fingerprint, etc.).</li>
          <li>LeetCode's judgement is final. Solutions that don't compile or time out count as misses.</li>
          <li>Rankings are openly browsable. The full solutions dataset is downloadable for verification.</li>
        </ul>
      </section>

      <section>
        <h2>Want your model included?</h2>
        <p>
          Open to research labs and independent builders.{" "}
          See the <Link to="/about">team page</Link> and reach out — we onboard models
          ourselves once access is granted.
        </p>
      </section>

      <section className="muted" style={{ fontSize: "0.85em", borderTop: "1px solid var(--border)", paddingTop: 12 }}>
        Licensed under the Apache License 2.0. Problem statements © LeetCode; this site is not affiliated with LeetCode.
      </section>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ flex: "1 1 240px", minWidth: 240 }}>
      <div className="muted" style={{ fontSize: "0.75rem", letterSpacing: 0.5 }}>STEP {n}</div>
      <h3 style={{ margin: "4px 0 8px" }}>{title}</h3>
      <p style={{ margin: 0, color: "var(--fg-dim)", lineHeight: 1.5 }}>{children}</p>
    </div>
  );
}
