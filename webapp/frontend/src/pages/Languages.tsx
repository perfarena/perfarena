import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, LanguageListItem, LanguageTopSubmission } from "../lib/api";
import { formatPassAt1, formatRuntime } from "../lib/format";

/* /languages — one full-width card per language, styled after the efficiency
 * dashboard: a ranked mini-leaderboard with pass@1-tinted bars, so "who is
 * winning here" reads at a glance. The whole card links to /languages/:name
 * for the full leaderboard.
 *
 * A page-level metric toggle re-ranks every card: runtime (default), memory,
 * energy, or pass@k. Bars are always "longer is better", so the metric the
 * reader is optimizing for reads the same way whether lower (runtime) or
 * higher (pass@k) is good.
 *
 * The table lives in an overflow-x:auto wrapper and the cards are full width,
 * so the model rows never spill past the card edge on narrow screens. */

type Metric = "runtime" | "memory" | "energy" | "passk";

const METRICS: {
  key: Metric; label: string; field: keyof LanguageTopSubmission;
  higher: boolean; color: string; hdr: string;
}[] = [
  { key: "runtime", label: "Runtime", field: "avg_runtime_ms",       higher: false, color: "var(--accent)",   hdr: "per-op runtime" },
  { key: "memory",  label: "Memory",  field: "avg_memory_mb",        higher: false, color: "var(--accent-2)", hdr: "peak memory" },
  { key: "energy",  label: "Energy",  field: "avg_runtime_energy_j", higher: false, color: "var(--warn)",     hdr: "energy / op" },
  { key: "passk",   label: "pass@k",  field: "pass_at_k",            higher: true,  color: "var(--accent)",   hdr: "pass@k" },
];

