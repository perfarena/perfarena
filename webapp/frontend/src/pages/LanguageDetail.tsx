import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, LanguageDetailPayload } from "../lib/api";
import {
  formatDuration, formatEnergy, formatMemory, formatPassAt1, formatPercentile, formatRuntime,
} from "../lib/format";
import { SHOW_RELATIVE_METRICS } from "../lib/flags";
import PromptName from "../components/PromptName";
import Th from "../components/Th";

/** Columns whose values are strings (sorted lexically, A→Z by default). */
const STRING_COLS = new Set(["model_name", "submitter"]);
/** Numeric columns where lower is better, so a fresh click sorts ascending
 *  (best first). Everything else numeric defaults descending. */
const LOWER_IS_BETTER = new Set([
  "avg_runtime_ms", "avg_memory_mb", "total_cost_usd",
  "total_generation_ms", "total_generation_energy_j",
]);

export default function LanguageDetail() {
  const { name = "" } = useParams();
  const [d, setD] = useState<LanguageDetailPayload | null>(null);
  const [err, setErr] = useState("");
  const [sortKey, setSortKey] = useState<string>("pass_at_1");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    api.languageDetail(name).then(setD).catch((e: unknown) => setErr(String(e)));
  }, [name]);

  function onSort(key: string) {
    if (key === sortKey) {
      setSortDir(dir => (dir === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Strings and lower-is-better metrics default ascending (best first);
      // pass-rates and percentiles default descending.
      setSortDir(STRING_COLS.has(key) || LOWER_IS_BETTER.has(key) ? "asc" : "desc");
    }
  }

  // Sort a fresh copy so the API's eligibility-filtered order is preserved.
  // Null-handling: rows with a null value for the active key always sink to
  // the bottom regardless of direction, so a sort on a mostly-null column
  // still surfaces the rows that DO have data at the top.
  const sortedTop = useMemo(() => {
    if (!d) return [];
    const sign = sortDir === "desc" ? -1 : 1;
    return [...d.top_models].sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sortKey];
      const bv = (b as unknown as Record<string, unknown>)[sortKey];
      const aNull = av == null;
      const bNull = bv == null;
      if (aNull && bNull) return 0;
      if (aNull) return 1;        // a sinks below b
      if (bNull) return -1;
      const cmp = STRING_COLS.has(sortKey)
        ? String(av).localeCompare(String(bv))
        : (av as number) - (bv as number);
      return sign * cmp;
    });
  }, [d, sortKey, sortDir]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;

  const { info, stats } = d;
  const top_models = sortedTop;

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>{info.display_name}</h1>
        <p>{info.category}</p>
      </div>

      <section className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="submissions" value={stats.n_submissions.toString()} />
        <Stat label="fully accepted" value={stats.n_fully_accepted.toString()} />
        <Stat label="distinct models" value={stats.n_distinct_models.toString()} />
        <Stat label="distinct submitters" value={stats.n_distinct_submitters.toString()} />
      </section>

      <section className="row" style={{ gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div className="card col" style={{ flex: "1 1 360px" }}>
          <h2 style={{ marginTop: 0 }}>Language properties</h2>
          <KV label="Paradigms" value={info.paradigms.join(", ") || "–"} />
          <KV label="Typing" value={info.typing} />
          <KV label="Memory model" value={info.gc} />
          <KV label="Runtime" value={info.runtime} />
        </div>

        <div className="card col" style={{ flex: "1 1 280px" }}>
          <h2 style={{ marginTop: 0 }}>Surveys</h2>
          <KV label="Stack Overflow 2024" value={info.so_used_pct_2024 == null ? "–" : `${info.so_used_pct_2024.toFixed(1)}% used`} />
          <KV label="SO admired (2024)" value={info.so_admired_pct_2024 == null ? "–" : `${info.so_admired_pct_2024.toFixed(0)}%`} />
          <KV label="TIOBE 2025 rank" value={info.tiobe_rank_2025 == null ? "–" : `#${info.tiobe_rank_2025}`} />
          <div className="muted" style={{ fontSize: "0.78rem", marginTop: 8 }}>
            Approximate figures from public developer surveys (Stack Overflow 2024, TIOBE 2025).
          </div>
        </div>

        <div className="card col" style={{ flex: "1 1 280px" }}>
          <h2 style={{ marginTop: 0 }}>Performance footprint</h2>
          <KV label="Cold start" value={info.cold_start} />
          <KV label="Peak speed" value={info.peak_speed} />
          <KV label="Memory baseline" value={info.memory_baseline} />
        </div>
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Design notes</h2>
        <p style={{ marginTop: 0, lineHeight: 1.65 }}>{info.design_notes}</p>
        {info.key_traits.length > 0 && (
          <ul style={{ lineHeight: 1.6 }}>
            {info.key_traits.map(t => <li key={t}>{t}</li>)}
          </ul>
        )}
      </section>

      <section>
        <h2>Best of this language on the leaderboard</h2>
        <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
          <Stat label="best pass@1" value={formatPassAt1(stats.best.pass_at_1)} />
          <Stat label="lowest avg runtime" value={formatRuntime(stats.best.avg_runtime_ms)} />
          <Stat label="lowest best runtime" value={formatRuntime(stats.best.best_runtime_ms)} />
          <Stat label="lowest peak mem" value={formatMemory(stats.best.avg_memory_mb)} />
          <Stat label="lowest best peak mem" value={formatMemory(stats.best.best_memory_mb)} />
          {SHOW_RELATIVE_METRICS && <Stat label="best runtime %ile" value={formatPercentile(stats.best.avg_runtime_percentile)} />}
          {SHOW_RELATIVE_METRICS && <Stat label="best memory %ile" value={formatPercentile(stats.best.avg_memory_percentile)} />}
        </div>
      </section>

      <section className="col">
        <h2 style={{ margin: 0 }}>Top models in {info.display_name}</h2>
        {top_models.length === 0 ? (
          <div className="empty">No judged submissions in this language yet.</div>
        ) : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <Th label="#" tip="Row position under the current sort. Click any sortable header to re-sort; click again to flip the direction." />
                <Th label="Model" tip="Model name and version. Click the name to open the model's aggregate page." sortKey="model_name" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Submitter" tip="Submitter: organization or individual who submitted the batch. Click to open all of their submissions." sortKey="submitter" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Prompt" tip="Registered prompt template used. Click to compare prompts." />
                <Th label="pass@1" tip="Unbiased pass@1 estimator over k samples: the average of c/n per problem." sortKey="pass_at_1" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@k" tip="Mean acceptance rate across the submitted attempts: total accepted samples / total submitted samples." sortKey="pass_at_k" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg runtime" tip="Average runtime across accepted problems. Lower is better." sortKey="avg_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Peak mem" tip="Mean peak (max resident) memory across accepted problems. Lower is better." sortKey="avg_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                {SHOW_RELATIVE_METRICS && <Th label="Runtime %ile" tip="Mean per-problem LeetCode runtime percentile across accepted attempts. Higher is better." sortKey="avg_runtime_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="Memory %ile" tip="Mean per-problem LeetCode memory percentile across accepted attempts. Higher is better." sortKey="avg_memory_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                <Th label="Cost" tip="Total $ across all attempts in this submission. Recomputed live from the model's current $/1M-token rates when set on /models." sortKey="total_cost_usd" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Gen time" tip="Total generation wall-clock summed across attempts." sortKey="total_generation_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Gen energy" tip="Total generation energy (Joules). Only present when the submit client measured it (NVML / RAPL / provider telemetry)." sortKey="total_generation_energy_j" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="" />
              </tr></thead>
              <tbody>
                {top_models.map((m, i) => (
                  <tr key={m.submission_id}>
                    <td>{i + 1}</td>
                    <td>
                      <Link to={`/models/${encodeURIComponent(m.model_name)}`}>
                        <b>{m.model_name}</b>
                      </Link>
                      <span className="muted"> {m.model_version}</span>
                    </td>
                    <td><Link to={`/submitters/${m.submitter_slug}`}>{m.submitter}</Link></td>
                    <td><PromptName id={m.prompt_id} name={m.prompt_name} /></td>
                    <td className="metric">{formatPassAt1(m.pass_at_1)}</td>
                    <td className="metric">{formatPassAt1(m.pass_at_k)}</td>
                    <td className="metric">{formatRuntime(m.avg_runtime_ms)}</td>
                    <td className="metric">{formatMemory(m.avg_memory_mb)}</td>
                    {SHOW_RELATIVE_METRICS && <td className="metric">{formatPercentile(m.avg_runtime_percentile)}</td>}
                    {SHOW_RELATIVE_METRICS && <td className="metric">{formatPercentile(m.avg_memory_percentile)}</td>}
                    <td className="metric">{m.total_cost_usd == null ? "–" : `$${m.total_cost_usd.toFixed(4)}`}</td>
                    <td className="metric">{formatDuration(m.total_generation_ms)}</td>
                    <td className="metric">{formatEnergy(m.total_generation_energy_j)}</td>
                    <td><Link to={`/submissions/${m.submission_id}`}>details →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="row" style={{ gap: 12, padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
      <div className="muted" style={{ minWidth: 140 }}>{label}</div>
      <div style={{ flex: 1 }}>{value}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 160 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
