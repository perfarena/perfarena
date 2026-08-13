import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, CompetitionPublic, RankingEntry } from "../lib/api";
import { formatPassAt1, formatPercentile } from "../lib/format";
import LanguageLink from "../components/LanguageLink";
import PromptName from "../components/PromptName";

// Default competition the "Top of the leaderboard" preview is scoped to.
// Matches Rankings.tsx's default — the seed LC-99 contains every problem
// on a fresh install, so this is a no-op there.
const PRIMARY_COMPETITION_SLUG = "leetcode-99-perf";

export default function Home() {
  const [top, setTop] = useState<RankingEntry[]>([]);
  const [comps, setComps] = useState<CompetitionPublic[]>([]);

  useEffect(() => {
    api.rankings({ competition: PRIMARY_COMPETITION_SLUG })
      .then((rows: RankingEntry[]) => setTop(rows.slice(0, 5)))
      .catch(() => {});
    api.competitions().then(setComps).catch(() => setComps([]));
  }, []);

  const primary = comps.find(c => c.slug === PRIMARY_COMPETITION_SLUG) ?? null;
  // Other competitions (besides the primary) get their own card row below
  // the primary card so visitors discover the multi-competition story.
  const other = comps.filter(c => c.slug !== PRIMARY_COMPETITION_SLUG);

  return (
    <div className="col" style={{ gap: 28 }}>
      <section className="hero" style={{ paddingBottom: 0 }}>
        {/* Hero logo. Same /logo.png the header + favicon use; degrades to
            the wordmark below if the file isn't present. */}
        <img src="/logo.png" alt=""
             onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
             style={{ height: 96, width: 96, display: "block", marginBottom: 12 }} />
        <h1 style={{ fontSize: "2.4rem", lineHeight: 1.1 }}>
          A ranking of code models by performance
        </h1>
        <p style={{ maxWidth: 760, fontSize: "1.1rem", lineHeight: 1.5 }}>
          PerfArena ranks language models. It ranks them by the performance
          of the code that they write. Each competition has a problem set and
          two oracles. The correctness oracle finds if a solution is correct.
          The performance oracle measures the runtime, the memory, and the
          energy. Each competition has its own leaderboard. The first
          competition has the {primary?.n_problems ?? 99} LeetCode problems.
          The team can add more problem sets. The team runs each model. A run
          has three steps: the prompt, the solution, and the analysis. The
          team checks each result. Anyone can do the analysis again and get
          the same result. A hash chain keeps the full record, and no person
          can change a result later.
        </p>
        <div className="row" style={{ marginTop: 16, gap: 12, flexWrap: "wrap" }}>
          <Link to="/competitions"><button>See the competitions</button></Link>
          <Link to="/rankings"><button className="secondary">Open the leaderboard</button></Link>
          <Link to="/models"><button className="secondary">See the models</button></Link>
          <Link to="/blog"><button className="secondary">Read the blog</button></Link>
        </div>
      </section>

      <section className="col" style={{ gap: 12 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Competitions</h2>
          <Link to="/competitions" className="muted">See all competitions →</Link>
        </div>
        {comps.length === 0 ? (
          <div className="empty">No competitions yet.</div>
        ) : (
          <div className="col" style={{ gap: 12 }}>
            {primary && <CompetitionPreview c={primary} featured />}
            {other.length > 0 && (
              <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
                {other.map(c => <CompetitionPreview key={c.id} c={c} />)}
              </div>
            )}
          </div>
        )}
      </section>

      <section>
        <h2>
          Top of the leaderboard
          {primary && (
            <span className="muted" style={{ fontSize: "0.9rem", fontWeight: 400, marginLeft: 8 }}>
              · {primary.name}
            </span>
          )}
        </h2>
        {top.length === 0 ? (
          <div className="empty">This competition has no scored submissions yet.</div>
        ) : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>#</th><th>Model</th><th>Submitter</th><th>Lang</th>
                <th title="The prompt template for this run. A different prompt is a different experiment.">Prompt</th>
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
                    <td><PromptName id={r.prompt_id} name={r.prompt_name} /></td>
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
        <h2>How it works</h2>
        <div className="row" style={{ gap: 16, alignItems: "stretch", flexWrap: "wrap" }}>
          <Step n={1} title="Select a competition">
            Each <Link to="/competitions">competition</Link> has its own
            problem set and its own oracles. The first competition uses
            LeetCode. Later competitions can use other problem sets and other
            oracles. If two competitions have the same problem, they share its
            submissions.
          </Step>
          <Step n={2} title="Register a model">
            Give the team an endpoint to your model and the model
            specifications. The team adds the model to the{" "}
            <Link to="/models">model list</Link>. Each run uses a{" "}
            <Link to="/prompts">prompt</Link> from the list. The prompt does
            not change, so you can do the run again.
          </Step>
          <Step n={3} title="The team runs and scores it">
            The team runs the full process. Each solution goes to the two
            oracles. The correctness oracle gives a pass result or a fail
            result. The performance oracle gives the runtime, the memory, and
            the energy. For the LeetCode competition, LeetCode runs each
            attempt five times. This decreases the timing differences. The
            team counts each accepted solution in the pass@1 score and the
            pass@k score.
          </Step>
          <Step n={4} title="Examine the results">
            Each leaderboard changes when a new batch of results comes in.
            PerfArena keeps a full record for each attempt. The record has the
            prompt, the response, the reasoning, the signature, and the digest
            of the model. A hash chain protects the record. No person can
            change a result later.
          </Step>
        </div>
      </section>

      <section>
        <h2>The rules</h2>
        <ul className="muted" style={{ lineHeight: 1.7 }}>
          <li>Each submission is part of one competition. Each leaderboard uses the problem set of its competition.</li>
          <li>Each model uses one <Link to="/prompts">prompt template</Link> from the list. The record of the submission shows the prompt.</li>
          <li>PerfArena identifies each model with a digest. The digest works for all providers, for example Ollama sha256, HF revision SHA, or OpenAI system fingerprint.</li>
          <li>The result from the oracle is final. If a solution does not compile or does not finish in time, it is a fail.</li>
          <li>Anyone can look at the rankings. Anyone can download the full set of solutions and check the results.</li>
        </ul>
      </section>

      <section>
        <h2>Do you want the team to rank your model?</h2>
        <p>
          All results are open to everyone. The team keeps the rankings up to
          date. The team also welcomes help. A research lab, an independent
          builder, or a researcher can ask the team to add a model. Give the
          team an endpoint. The team then adds the model and monitors each
          run, from the generation to the analysis. The team can also make a
          new competition for a different problem set. Look at the{" "}
          <Link to="/about">team page</Link> to contact the team.
        </p>
      </section>
    </div>
  );
}

function CompetitionPreview({
  c, featured = false,
}: { c: CompetitionPublic; featured?: boolean }) {
  return (
    <Link to={`/competitions/${c.slug}`}
          style={{ textDecoration: "none", color: "inherit",
                   flex: featured ? "1 1 100%" : "1 1 280px",
                   minWidth: featured ? undefined : 260 }}>
      <article className="card col" style={{
        gap: 8,
        ...(featured ? {
          borderColor: "var(--accent)",
          background: "var(--accent-soft)",
        } : {}),
      }}>
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <h3 style={{ margin: 0 }}>
            {c.name}
            {featured && (
              <span className="badge badge-status"
                    style={{ fontSize: "0.7rem", marginLeft: 8, verticalAlign: "middle" }}>
                featured
              </span>
            )}
          </h3>
          <span className="muted" style={{ fontSize: "0.85rem" }}>{c.kind}</span>
        </div>
        {c.description && (
          <div className="muted" style={{ fontSize: "0.9rem", whiteSpace: "pre-wrap" }}>
            {c.description}
          </div>
        )}
        <div className="row" style={{ gap: 16, marginTop: 4, flexWrap: "wrap" }}>
          <Mini label="problems"    value={String(c.n_problems)} />
          <Mini label="submissions" value={String(c.n_submissions)} />
          <Mini label="models"      value={String(c.n_models)} />
        </div>
      </article>
    </Link>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="col" style={{ gap: 2 }}>
      <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
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
