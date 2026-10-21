import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, EfficiencyEntrant, EfficiencyMetricName, EfficiencyRankings,
} from "../lib/api";
import { formatDollar, formatPassAt1, formatWinProb } from "../lib/format";
import Th from "./Th";

/* Efficiency leaderboard with the method of the paper (GET
 * /api/efficiency-rankings). Every (language, problem) cell that two entrants
 * both solved is one duel per metric, and a Bradley-Terry model turns the
 * duels into one win probability per entrant. An entrant is one model under
 * one workflow, so single-shot and agentic runs share the table and the
 * workflow filters separate them. */

export const METRIC_LABEL: Record<EfficiencyMetricName, string> = {
  latency: "Latency", energy: "Energy", memory: "Memory",
};
const METRICS: EfficiencyMetricName[] = ["latency", "energy", "memory"];

/** Model name, plus the workflow unless it is a plain single-round prompt. */
export function entrantLabel(e: { model_name: string; workflow: string; key: string }): string {
  return e.key.endsWith("|direct|r1") ? e.model_name : `${e.model_name} (${e.workflow})`;
}

/** Badge of the golden baseline: a reference row that is listed, never ranked. */
export function ReferenceBadge({ version }: { version: string }) {
  return (
    <span className="badge badge-status" style={{ marginLeft: 6 }}
          title={`Golden baseline, ${version}: for each problem, language, and metric, the best correct solution of the campaign LLMs. Copied from stored results, not generated or measured again. Listed, never ranked. Compare an LLM against it on the Compare page.`}>
      reference
    </span>
  );
}

type SortKey = EfficiencyMetricName | "pass_at_1" | "cost" | "pareto" | "model_name";

function sortValue(e: EfficiencyEntrant, k: SortKey): number | string | null {
  if (k === "model_name") return e.model_name;
  if (k === "pass_at_1") return e.pass_at_1;
  if (k === "cost") return e.cost_per_attempt_usd;
  if (k === "pareto") return e.pareto_level;
  return e.metrics[k].rated ? e.metrics[k].win_prob : null;
}

