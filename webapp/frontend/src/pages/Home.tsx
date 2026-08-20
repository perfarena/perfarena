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
          A multi-competition ranking of coding models by how their code performs
        </h1>
        <p style={{ maxWidth: 760, fontSize: "1.1rem", lineHeight: 1.5 }}>
          PerfArena runs open, replicable competitions that rank language
          models by how well the code they generate performs. Each
          competition brings its own problem set and its own oracles: a
          correctness oracle decides whether a solution is right, a
          performance oracle measures how it runs. The seed{" "}
          {primary?.n_problems ?? 99}-problem LeetCode set sits alongside any
          new set the team adds, and each set keeps its own leaderboard. We
          run every registered model ourselves, from prompt to solution to
          analysis, so every result is verified and reproducible, with its
          full provenance anchored in an append-only hash chain.
        </p>
        <div className="row" style={{ marginTop: 16, gap: 12, flexWrap: "wrap" }}>
          <Link to="/competitions"><button>Browse competitions</button></Link>
          <Link to="/rankings"><button className="secondary">Open the leaderboard</button></Link>
          <Link to="/models"><button className="secondary">Models</button></Link>
          <Link to="/blog"><button className="secondary">Blog</button></Link>
        </div>
      </section>

      <section className="col" style={{ gap: 12 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Competitions</h2>
          <Link to="/competitions" className="muted">all competitions →</Link>
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
          <div className="empty">No judged submissions in this competition yet.</div>
        ) : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>#</th><th>Model</th><th>Submitter</th><th>Lang</th>
                <th title="Registered prompt template used. Different prompts = different experimental cells.">Prompt</th>
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
          <Step n={1} title="Pick a competition">
            Each <Link to="/competitions">competition</Link> sets its own
            problem set and its own oracles. The seed LC-99 set runs on
            LeetCode; new competitions can bring other suites and other
            judges. A problem shared across competitions pools its
            submissions.
          </Step>
          <Step n={2} title="Register a model">
            Builders give us an endpoint to reach their model, plus its
            specs. The model joins the <Link to="/models">model list</Link>,
            and every run uses a published <Link to="/prompts">prompt</Link>{" "}
            as written, so the setup stays reproducible.
          </Step>
          <Step n={3} title="We run and judge it">
            We drive the whole pipeline. Each solution goes to the
            competition's oracles: the correctness oracle returns pass or
            fail, the performance oracle reports runtime, memory, and energy.
            For LC-99, LeetCode runs each attempt five times to smooth timing
            noise. Accepted solutions count toward pass@1 and pass@k.
          </Step>
          <Step n={4} title="Auditable rankings">
            Each leaderboard updates as soon as a batch lands. Every attempt
            keeps its full trace (prompt, response, reasoning, signature,
            model digest) in an append-only hash chain, so no result can be
            changed after the fact.
          </Step>
        </div>
      </section>

      <section>
        <h2>Ground rules</h2>
        <ul className="muted" style={{ lineHeight: 1.7 }}>
          <li>Every submission belongs to one competition, and each leaderboard is computed from that competition's own problem set.</li>
          <li>Each model runs on one of the published <Link to="/prompts">prompt templates</Link>, and the exact prompt is part of every submission's record.</li>
          <li>Model identity is captured with a provider-agnostic digest (Ollama sha256, HF revision SHA, OpenAI system fingerprint, and so on).</li>
          <li>The oracle's verdict is final. Solutions that fail to compile or time out count as misses.</li>
          <li>Rankings are open to browse, and the full solutions dataset is downloadable for verification.</li>
        </ul>
      </section>

      <section>
        <h2>Want your model ranked?</h2>
        <p>
          Every result here is open to the community. We keep the rankings
          populated and current ourselves, and we welcome help. Research
          labs, independent builders, and researchers can have a model added
          on request: give us an endpoint, and we onboard it and monitor
          every run from generation through analysis. We can also set up a
          new competition around a problem suite you care about. See the{" "}
          <Link to="/about">team page</Link> to get in touch.
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
