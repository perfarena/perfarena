import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, CostEstimatorStats, CostEstimatorSubmissionRow,
} from "../lib/api";

/* Cost-estimator page.
 *
 * What the user asked for: "estimate how much it'd cost to populate THIS
 * model across the platform given $-per-1M token rates I supply".
 *
 * What we serve up: instead of multiplying noisy per-attempt averages
 * (which fake a precision the data doesn't actually have), we report
 * the OBSERVED range of total input/output token counts across already-
 * completed full sweeps. The user multiplies that range by their own
 * rates to get a [min … max] cost envelope. Filters (prompt, language,
 * model, competition) narrow the comparable sweeps and tighten the
 * envelope without inventing data.
 *
 * Sample size matters — at n<3 full sweeps the range stops being a
 * trustworthy envelope. We surface n with each range so the user can
 * see how confident the estimate is.
 *
 * Pure inline SVG / table styling — no chart library, matches the rest
 * of the app. */

/* Tiny localStorage-backed memo for the user's preferred $-per-1M rates.
 * We don't ship a default — leaving the fields empty signals "I haven't
 * told you yet" so the cost panel can stay quiet rather than display a
 * meaningless $0 estimate. */
const RATE_STORAGE = "arena.cost-estimator.rates";

interface Rates { in_rate: string; out_rate: string; }

function loadRates(): Rates {
  try {
    const raw = localStorage.getItem(RATE_STORAGE);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore corrupt JSON */ }
  return { in_rate: "", out_rate: "" };
}
function saveRates(r: Rates) {
  try { localStorage.setItem(RATE_STORAGE, JSON.stringify(r)); } catch { /* ignore */ }
}


