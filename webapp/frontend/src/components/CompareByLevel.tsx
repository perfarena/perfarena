import { CompareByLevelRow } from "../lib/api";
import { MODEL_COLOURS } from "./CompareHistogram";

/* Difficulty breakdown for the model comparison.
 *
 * One grouped bar chart per metric (correctness + perf). Within each
 * chart, the x-axis is the 3 difficulty levels (Easy / Medium / Hard);
 * within each level we draw one bar per model, side-by-side, colour-
 * matched to the per-problem matrix on the same page.
 *
 * Pure inline SVG (matches RunsChart / CompareHistogram pattern; no
 * chart library). The chart shape is intentionally chunky and readable
 * over fancy — every bar's value is labelled directly above it so the
 * eye doesn't have to chase a y-axis tick line.
 *
 * Reading the bars:
 *   * pass-rate, runtime %ile, memory %ile  →  HIGHER is better
 *   * avg runtime (ms), avg memory (MB)     →  LOWER is better
 * The header banner spells this out so a viewer doesn't have to
 * remember the convention. */

const LEVELS: ("Easy" | "Medium" | "Hard")[] = ["Easy", "Medium", "Hard"];

interface MetricSpec {
  key: keyof CompareByLevelRow;
  label: string;
  unit: string;
  /** Format the raw value into a label string that sits over its bar. */
  fmt: (v: number) => string;
  /** When higher value = better. Drives the "winner" annotation in the
   *  header but doesn't change how the bars are drawn (always tallest =
   *  biggest raw value; the viewer reads the direction from the label). */
  higherIsBetter: boolean;
  /** Optional value transform — e.g. multiply pass_rate by 100 so the
   *  axis lines up with the more familiar 0–100 scale. */
  xform?: (v: number) => number;
}

const METRICS: MetricSpec[] = [
  {
    key: "pass_rate", label: "Pass rate", unit: "%",
    fmt: v => `${v.toFixed(1)}%`,
    higherIsBetter: true,
    xform: v => v * 100,
  },
  {
    key: "avg_runtime_ms", label: "Avg runtime", unit: "ms",
    fmt: v => v >= 1000 ? `${(v/1000).toFixed(2)}s` : `${v.toFixed(0)} ms`,
    higherIsBetter: false,
  },
  {
    key: "avg_memory_mb", label: "Avg memory", unit: "MB",
    fmt: v => `${v.toFixed(1)} MB`,
    higherIsBetter: false,
  },
  {
    key: "avg_runtime_percentile", label: "Runtime %ile", unit: "%",
    fmt: v => `${v.toFixed(1)}%`,
    higherIsBetter: true,
  },
  {
    key: "avg_memory_percentile", label: "Memory %ile", unit: "%",
    fmt: v => `${v.toFixed(1)}%`,
    higherIsBetter: true,
  },
  // Generator-side metrics — what it cost the LLM to produce solutions.
  // Charted on the same row as the judge-side metrics so the operator
  // can ask: "is this model's faster verdict worth its higher $ cost?"
  {
    key: "avg_cost_usd", label: "Avg $ / problem", unit: "$",
    fmt: v => v >= 1     ? `$${v.toFixed(2)}`
           : v >= 0.01  ? `$${v.toFixed(3)}`
                        : `$${v.toFixed(4)}`,
    higherIsBetter: false,
  },
  {
    key: "avg_response_tokens", label: "Avg tokens / problem", unit: "tok",
    fmt: v => v >= 1000 ? `${(v/1000).toFixed(1)}k` : v.toFixed(0),
    higherIsBetter: false,
  },
  {
    key: "avg_wall_clock_ms", label: "Avg generation time", unit: "s",
    fmt: v => v >= 1000 ? `${(v/1000).toFixed(1)}s` : `${v.toFixed(0)} ms`,
    higherIsBetter: false,
  },
];


export default function CompareByLevel({
  models, rows,
}: { models: string[]; rows: CompareByLevelRow[] }) {
  // Index rows by (model, level) → row for O(1) lookup while drawing.
  // Empty cells (a model that didn't touch a level) render as a thin
  // baseline tick at zero so the missing-bar reading isn't ambiguous.
  const byCell = new Map<string, CompareByLevelRow>();
  for (const r of rows) byCell.set(`${r.model_name}|${r.level}`, r);

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="row" style={{ alignItems: "baseline", flexWrap: "wrap", gap: 12 }}>
        <h2 style={{ margin: 0 }}>Performance by difficulty</h2>
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          one grouped chart per metric · bar colour = model · higher bar =
          larger raw value (the metric label says which direction is better)
        </span>
      </div>

      <div className="row" style={{
        gap: 8, flexWrap: "wrap", marginTop: 10, marginBottom: 14,
      }}>
        {models.map((m, i) => (
          <span key={m} style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: "0.85rem",
          }}>
            <span style={{
              display: "inline-block", width: 12, height: 12, borderRadius: 2,
              background: MODEL_COLOURS[i % MODEL_COLOURS.length],
            }} />
            {m}
          </span>
        ))}
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
        gap: 16,
      }}>
        {METRICS.map(m => (
          <MetricChart key={m.key as string}
                       spec={m} models={models} byCell={byCell} />
        ))}
      </div>
    </div>
  );
}


