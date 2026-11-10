import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, CompetitionPublic, EfficiencyRanks } from "../lib/api";
import BumpChart, { bumpColors } from "../components/BumpChart";

// Default competition the "Top of the leaderboard" preview is scoped to.
// Matches Rankings.tsx's default — the seed LC-99 contains every problem
// on a fresh install, so this is a no-op there.
const PRIMARY_COMPETITION_SLUG = "leetcode-99-perf";
const METRIC_TITLE: Record<string, string> = {
  latency: "Latency", energy: "Energy", memory: "Memory",
};

export default function Home() {
  const [comps, setComps] = useState<CompetitionPublic[]>([]);
  // The per-language efficiency bump charts are tabbed per competition. Each
  // tab's ranks are fetched lazily the first time it's opened and cached here,
  // so the homepage only pays for the active competition up front.
  const [activeComp, setActiveComp] = useState<string>(PRIMARY_COMPETITION_SLUG);
  const [effByComp, setEffByComp] = useState<Record<string, EfficiencyRanks>>({});
  const [loadingComp, setLoadingComp] = useState<string | null>(null);
  const [metric, setMetric] = useState<string>("latency");

  function loadEff(slug: string) {
    if (effByComp[slug]) return;                    // cached — lazy no-op
    setLoadingComp(slug);
    api.efficiencyRanks(slug)
      .then(d => setEffByComp(m => ({ ...m, [slug]: d })))
      .catch(() => setEffByComp(m => ({
        ...m, [slug]: { competition: slug, languages: [], metrics: [], ranks: {} },
      })))
      .finally(() => setLoadingComp(l => (l === slug ? null : l)));
  }

  useEffect(() => {
    loadEff(PRIMARY_COMPETITION_SLUG);              // eager-load only the default tab
    api.competitions().then(setComps).catch(() => setComps([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onTab(slug: string) { setActiveComp(slug); loadEff(slug); }

  const primary = comps.find(c => c.slug === PRIMARY_COMPETITION_SLUG) ?? null;
  // Tab order: primary first, then the rest by name. If the primary isn't in
  // the list yet (still loading), fall back to whatever competitions exist.
  const tabComps = [
    ...comps.filter(c => c.slug === PRIMARY_COMPETITION_SLUG),
    ...comps.filter(c => c.slug !== PRIMARY_COMPETITION_SLUG),
  ];
  const activeCompObj = comps.find(c => c.slug === activeComp) ?? primary;
  const eff = effByComp[activeComp];
  // Only render a metric's chart when it actually has data (LeetCode rows carry
  // no energy, so the energy chart is simply omitted there).
  const metricsWithData = eff
    ? eff.metrics.filter(mt => Object.keys(eff.ranks[mt] ?? {}).length > 0)
    : [];
  const colors = eff ? bumpColors(metricsWithData.map(mt => eff.ranks[mt] ?? {})) : undefined;
  // A metric without data in this competition falls back to the first one.
  const shownMetric = metricsWithData.includes(metric) ? metric : metricsWithData[0];
  // Other competitions (besides the primary) get their own card row below
  // the primary card so visitors discover the multi-competition story.
  const other = comps.filter(c => c.slug !== PRIMARY_COMPETITION_SLUG);

  return (
    <div className="col" style={{ gap: 28 }}>
      <section className="hero" style={{ paddingBottom: 0 }}>
        {/* Hero logo. Same /logo.png the header + favicon use; degrades to
            the wordmark below if the file isn't present. */}
        <img src={`${import.meta.env.BASE_URL}logo.png`} alt=""
             onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
             style={{ height: 96, width: 96, display: "block", marginBottom: 12 }} />
        <h1 style={{ fontSize: "2.4rem", lineHeight: 1.1 }}>
          A multi-competition ranking of coding models by how their code performs
        </h1>
        <p style={{ maxWidth: 760, fontSize: "1.1rem", lineHeight: 1.5 }}>
          PerfArena runs open, replicable competitions that rank language
          models by how well the code they generate performs. Each
          competition has its own problem set and its own oracles. A
          correctness oracle decides whether a solution is correct. A
          performance oracle measures how the solution runs. The team can add
          new problem sets next to the seed{" "}
          {primary?.n_problems ?? 99}-problem LeetCode set, and each set has
          its own leaderboard. We run every registered model ourselves, from
          the prompt to the solution to the analysis. Thus, every result is
          verified and reproducible. An append-only hash chain records the
          full provenance of each result.
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
          <Link to="/competitions" className="muted">All competitions →</Link>
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
        <h2 style={{ marginBottom: 8 }}>Efficiency by language</h2>
        {/* One tab per competition. The active tab's per-language ranks are
            fetched lazily on first open and cached, so the homepage only loads
            the default up front. */}
        {tabComps.length > 0 && (
          <div className="row" style={{ gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
            {tabComps.map(c => (
              <button key={c.slug}
                      className={c.slug === activeComp ? "" : "secondary"}
                      onClick={() => onTab(c.slug)}
                      style={{ fontSize: "0.85rem" }}>
                {c.name}
              </button>
            ))}
          </div>
        )}
        {activeCompObj && (
          <div className="muted" style={{ fontSize: "0.85rem", marginBottom: 8 }}>
            {activeCompObj.n_problems} problems · {activeCompObj.n_submissions} submissions · {activeCompObj.n_models} models
          </div>
        )}
        {loadingComp === activeComp && !eff ? (
          <div className="empty">Loading…</div>
        ) : metricsWithData.length === 0 ? (
          <div className="empty">No measured solutions in this competition yet.</div>
        ) : (
          <>
            <div className="muted" style={{ fontSize: "0.85rem", marginBottom: 8 }}>
              Rank by language (1 = best). Each rank comes from the model's win
              probability on the problems that both models in a pair solved.
              Hover over a point to see its value.
            </div>
            {/* Inner tabs: one chart per metric. The chosen metric stays when
                the competition changes, and a model keeps one color in all charts. */}
            <div className="tabs" role="tablist" aria-label="Metric" style={{ marginBottom: 12 }}>
              {metricsWithData.map(mt => (
                <button key={mt} role="tab" aria-selected={mt === shownMetric}
                        className={`tab${mt === shownMetric ? " active" : ""}`}
                        onClick={() => setMetric(mt)}>
                  {METRIC_TITLE[mt] ?? mt}
                </button>
              ))}
            </div>
            <BumpChart title={METRIC_TITLE[shownMetric] ?? shownMetric}
                       languages={eff!.languages}
                       ranked={eff!.ranks[shownMetric] ?? {}}
                       colors={colors} />
          </>
        )}
        <div className="row" style={{ marginTop: 12 }}>
          <Link to={`/rankings?competition=${encodeURIComponent(activeComp)}`}>See the full leaderboard →</Link>
        </div>
      </section>

      <section>
        <h2>Open, transparent, extensible</h2>
        <div className="row" style={{ gap: 16, alignItems: "stretch", flexWrap: "wrap" }}>
          <Point title="Open source and replicable">
            The whole competition is open source. You can take the code, run
            the same benchmark yourself, and get the same results. Nothing is
            hidden behind our servers.
          </Point>
          <Point title="Transparent end to end">
            We host the platform and run every step ourselves, from generating
            the code to executing and measuring it. We keep the full record of
            every result, so anyone can check how we produced it.
          </Point>
          <Point title="Built to extend">
            We can add new <Link to="/competitions">competitions</Link>,{" "}
            <Link to="/models">models</Link>,{" "}
            <Link to="/prompts">prompt templates</Link>, and AI agent workflows.
            The platform is not tied to one problem set or one way of generating
            code.
          </Point>
        </div>
      </section>

      <section>
        <h2>Ground rules</h2>
        <ul className="muted" style={{ lineHeight: 1.7 }}>
          <li>Every submission belongs to one competition. Each leaderboard uses only the problem set of its competition.</li>
          <li>Each model uses one of the published <Link to="/prompts">prompt templates</Link>. The record of every submission includes the exact prompt.</li>
          <li>A provider-agnostic digest records the identity of each model (Ollama sha256, HF revision SHA, OpenAI system fingerprint, and so on).</li>
          <li>The oracle's verdict is final. Solutions that fail to compile or time out count as misses.</li>
          <li>Anyone can browse the rankings and download the full solutions dataset to verify the results.</li>
        </ul>
      </section>

      <section>
        <h2>Want your model ranked?</h2>
        <p>
          Every result here is open to the community. We keep the rankings
          populated and current ourselves. We welcome help. Research labs,
          independent builders, and researchers can ask us to add a model.
          Give us an endpoint, and we add the model and monitor every run,
          from generation to analysis. We can also set up a new competition
          around a problem suite that you care about. To contact us, see the{" "}
          <Link to="/about">team page</Link>.
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

function Point({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ flex: "1 1 240px", minWidth: 240 }}>
      <h3 style={{ margin: "0 0 8px" }}>{title}</h3>
      <p style={{ margin: 0, color: "var(--fg-dim)", lineHeight: 1.5 }}>{children}</p>
    </div>
  );
}