export default function CostEstimator() {
  const [stats, setStats] = useState<CostEstimatorStats | null>(null);
  const [err, setErr] = useState("");
  // Filter state. Each filter narrows the cohort of comparable sweeps.
  // Empty string = "no filter" (forwarded as undefined to the API).
  const [promptId, setPromptId] = useState<string>("");
  const [language, setLanguage] = useState<string>("");
  const [modelName, setModelName] = useState<string>("");
  const [competition, setCompetition] = useState<string>("");
  // User-supplied rates — persisted across reloads via localStorage so a
  // sweep planner doesn't have to retype them each session.
  const [rates, setRates] = useState<Rates>(() => loadRates());

  useEffect(() => { saveRates(rates); }, [rates]);

  useEffect(() => {
    const p: {
      prompt_id?: number; language?: string;
      model_name?: string; competition?: string;
    } = {};
    if (promptId)    p.prompt_id   = Number(promptId);
    if (language)    p.language    = language;
    if (modelName)   p.model_name  = modelName;
    if (competition) p.competition = competition;
    api.costEstimatorStats(p)
      .then(setStats)
      .catch((e: unknown) => setErr(String(e)));
  }, [promptId, language, modelName, competition]);

  // Derived numbers — the cost envelope is just the token range × $-rates,
  // recomputed whenever either the stats or the rates change. Returns
  // null entries when input is missing so the UI can show "—".
  const cost = useMemo(() => computeEnvelope(stats, rates), [stats, rates]);

  return (
    <div>
      <div className="hero">
        <h1>Cost estimator</h1>
        <p>
          Estimate the $ cost of populating a model across the platform.
          Numbers come from observed token totals on already-completed
          full sweeps — narrow the cohort with the filters below for a
          tighter envelope.
        </p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      {/* ----- Filter row -------------------------------------------- */}
      <div className="filter-bar">
        <select value={competition} onChange={e => setCompetition(e.target.value)}
                aria-label="Filter by competition">
          <option value="">All competitions</option>
          {stats?.available_competitions.map(c => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        <select value={modelName} onChange={e => setModelName(e.target.value)}
                aria-label="Filter by model">
          <option value="">All models</option>
          {stats?.available_models.map(m => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <select value={language} onChange={e => setLanguage(e.target.value)}
                aria-label="Filter by language">
          <option value="">All languages</option>
          {stats?.available_languages.map(l => (
            <option key={l} value={l}>{l}</option>
          ))}
        </select>
        <select value={promptId} onChange={e => setPromptId(e.target.value)}
                aria-label="Filter by prompt">
          <option value="">All prompts</option>
          {stats?.available_prompts.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </div>

      {/* ----- Rates input + estimate panel -------------------------- */}
      <div className="card" style={{ padding: 16, marginBottom: 12 }}>
        <div className="row" style={{ gap: 24, flexWrap: "wrap" }}>
          <div>
            <label htmlFor="in_rate" style={{ display: "block", fontSize: "0.85rem", color: "var(--fg-dim)" }}>
              Input ($ / 1M tokens)
            </label>
            <input id="in_rate" type="number" step="0.01" min="0"
                   placeholder="e.g. 0.40"
                   value={rates.in_rate}
                   onChange={e => setRates(r => ({ ...r, in_rate: e.target.value }))}
                   style={{ width: 140 }} />
          </div>
          <div>
            <label htmlFor="out_rate" style={{ display: "block", fontSize: "0.85rem", color: "var(--fg-dim)" }}>
              Output ($ / 1M tokens)
            </label>
            <input id="out_rate" type="number" step="0.01" min="0"
                   placeholder="e.g. 1.60"
                   value={rates.out_rate}
                   onChange={e => setRates(r => ({ ...r, out_rate: e.target.value }))}
                   style={{ width: 140 }} />
          </div>
          <div className="muted" style={{ fontSize: "0.85rem", alignSelf: "end" }}>
            Defaults to OpenAI gpt-4.1-mini list price (0.40 / 1.60). Stored locally.
          </div>
        </div>

        <EnvelopePanel stats={stats} cost={cost} />
      </div>

      {/* ----- Matching submissions table ---------------------------- */}
      <SubmissionsTable rows={stats?.submissions ?? []} />
    </div>
  );
}


/* ====== envelope computation ============================================ */

interface CostEnvelope {
  min_cost: number | null;
  max_cost: number | null;
  median_cost: number | null;
}

function computeEnvelope(
  stats: CostEstimatorStats | null, rates: Rates,
): CostEnvelope {
  if (!stats) return { min_cost: null, max_cost: null, median_cost: null };
  const inR = Number(rates.in_rate);
  const outR = Number(rates.out_rate);
  if (!isFinite(inR) || !isFinite(outR) || inR <= 0 || outR <= 0) {
    return { min_cost: null, max_cost: null, median_cost: null };
  }
  const i = stats.input_tokens;
  const o = stats.output_tokens;
  // The envelope's MIN cost is the cheapest observed sweep (min input
  // AND min output coming from potentially-different sweeps, so this is
  // a CONSERVATIVE lower bound — the real cheapest sweep is at least as
  // high). The MAX cost is similarly the worst case. Median is the
  // pair-wise median for a one-glance "typical" number.
  const dollars = (in_tok: number | null, out_tok: number | null) => {
    if (in_tok == null || out_tok == null) return null;
    return (in_tok * inR + out_tok * outR) / 1_000_000;
  };
  return {
    min_cost: dollars(i.min, o.min),
    max_cost: dollars(i.max, o.max),
    median_cost: dollars(i.median, o.median),
  };
}


/* ====== envelope panel ================================================== */

function EnvelopePanel({
  stats, cost,
}: { stats: CostEstimatorStats | null; cost: CostEnvelope }) {
  if (!stats) return <div className="muted" style={{ marginTop: 16 }}>Loading…</div>;
  const n = stats.input_tokens.n;
  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
      <div className="row" style={{ gap: 32, flexWrap: "wrap" }}>
        <RangeStat label="Input tokens / sweep"
                   range={stats.input_tokens} unit="" />
        <RangeStat label="Output tokens / sweep"
                   range={stats.output_tokens} unit="" />
        <RangeStat label="Estimated cost"
                   range={{
                     min: cost.min_cost,
                     max: cost.max_cost,
                     median: cost.median_cost,
                     n: stats.input_tokens.n,
                   }}
                   unit="$" formatter={fmtDollar} highlight />
      </div>
      <div className="muted" style={{ fontSize: "0.85rem", marginTop: 10, lineHeight: 1.5 }}>
        Computed from <b>{n}</b> already-completed full sweep{n === 1 ? "" : "s"} matching the filters.
        {n === 0 && " No full sweep with token data exists yet for this combination — try fewer filters or wait for a sweep to finish."}
        {n > 0 && n < 3 && " Small sample — treat the envelope as indicative until more sweeps land."}
      </div>
    </div>
  );
}


function RangeStat({
  label, range, unit, formatter, highlight,
}: {
  label: string;
  range: { min: number | null; max: number | null; median: number | null; n: number };
  unit: string;
  formatter?: (n: number) => string;
  highlight?: boolean;
}) {
  const fmt = formatter ?? ((n: number) => n.toLocaleString());
  const has = range.min != null && range.max != null;
  return (
    <div style={{
      borderLeft: highlight ? "3px solid var(--accent)" : "3px solid transparent",
      paddingLeft: highlight ? 10 : 0,
    }}>
      <div style={{ fontSize: "0.8rem", color: "var(--fg-dim)", textTransform: "uppercase", letterSpacing: 0.4 }}>
        {label}
      </div>
      <div style={{ fontSize: "1.4rem", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
        {has
          ? <>{fmt(range.min!)} {unit && !formatter ? unit : ""} <span className="muted" style={{ fontSize: "0.9rem", margin: "0 6px" }}>…</span> {fmt(range.max!)} {unit && !formatter ? unit : ""}</>
          : <span className="muted">—</span>}
      </div>
      {has && range.median != null && (
        <div className="muted" style={{ fontSize: "0.8rem" }}>
          median {fmt(range.median)} {unit && !formatter ? unit : ""}
        </div>
      )}
    </div>
  );
}


function fmtDollar(n: number): string {
  return n >= 100 ? `$${n.toFixed(0)}`
       : n >= 1   ? `$${n.toFixed(2)}`
                  : `$${n.toFixed(4)}`;
}


/* ====== matching-submissions table ====================================== */

function SubmissionsTable({ rows }: { rows: CostEstimatorSubmissionRow[] }) {
  if (!rows.length) {
    return (
      <div className="card" style={{ padding: 16 }}>
        <div className="muted">No submissions match the current filters.</div>
      </div>
    );
  }
  return (
    <div className="card tbl-scroll" style={{ padding: 0 }}>
      <table className="tbl">
        <thead><tr>
          <th>#</th>
          <th>Model</th>
          <th>Language</th>
          <th>Prompt</th>
          <th>Competition</th>
          <th title="Unique problems hit / problems in scope. ≥ scope = full sweep.">Coverage</th>
          <th>Input tokens</th>
          <th>Output tokens</th>
        </tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.submission_id} style={r.is_full_sweep ? undefined : { opacity: 0.6 }}>
              <td>
                <Link to={`/submissions/${r.submission_id}`}>#{r.submission_id}</Link>
              </td>
              <td>{r.model_name}<div className="muted" style={{ fontSize: "0.75rem" }}>{r.model_version}</div></td>
              <td>{r.language}</td>
              <td className="muted">{r.prompt_name ?? "—"}</td>
              <td>
                {r.competition_slug
                  ? <Link to={`/competitions/${r.competition_slug}`}>{r.competition_name}</Link>
                  : <span className="muted">—</span>}
              </td>
              <td style={{ fontVariantNumeric: "tabular-nums" }}>
                {r.n_problems_attempted} / {r.n_problems_in_scope}
                {r.is_full_sweep && <span title="full sweep" style={{ color: "var(--good)", marginLeft: 6 }}>✓</span>}
              </td>
              <td style={{ fontVariantNumeric: "tabular-nums" }}>
                {r.total_input_tokens != null
                  ? r.total_input_tokens.toLocaleString()
                  : <span className="muted">—</span>}
              </td>
              <td style={{ fontVariantNumeric: "tabular-nums" }}>
                {r.total_output_tokens != null
                  ? r.total_output_tokens.toLocaleString()
                  : <span className="muted">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
