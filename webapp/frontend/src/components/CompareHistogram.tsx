import { Fragment } from "react";
import { CompareProblemRow } from "../lib/api";

/* Per-problem comparison bars for an arbitrary number of models (N ≥ 2),
 * embedded inside the per-problem matrix table. Each problem row gets
 * 2 or 3 metric rows; within each metric one overlapping bar per model
 * is drawn on a shared track:
 *
 *      Runtime (ms)    ▌▌▌▌▌▌▌▌▌▌    113.4 ms / 58.5 ms
 *      Memory  (MB)    ▌▌▌▌▌▌▌▌      15.66 MB / 21.16 MB
 *      Energy  (J)     —              —  ←  shown only when both report
 *
 * Lower ms / MB / J is better on every axis. The BAR WIDTH shows each
 * model's RANK within the problem (the winner takes the whole bar):
 * within a (problem, N-model) pair the model with the LOWEST raw value
 * gets the full track, and the others scale down as
 * `(min / its_value) × TRACK_W`. A model twice as slow lands at half
 * the bar length.
 *
 * Reading the rows is now consistent: BIGGER bar = better model, in
 * both the runtime row and the memory row. The raw numbers next to the
 * bar tell you what the actual ms/MB were, colour-keyed by model.
 *
 * Overlap drawing: bars are drawn LARGEST-FIRST with monotonically
 * increasing opacity, so smaller (= worse) bars stay visible in front
 * of bigger ones — you never lose a model's bar behind another's fill.
 *
 * Pure inline SVG (matches RunsChart's pattern; no charting dep). */

export type CompareCell = {
  runtime_ms: number | null;
  memory_mb: number | null;
  generation_energy_j: number | null;
  runtime_energy_j: number | null;
} | undefined;

interface MetricSpec {
  key: "runtime" | "memory" | "energy";
  label: string;
  unit: string;
  unitTip: string;
  raw: (c: NonNullable<CompareCell>) => number | null;
}

const METRICS: MetricSpec[] = [
  {
    key: "runtime", label: "Runtime", unit: "ms",
    unitTip: "The mean runtime of the best accepted attempt, in ms. A lower value is better. The bar width shows the rank of each model on this metric within the problem. The fastest model, with the lowest ms, fills the track. The other models scale down by the minimum ms divided by their own ms, times the track width. A bigger bar is better.",
    raw: c => c.runtime_ms,
  },
  {
    key: "memory", label: "Peak mem", unit: "MB",
    unitTip: "The mean peak memory (maximum resident) of the best accepted attempt, in MB. This is peak memory on both oracles. It is never per-op. A lower value is better. The scaling matches the runtime metric. The leanest model, with the lowest MB, fills the track. The other models scale by the ratio. A bigger bar is better.",
    raw: c => c.memory_mb,
  },
  {
    key: "energy", label: "Energy", unit: "J",
    unitTip: "The best-attempt energy in Joules. It prefers runtime energy (the execution side). Arena-local and similar harnesses report the runtime energy. This is the axis that changes most across models that solve the same problem. It falls back to generation energy (the LLM side) when the runtime figure is empty. A lower value is better. The scaling is the same. The most efficient model fills the track.",
    raw: c => c.runtime_energy_j ?? c.generation_energy_j,
  },
];

/* Distinct-enough categorical palette for up to ~8 models. Tracks the
 * platform's accent / status colours where possible so the chart reads
 * the same in light and dark modes. */
export const MODEL_COLOURS: readonly string[] = [
  "var(--accent)",      // primary blue
  "var(--accent-2)",    // green
  "#fbbf24",            // amber
  "#f87171",            // red
  "#22d3ee",            // cyan
  "#c084fc",            // purple
  "#fb923c",            // orange
  "#f472b6",            // pink
];

/** Does ANY problem in scope have energy reported for EVERY model? Drives
 *  whether the matrix shows an energy row at all so vertical alignment
 *  stays clean across the table. With N models we require all of them
 *  to report energy on at least one problem — partial data would lie
 *  about the ranking. */
export function shouldShowEnergyColumn(
  rows: CompareProblemRow[], models: string[],
): boolean {
  if (models.length < 2) return false;
  // Row qualifies if EVERY compared model has SOME energy value on it —
  // runtime energy preferred (arena-local etc.), generation energy fallback.
  return rows.some(r =>
    models.every(m => {
      const c = r.per_model[m];
      if (!c) return false;
      return c.runtime_energy_j != null || c.generation_energy_j != null;
    }),
  );
}


/** The bars themselves — one cell of the matrix. Width adapts to N, so
 *  three or four models fit alongside the existing numeric column without
 *  the table blowing out sideways too much. */