export default function Languages() {
  const [rows, setRows] = useState<LanguageListItem[]>([]);
  const [err, setErr] = useState("");
  const [hideEmpty, setHideEmpty] = useState(true);
  const [metric, setMetric] = useState<Metric>("runtime");

  useEffect(() => {
    api.languages()
      .then(setRows)
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  const visible = hideEmpty ? rows.filter(r => r.n_submissions > 0) : rows;

  if (err) {
    return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  }

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>Languages</h1>
        <p>
          Every language the platform tracks, with paradigm, TIOBE rank, and its
          models ranked by the metric you choose. Click any card for the full
          per-language leaderboard.
        </p>
      </div>

      <div className="row" style={{ justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div className="seg" role="group" aria-label="Rank by">
          {METRICS.map(m => (
            <button key={m.key}
                    className={metric === m.key ? "on" : ""}
                    aria-pressed={metric === m.key}
                    onClick={() => setMetric(m.key)}>{m.label}</button>
          ))}
        </div>
        <label className="row" style={{ gap: 6, fontSize: "0.9rem" }}>
          <input type="checkbox" checked={hideEmpty}
                 onChange={e => setHideEmpty(e.target.checked)} />
          hide languages with no submissions
        </label>
      </div>

      {rows.length === 0 ? (
        <div className="empty">Loading…</div>
      ) : visible.length === 0 ? (
        <div className="empty">
          No language has any submissions yet. Uncheck the filter to see the
          full catalog.
        </div>
      ) : (
        <div className="lang-grid">
          {visible.map(l => <LanguageCard key={l.name} l={l} metric={metric} />)}
        </div>
      )}

      <p className="muted" style={{ fontSize: "0.8rem", maxWidth: "82ch" }}>
        Each card ranks a language's models by the selected metric. Bars are
        longer-is-better; fill opacity encodes pass@1. ◆ marks the top model
        with pass@1&nbsp;≥&nbsp;0.90. Runtime and energy are per operation; peak
        memory includes the language runtime floor, so compare it only within a
        language. Energy is recorded only on the instrumented runner, so models
        run elsewhere show “—”.
      </p>
    </div>
  );
}

function passClass(p: number | null): string {
  const v = p ?? 0;
  return v >= 0.9 ? "pass-hi" : v >= 0.6 ? "pass-mid" : "pass-lo";
}

/** Bar width, always longer-is-better. Lower-is-better metrics are inverted on
 *  a log scale; higher-is-better metrics scale linearly within the group. */
function barWidth(v: number | null, lo: number, hi: number, higher: boolean): number {
  if (v == null) return 0;
  if (hi <= lo) return 100;
  if (higher) return 6 + 94 * (v - lo) / (hi - lo);
  if (v <= 0) return 0;
  return 6 + 94 * (Math.log(hi) - Math.log(v)) / (Math.log(hi) - Math.log(lo));
}

function fmtEnergy(j: number | null): string {
  if (j == null) return "—";
  return j >= 1e-3 ? `${(j * 1e3).toFixed(2)} mJ` : `${(j * 1e6).toFixed(1)} µJ`;
}
function fmtMem(mb: number | null): string {
  return mb == null ? "—" : `${mb.toFixed(1)} MB`;
}
function fmtMetric(metric: Metric, v: number | null): string {
  if (metric === "runtime") return formatRuntime(v);
  if (metric === "memory") return fmtMem(v);
  if (metric === "energy") return fmtEnergy(v);
  return formatPassAt1(v);
}

function LanguageCard({ l, metric }: { l: LanguageListItem; metric: Metric }) {
  const href = `/languages/${encodeURIComponent(l.name)}`;
  const rows = l.top_submissions ?? [];
  const m = METRICS.find(x => x.key === metric)!;
  const val = (s: LanguageTopSubmission) => s[m.field] as number | null;

  // Rows that have the selected metric rank first (sorted best→worst); rows
  // missing it sink to the bottom with a "—".
  const rankable = rows.filter(s => {
    const x = val(s);
    return x != null && (m.higher || x > 0);
  });
  const missing = rows.filter(s => !rankable.includes(s));
  const sorted = [...rankable].sort((a, b) => {
    const av = val(a) as number, bv = val(b) as number;
    return m.higher ? bv - av : av - bv;
  });
  const display = [...sorted, ...missing];

  const vals = rankable.map(val) as number[];
  const lo = vals.length ? Math.min(...vals) : 0;
  const hi = vals.length ? Math.max(...vals) : 0;
  const best = rows.find(s => (s.pass_at_1 ?? 0) >= 0.9) ?? null;

  return (
    <Link to={href} className="card lang-card col" style={{ textDecoration: "none", color: "inherit" }}>
      <div className="lang-head">
        <div className="col" style={{ gap: 3 }}>
          <h3>{l.display_name}</h3>
          <span className="lang-meta">
            {l.category}
            {best && <> · Best: <b>{best.model_name}</b> · pass@1 {formatPassAt1(best.pass_at_1)}</>}
          </span>
        </div>
        <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
          <Mini label="models" value={String(l.n_models)} />
          <Mini label="submissions" value={String(l.n_submissions)} />
          <Mini label="accepted" value={String(l.n_fully_accepted)} />
          {l.tiobe_rank_2025 != null && <Mini label="TIOBE '25" value={`#${l.tiobe_rank_2025}`} />}
        </div>
      </div>

      {(l.paradigms ?? []).length > 0 && (
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {(l.paradigms ?? []).map(p => (
            <span key={p} className="badge badge-status">{p}</span>
          ))}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="muted" style={{ fontSize: "0.85rem", fontStyle: "italic" }}>
          No judged submissions yet for this language.
        </div>
      ) : (
        <div className="rank-wrap">
          <table className="rank-tbl">
            <thead>
              <tr>
                <th className="rk">#</th>
                <th className="l">Model</th>
                <th>pass@1</th>
                <th>pass@k</th>
                <th className="l chart">{m.hdr} · longer is better</th>
                <th>rt</th>
                <th>mem</th>
                <th>energy</th>
              </tr>
            </thead>
            <tbody>
              {display.map((s, i) => {
                const v = val(s);
                const ranked = i < sorted.length;
                const w = barWidth(v, lo, hi, m.higher);
                const op = (0.22 + 0.78 * Math.min(1, s.pass_at_1 ?? 0)).toFixed(3);
                return (
                  <tr key={s.submission_id}>
                    <td className="rk">{ranked ? i + 1 : "–"}</td>
                    <td className="l mdl">
                      {s.model_name}
                      {s === best && <span className="win-mark" title="best with pass@1 ≥ 0.90"> ◆</span>}
                      {s.model_version && (
                        <span className="muted" style={{ fontSize: "0.8em" }}> {s.model_version}</span>
                      )}
                    </td>
                    <td className="num">
                      <span className={`pass-pill ${passClass(s.pass_at_1)}`}>{formatPassAt1(s.pass_at_1)}</span>
                    </td>
                    <td className="num muted">{formatPassAt1(s.pass_at_k)}</td>
                    <td className="l chart">
                      <div className="cbar">
                        <div className="track">
                          <div className="fill" style={{ width: `${w}%`, opacity: op, background: m.color }} />
                        </div>
                        <span className="val">{fmtMetric(metric, v)}</span>
                      </div>
                    </td>
                    <td className="num muted">{formatRuntime(s.avg_runtime_ms)}</td>
                    <td className="num muted">{s.avg_memory_mb == null ? "—" : s.avg_memory_mb.toFixed(1)}</td>
                    <td className="num muted">{fmtEnergy(s.avg_runtime_energy_j)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Link>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="col" style={{ gap: 2, alignItems: "flex-end" }}>
      <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  );
}