export default function EfficiencyBoard({
  competition, harness, language, agent, agentKind, rounds, promptId, query,
}: {
  competition: string; harness: string; language: string;
  agent: string; agentKind: string; rounds: string; promptId: string; query: string;
}) {
  const [data, setData] = useState<EfficiencyRankings | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("latency");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    if (!competition) return;
    let live = true;
    setLoading(true); setErr("");
    api.efficiencyRankings({
      competition, harness: harness || undefined, language: language || undefined,
      agent: agent || undefined, agent_kind: agentKind || undefined,
      rounds: rounds || undefined, prompt_id: promptId ? Number(promptId) : undefined,
    })
      .then(d => { if (live) setData(d); })
      .catch((e: unknown) => { if (live) { setErr(String(e)); setData(null); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [competition, harness, language, agent, agentKind, rounds, promptId]);

  function onSort(key: string) {
    const k = key as SortKey;
    if (k === sortKey) setSortDir(d => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      // Win probability and pass@1 read best-first high to low; cost, the
      // Pareto level, and names read best-first low to high.
      setSortDir(k === "cost" || k === "pareto" || k === "model_name" ? "asc" : "desc");
    }
  }

  const rows = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLowerCase();
    const shown = data.entrants.filter(e => !needle ||
      `${e.model_name} ${e.model_version} ${e.workflow}`.toLowerCase().includes(needle));
    return [...shown].sort((a, b) => {
      if (a.reference !== b.reference) return a.reference ? -1 : 1;   // the golden baseline first
      if (a.partial !== b.partial) return a.partial ? 1 : -1;   // partial runs last
      const av = sortValue(a, sortKey), bv = sortValue(b, sortKey);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;                                  // unrated values last
      if (bv == null) return -1;
      const cmp = typeof av === "string" ? av.localeCompare(String(bv)) : av - Number(bv);
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [data, query, sortKey, sortDir]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!data) {
    return <div className="empty">{loading ? "Computing the ranking…" : "No data."}</div>;
  }
  if (data.entrants.length === 0) {
    return <div className="empty">No judged submissions match these filters.</div>;
  }

  // A metric gets a column only when some entrant holds duels in it
  // (LeetCode measures no energy).
  const metrics = METRICS.filter(m => data.entrants.some(e => e.metrics[m].rated));
  const nRanked = data.entrants.filter(e => !e.partial && !e.reference).length;
  const unratedTip = language
    ? `fewer than ${data.min_cells} problems solved in ${language} that another entrant also solved`
    : "no problem solved that another entrant also solved";

  return (
    <div className="col" style={{ gap: 12, opacity: loading ? 0.6 : 1 }}>
      <div className="muted" style={{ fontSize: "0.85rem" }}>
        {nRanked} ranked entrant{nRanked === 1 ? "" : "s"} · perf oracle <code>{data.harness ?? "–"}</code>
        {" "}· {data.n_problems} problems × {language ? `1 language (${language})` : `${data.languages.length} languages`}
        {" "}· computed {new Date(data.computed_at).toLocaleString()}
        {metrics.map(m => (
          <span key={m}> · {METRIC_LABEL[m].toLowerCase()}: {data.metrics[m].sig_pairs} of {data.metrics[m].n_pairs} pairs differ</span>
        ))}
        {" "}· <Link to="/snapshots">dated snapshots</Link>
      </div>

      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <Th label="#" tip="Row position under the current sort." />
            <Th label="Model" tip="Model name and version. Click the name to open the model's page." sortKey="model_name" activeKey={sortKey} dir={sortDir} onSort={onSort} />
            <Th label="Workflow" tip="How the model produced its solutions: a plain prompt (direct) or an agent, and how many rounds of refinement. Each model × workflow pair ranks as its own entrant." />
            {metrics.map(m => (
              <Th key={m} label={METRIC_LABEL[m]}
                  tip={`Win probability in ${METRIC_LABEL[m].toLowerCase()}: the mean chance that this entrant beats another entrant on a problem that both solved in the same language (Bradley-Terry model). Two values within ${data.metrics[m].tie_pct}% tie and count as half a win. Bold marks the tied leaders: no other entrant is significantly better (bootstrap, Holm correction, α = 0.05). The small number is the rank.`}
                  sortKey={m} activeKey={sortKey} dir={sortDir} onSort={onSort} />
            ))}
            <Th label="pass@1" tip="Share of the competition's cells (problem × language) where the first sample was correct. A failed problem gives no duel, so it shows here and not in the win probabilities." sortKey="pass_at_1" activeKey={sortKey} dir={sortDir} onSort={onSort} />
            <Th label="Solved" tip="Cells solved / cells the competition holds for this entrant's languages." />
            <Th label="$/attempt" tip="LLM cost per attempted solution: the cost of every attempt (all rounds) divided by the number of attempted cells. Recomputed from current model pricing when it is set." sortKey="cost" activeKey={sortKey} dir={sortDir} onSort={onSort} />
            <Th label="Pareto" tip={`Pareto level over ${data.pareto_metrics.map(m => METRIC_LABEL[m].toLowerCase()).join(", ")}. Level 1 = no other entrant dominates it. One entrant dominates another when it is significantly better in at least one metric and significantly worse in none.`} sortKey="pareto" activeKey={sortKey} dir={sortDir} onSort={onSort} />
            <Th label="Langs" tip="Languages this entrant has a judged submission in. Hover for the list." />
          </tr></thead>
          <tbody>
            {rows.map((e, i) => (
              <tr key={e.key} style={e.partial ? { opacity: 0.55 }
                : e.reference ? { background: "var(--accent-soft)" } : undefined}>
                <td>{e.reference ? "" : i + 1 - rows.filter(r => r.reference).length}</td>
                <td>
                  <Link to={`/models/${encodeURIComponent(e.model_name)}`}><b>{e.model_name}</b></Link>
                  <span className="muted"> {e.model_version}</span>
                  {e.reference && <ReferenceBadge version={e.model_version} />}
                  {e.partial && (
                    <span className="badge badge-medium" style={{ marginLeft: 6 }}
                          title="Attempted fewer than a third of its cells: listed but not ranked.">
                      partial
                    </span>
                  )}
                </td>
                <td>
                  {e.agent_slug === "direct"
                    ? <span className="muted">direct</span>
                    : e.reference ? <span className="muted">reference</span>
                    : <span title={e.agent_slug}>{e.agent_name}</span>}
                  {e.agent_kind === "agentic" && (
                    <span className="badge badge-status" style={{ marginLeft: 6 }}>agentic</span>
                  )}
                  {e.n_rounds > 1 && <span className="muted"> · {e.n_rounds} rounds</span>}
                </td>
                {metrics.map(m => {
                  const c = e.metrics[m];
                  if (!c.rated) {
                    return (
                      <td key={m} className="metric muted"
                          title={e.reference ? "reference: not ranked"
                            : e.partial ? "partial run: not ranked" : unratedTip}>–</td>
                    );
                  }
                  return (
                    <td key={m} className="metric"
                        title={c.tied_leader ? "tied leader: no entrant is significantly better" : undefined}>
                      {c.tied_leader ? <b>{formatWinProb(c.win_prob)}</b> : formatWinProb(c.win_prob)}
                      <span className="muted" style={{ fontSize: "0.75em", marginLeft: 4 }}>#{c.rank}</span>
                    </td>
                  );
                })}
                <td className="metric">{formatPassAt1(e.pass_at_1)}</td>
                <td className="metric">{e.n_solved}/{e.n_cells}</td>
                <td className="metric">{formatDollar(e.cost_per_attempt_usd)}</td>
                <td className="metric">{e.pareto_level ?? "–"}</td>
                <td className="metric" title={e.languages.join(", ")}>{e.languages.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ParetoLevels data={data} />
    </div>
  );
}

/* Pareto levels as rows of chips. Level 1 holds the entrants that no other
 * entrant dominates; each next level holds the ones that only the levels
 * above dominate. A chip's tooltip names the entrants it dominates. */
function ParetoLevels({ data }: { data: EfficiencyRankings }) {
  const ranked = data.entrants.filter(e => e.pareto_level != null);
  if (ranked.length < 2 || data.pareto_metrics.length === 0) return null;
  const byKey = new Map(data.entrants.map(e => [e.key, e]));
  const levels = new Map<number, EfficiencyEntrant[]>();
  for (const e of ranked) {
    const l = e.pareto_level!;
    if (!levels.has(l)) levels.set(l, []);
    levels.get(l)!.push(e);
  }
  const dominated = (k: string) => data.pareto_edges.filter(([a]) => a === k)
    .map(([, b]) => byKey.get(b)).filter((e): e is EfficiencyEntrant => !!e).map(entrantLabel);

  return (
    <section className="card">
      <h3 style={{ marginTop: 0, marginBottom: 4 }}>Pareto levels</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: "0.85rem" }}>
        Over {data.pareto_metrics.map(m => METRIC_LABEL[m].toLowerCase()).join(", ")}.
        An entrant dominates another when it is significantly better in at least
        one metric and significantly worse in none. Level 1 holds the entrants
        that no other entrant dominates. Hover an entrant to see whom it dominates.
      </p>
      <div className="col" style={{ gap: 8 }}>
        {[...levels.keys()].sort((a, b) => a - b).map(l => (
          <div key={l} className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span className="muted" style={{ width: 64, fontSize: "0.8rem" }}>Level {l}</span>
            {levels.get(l)!.map(e => {
              const d = dominated(e.key);
              return (
                <span key={e.key}
                      title={d.length ? `dominates: ${d.join(", ")}` : "dominates no entrant directly"}
                      style={{
                        padding: "2px 8px", borderRadius: 999, fontSize: "0.8rem",
                        background: l === 1 ? "var(--accent-soft)" : "var(--panel-2)",
                        border: "1px solid var(--border)", whiteSpace: "nowrap",
                      }}>
                  {entrantLabel(e)}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}