export default function CompareBars({
  models, cells, showEnergy,
}: {
  models: string[];
  cells: CompareCell[];          // parallel to `models`
  showEnergy: boolean;
}) {
  if (models.length !== cells.length) {
    // Defensive — the host must pass parallel arrays. Surface an empty
    // SVG so the table cell stays the same size rather than blowing up.
    return null;
  }
  const metrics = METRICS.filter(m => m.key !== "energy" || showEnergy);

  // Layout (per metric row, flat single-line):
  //
  //   [ Memory (MB) ]   [▌▌▌▌▌▌▌▌▌▌▌▌    ]   15.66 MB / 21.16 MB
  //                                          ^^^^^^^^^   ^^^^^^^^
  //                                          model-A    model-B
  //                                          colour     colour
  //
  // One text run per metric, with each model's value as its own coloured
  // <tspan> separated by " / ". Solves the stacked-value overlap problem
  // by eliminating the stacking — the whole row collapses to a single
  // line, vertically centred on the bar.
  const TRACK_W = 110;
  const TRACK_X = 92;
  const VALUE_X = TRACK_X + TRACK_W + 6;
  // Width grows with N — each model contributes ~7 chars ("113.4 ms" ≈ 50 px)
  // plus a 3-char " / " separator (~16 px). Conservative budget per model.
  const VALUE_PER_MODEL = 64;
  const VALUE_W = VALUE_PER_MODEL * models.length;
  const VALUE_FONT = "0.7rem";
  const BAR_H = 14;
  const PER_METRIC_H = 24;                  // bar (14) + 5 padding top + 5 bottom
  const TOTAL_W = VALUE_X + VALUE_W;
  const TOTAL_H = metrics.length * PER_METRIC_H + 4;

  return (
    /* overflow: visible lets the text spill out of the viewBox if a value
       still ends up wider than the value column (3-digit memory + a long
       float on energy can edge close). Without it the SVG silently clips
       the unit suffix and the row reads like "15.66" instead of
       "15.66 MB" — the exact symptom this whole edit chases down. */
    <svg width={TOTAL_W} height={TOTAL_H}
         style={{ display: "block", overflow: "visible",
                  fontFamily: "ui-sans-serif, system-ui" }}>
      {metrics.map((m, i) => {
        const yBase = i * PER_METRIC_H + 2;
        // Bar width = each model's RANK on this metric within the problem.
        // The winner (lowest ms/MB/J — lower is better on every axis) gets
        // the full track; others scale down as `(min / its_value) × TRACK_W`.
        // So a model twice as slow is half the bar. Bigger bar = better
        // in BOTH the runtime and memory rows, regardless of which model
        // wins each — the rows "align" in the sense that the winner always
        // has the longest bar in its row.
        const rawVals = cells.map(c => (c ? m.raw(c) : null));
        const present = rawVals.filter((v): v is number => v != null && v > 0);
        const minV = present.length ? Math.min(...present) : null;
        const widths = rawVals.map(v => {
          if (v == null || v <= 0 || minV == null) return 0;
          const score = present.length === 1 ? 100 : (minV / v) * 100;
          return (score / 100) * TRACK_W;
        });
        // Sort indices by width descending so we draw the biggest bar first
        // (underneath) and smaller ones layer on top, fully visible.
        const drawOrder = widths
          .map((w, idx) => ({ w, idx }))
          .sort((a, b) => b.w - a.w);
        return (
          <g key={m.key} transform={`translate(0, ${yBase})`}>
            {/* Always-visible metric label + unit. The unit was previously
                only in the right-side per-model value labels; when the host
                cell clipped those (or when a model's value was missing),
                the row had no indication of whether it was in ms or MB. */}
            <text x={TRACK_X - 6} y={PER_METRIC_H / 2 + 4}
                  textAnchor="end" fontSize="0.7rem" fill="var(--fg-dim)">
              <title>{m.unitTip}</title>
              {m.label} ({m.unit})
            </text>
            {/* Track background */}
            <rect x={TRACK_X} y={PER_METRIC_H / 2 - 7}
                  width={TRACK_W} height={14} rx={2}
                  fill="var(--panel-2)" stroke="var(--border)" />
            {/* Overlapping bars, largest first → smallest in front. */}
            {drawOrder.map((d, layer) => {
              const colour = MODEL_COLOURS[d.idx % MODEL_COLOURS.length];
              // Opacity ramps with layer order so the topmost (smallest)
              // bar reads cleanly even when several are close in length.
              const opacity = 0.5 + 0.4 * (layer / Math.max(1, drawOrder.length - 1));
              return (
                <rect key={d.idx}
                      x={TRACK_X} y={PER_METRIC_H / 2 - 7}
                      width={d.w} height={14} rx={2}
                      fill={colour} opacity={opacity}>
                  <title>{`${models[d.idx]}: ${rawTextFor(m, cells[d.idx])}`}</title>
                </rect>
              );
            })}
            {/* All model values on ONE line, separated by " / ", each in
                its model's colour. The text element's y is the SVG
                baseline; PER_METRIC_H/2 + 4 puts the baseline roughly on
                the bar's vertical centre — same as the metric label. */}
            <text x={VALUE_X} y={PER_METRIC_H / 2 + 4}
                  fontSize={VALUE_FONT}
                  style={{ fontVariantNumeric: "tabular-nums" }}>
              {models.map((mName, idx) => (
                <Fragment key={mName}>
                  {idx > 0 && (
                    <tspan fill="var(--fg-dim)"> / </tspan>
                  )}
                  <tspan fill={MODEL_COLOURS[idx % MODEL_COLOURS.length]}>
                    <title>{`${mName}: ${rawTextFor(m, cells[idx])}`}</title>
                    {rawTextFor(m, cells[idx])}
                  </tspan>
                </Fragment>
              ))}
            </text>
          </g>
        );
      })}
    </svg>
  );
}


function rawTextFor(m: MetricSpec, cell: CompareCell): string {
  if (!cell) return "—";
  const v = m.raw(cell);
  if (v == null) return "—";
  const digits = v < 10 ? 2 : 1;
  return `${v.toFixed(digits)} ${m.unit}`;
}