function MetricChart({
  spec, models, byCell,
}: {
  spec: MetricSpec;
  models: string[];
  byCell: Map<string, CompareByLevelRow>;
}) {
  // Resolve each (level, model) into a numeric value (null if no data).
  // We also compute the max across the whole chart so every bar shares a
  // consistent y-axis and the visual comparison is honest.
  const cells = LEVELS.map(level => models.map(model => {
    const row = byCell.get(`${model}|${level}`);
    if (!row) return null;
    const raw = row[spec.key] as number | null;
    if (raw == null) return null;
    return spec.xform ? spec.xform(raw) : raw;
  }));
  const allValues = cells.flat().filter((v): v is number => v != null);
  const maxV = allValues.length ? Math.max(...allValues, 0) : 1;

  // Layout constants. Chart size is intentionally small so 5 charts fit
  // comfortably across a wide screen and still single-column on a phone.
  const W = 320;
  const H = 200;
  const PAD_L = 26;
  const PAD_R = 8;
  const PAD_T = 30;
  const PAD_B = 28;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;

  // Within each level "group" we draw `models.length` bars side by side.
  // Group width takes 78% of the per-level slot; gap inside the group is
  // tight so 4+ models still read cleanly.
  const groupSlot = plotW / LEVELS.length;
  const groupW = groupSlot * 0.78;
  const groupGap = groupSlot - groupW;
  const barW = Math.max(6, Math.floor(groupW / models.length) - 2);

  const yScale = (v: number) => plotH * (1 - (v / (maxV || 1)));

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <div style={{ fontWeight: 600 }}>{spec.label}</div>
        <span className="muted" style={{ fontSize: "0.75rem" }}>
          ({spec.higherIsBetter ? "higher = better" : "lower = better"})
        </span>
      </div>
      <svg width={W} height={H}
           style={{ display: "block", overflow: "visible" }}>
        {/* Baseline */}
        <line x1={PAD_L} y1={PAD_T + plotH} x2={W - PAD_R} y2={PAD_T + plotH}
              stroke="var(--border)" />
        {/* Light y-axis ticks at 25 / 50 / 75 / 100% of the chart max so
            the eye has reference lines without a heavy axis. */}
        {[0.25, 0.5, 0.75, 1].map(frac => (
          <line key={frac}
                x1={PAD_L} y1={PAD_T + plotH * (1 - frac)}
                x2={W - PAD_R} y2={PAD_T + plotH * (1 - frac)}
                stroke="var(--panel-2)" strokeDasharray="2 4" />
        ))}
        {/* Bars per level / model */}
        {LEVELS.map((level, gi) => (
          <g key={level} transform={`translate(${PAD_L + gi * groupSlot + groupGap / 2}, ${PAD_T})`}>
            {models.map((m, mi) => {
              const v = cells[gi][mi];
              const colour = MODEL_COLOURS[mi % MODEL_COLOURS.length];
              const x = mi * (barW + 2);
              if (v == null) {
                // No data — render a thin baseline marker so the missing
                // cell isn't ambiguous with "zero".
                return (
                  <g key={m}>
                    <rect x={x} y={plotH - 1} width={barW} height={2}
                          fill="var(--fg-dim)" opacity={0.35}>
                      <title>{`${m} · ${level} — no data`}</title>
                    </rect>
                  </g>
                );
              }
              const y = yScale(v);
              const h = plotH - y;
              return (
                <g key={m}>
                  <rect x={x} y={y} width={barW} height={Math.max(2, h)}
                        fill={colour} rx={2}>
                    <title>{`${m} · ${level} — ${spec.fmt(v)}`}</title>
                  </rect>
                  {/* Value label above the bar. Skipped when bars are very
                      narrow (4+ models) to avoid overlap. */}
                  {barW >= 14 && (
                    <text x={x + barW / 2} y={Math.max(8, y - 3)}
                          textAnchor="middle"
                          fontSize="0.62rem"
                          fill="var(--fg-dim)"
                          style={{ fontVariantNumeric: "tabular-nums" }}>
                      {spec.fmt(v)}
                    </text>
                  )}
                </g>
              );
            })}
            {/* X-axis level label */}
            <text x={groupW / 2} y={plotH + 16}
                  textAnchor="middle"
                  fontSize="0.75rem"
                  fill="var(--fg)">{level}</text>
          </g>
        ))}
        {/* Y-axis unit annotation in the top-left corner. */}
        <text x={4} y={PAD_T - 8}
              fontSize="0.65rem" fill="var(--fg-dim)"
              style={{ fontVariantNumeric: "tabular-nums" }}>
          {spec.unit}
        </text>
      </svg>
    </div>
  );
}
