import { CompareByTagRow } from "../lib/api";
import { MODEL_COLOURS } from "./CompareHistogram";

/* Per-tag breakdown for the model comparison — three radar charts.
 *
 * One radar chart per metric: pass rate, avg runtime, avg memory. Each
 * spoke on a chart is a LeetCode tag (Array, Dynamic Programming, Math,
 * Graph, …) shared across the compared models; each model is one
 * coloured polygon overlaid on the others. Colours match the per-problem
 * matrix and the difficulty bar charts on the same page, so the eye
 * tracks the same model wherever it appears.
 *
 * Pure inline SVG — no chart library, same approach as CompareByLevel.
 *
 * Reading the polygons:
 *   pass rate     → LARGER polygon = better (more passes)
 *   avg runtime   → SMALLER polygon = better (faster code)
 *   avg memory    → SMALLER polygon = better (leaner code)
 * The chart header spells out which direction wins so the viewer doesn't
 * have to remember.
 *
 * Per-chart axes are normalised independently to the chart's own max, so
 * a model that's 2× faster on one tag still has visible separation from
 * a model that's 2× faster on a different tag — the spoke length is
 * "fraction of this chart's worst case", not "absolute ms". */

interface MetricSpec {
  key: keyof CompareByTagRow;
  label: string;
  unit: string;
  /** Format a value for the tooltip. */
  fmt: (v: number) => string;
  /** When TRUE, bigger polygon = better. When FALSE we INVERT the radius
   *  so a smaller raw value still draws a bigger polygon — that way the
   *  visual "this model dominates" reading is the same direction across
   *  all three charts. */
  higherIsBetter: boolean;
  /** Optional value transform (e.g. pass_rate × 100 to read on 0–100). */
  xform?: (v: number) => number;
}

const METRICS: MetricSpec[] = [
  {
    key: "pass_rate", label: "Pass rate by tag", unit: "%",
    fmt: v => `${v.toFixed(1)}%`,
    higherIsBetter: true,
    xform: v => v * 100,
  },
  {
    key: "avg_runtime_ms", label: "Avg runtime by tag", unit: "ms",
    fmt: v => v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v.toFixed(0)} ms`,
    higherIsBetter: false,
  },
  {
    key: "avg_memory_mb", label: "Peak mem by tag", unit: "MB",
    fmt: v => `${v.toFixed(1)} MB`,
    higherIsBetter: false,
  },
  {
    key: "avg_runtime_energy_j", label: "Avg runtime energy by tag", unit: "J",
    // Scales to mJ/µJ/nJ so arena-local micro-joule readings don't clip.
    fmt: v => {
      const abs = Math.abs(v);
      if (abs >= 1)     return `${v.toFixed(2)} J`;
      if (abs >= 1e-3)  return `${(v * 1e3).toFixed(2)} mJ`;
      if (abs >= 1e-6)  return `${(v * 1e6).toFixed(2)} µJ`;
      return `${(v * 1e9).toFixed(2)} nJ`;
    },
    higherIsBetter: false,
  },
];

/** Charts get dense fast; cap the number of spokes so labels don't
 *  collide. We pick the N tags with the most attempt mass across all
 *  models — the long-tail "appears once" tags drop off the radar. */
const MAX_TAGS_PER_CHART = 12;


export default function CompareByTag({
  models, rows,
}: { models: string[]; rows: CompareByTagRow[] }) {
  if (!rows.length || !models.length) return null;

  // Pick the dominant tags: order by total attempt count across all
  // models, descending; keep the top MAX_TAGS_PER_CHART. That keeps the
  // radar legible while still showcasing the categories where the
  // comparison has the most signal.
  const tagAttempts = new Map<string, number>();
  for (const r of rows) {
    tagAttempts.set(r.tag, (tagAttempts.get(r.tag) || 0) + r.n_attempted);
  }
  const tags = Array.from(tagAttempts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, MAX_TAGS_PER_CHART)
    .map(([t]) => t)
    .sort();    // alpha order for stable axis reading

  // Index rows by (model, tag) for O(1) lookup while drawing.
  const byCell = new Map<string, CompareByTagRow>();
  for (const r of rows) byCell.set(`${r.model_name}|${r.tag}`, r);

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="row" style={{ alignItems: "baseline", flexWrap: "wrap", gap: 12 }}>
        <h2 style={{ margin: 0 }}>Performance by problem category</h2>
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          one radar chart per metric · each spoke is a LeetCode tag ·
          polygon color = model · spoke length is normalized per chart,
          so a model that dominates one chart's "worst case" still reads
          as dominant relative to its peers
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
        gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
        gap: 16,
      }}>
        {METRICS.map(spec => {
          // Skip a chart entirely when no cell in scope has data for its
          // metric — prevents empty "memory" radars for arena-local imports
          // and empty "runtime energy" radars for LC-only submissions.
          const hasAny = models.some(model =>
            tags.some(tag => {
              const cell = byCell.get(`${model}|${tag}`);
              return cell != null && (cell as unknown as Record<string, number | null>)[spec.key as string] != null;
            }),
          );
          if (!hasAny) return null;
          return (
            <RadarChart key={spec.key as string}
                        spec={spec} models={models} tags={tags}
                        byCell={byCell} />
          );
        })}
      </div>

      {tagAttempts.size > tags.length && (
        <div className="muted" style={{ marginTop: 10, fontSize: "0.75rem" }}>
          Showing the top {tags.length} tags by attempt count.{" "}
          {tagAttempts.size - tags.length} less-common tag
          {tagAttempts.size - tags.length === 1 ? "" : "s"} hidden to keep
          the spokes readable.
        </div>
      )}
    </div>
  );
}


function RadarChart({
  spec, models, tags, byCell,
}: {
  spec: MetricSpec;
  models: string[];
  tags: string[];
  byCell: Map<string, CompareByTagRow>;
}) {
  // Resolve every (tag, model) into a raw numeric value (or null when
  // the model never solved a problem in that tag — pass_rate=0 we still
  // plot, but runtime/memory genuinely have no data point).
  const raw = tags.map(tag => models.map(model => {
    const r = byCell.get(`${model}|${tag}`);
    if (!r) return null;
    const v = r[spec.key] as number | null;
    if (v == null) return null;
    return spec.xform ? spec.xform(v) : v;
  }));

  // Per-chart max for normalising radii. We use the chart's own values
  // so even small absolute differences stretch the polygon — otherwise
  // a chart where everyone is ~30 ms would render as a tiny dot.
  const allVals = raw.flat().filter((v): v is number => v != null);
  const maxV = allVals.length ? Math.max(...allVals) : 1;

  // Layout.
  const W = 360;
  const H = 320;
  const cx = W / 2;
  const cy = H / 2 + 4;
  const R = Math.min(W, H) / 2 - 56;   // outer radius, label margin

  // Each tag sits at an evenly-spaced angle around the circle. Start at
  // 12 o'clock and go clockwise so it reads like a clock face.
  const N = tags.length || 1;
  const angle = (i: number) => -Math.PI / 2 + (2 * Math.PI * i) / N;

  /** Map raw value → spoke radius. For "lower-is-better" metrics we
   *  invert so a smaller raw value draws a longer spoke — that way a
   *  visually-bigger polygon means "this model is winning" across every
   *  chart on the page. */
  const radius = (v: number) => {
    if (maxV <= 0) return 0;
    const frac = spec.higherIsBetter ? (v / maxV) : (1 - v / maxV);
    return Math.max(2, R * Math.max(0, Math.min(1, frac)));
  };

  // Concentric gridlines at 25/50/75/100% of R. We draw them as N-gons
  // matching the spoke count so the grid mirrors the polygons exactly.
  const grid = [0.25, 0.5, 0.75, 1.0];
  const gridPath = (frac: number) =>
    tags.map((_, i) => {
      const r = R * frac;
      const a = angle(i);
      return `${i === 0 ? "M" : "L"} ${cx + r * Math.cos(a)} ${cy + r * Math.sin(a)}`;
    }).join(" ") + " Z";

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <div style={{ fontWeight: 600 }}>{spec.label}</div>
        <span className="muted" style={{ fontSize: "0.75rem" }}>
          ({spec.higherIsBetter
            ? "larger polygon = better"
            : "larger polygon = better, radius inverted from " + spec.unit})
        </span>
      </div>
      <svg width={W} height={H}
           style={{ display: "block", overflow: "visible" }}>
        {/* Grid rings */}
        {grid.map(frac => (
          <path key={frac} d={gridPath(frac)}
                fill="none" stroke="var(--panel-2)"
                strokeDasharray="2 4" />
        ))}
        {/* Spokes from centre to each tag corner */}
        {tags.map((_, i) => {
          const a = angle(i);
          return (
            <line key={i}
                  x1={cx} y1={cy}
                  x2={cx + R * Math.cos(a)}
                  y2={cy + R * Math.sin(a)}
                  stroke="var(--border)" />
          );
        })}
        {/* Tag labels around the perimeter */}
        {tags.map((tag, i) => {
          const a = angle(i);
          const lr = R + 14;
          const x = cx + lr * Math.cos(a);
          const y = cy + lr * Math.sin(a);
          // textAnchor by angle quadrant — keep labels reading outward.
          const cos = Math.cos(a);
          const anchor =
            cos > 0.25 ? "start" : cos < -0.25 ? "end" : "middle";
          // Truncate long tags so the polygon stays the visual hero.
          const display = tag.length > 16 ? tag.slice(0, 14) + "…" : tag;
          return (
            <text key={tag}
                  x={x} y={y}
                  textAnchor={anchor as any}
                  dominantBaseline="central"
                  fontSize="0.7rem"
                  fill="var(--fg)">
              {display}
              <title>{tag}</title>
            </text>
          );
        })}
        {/* One polygon per model */}
        {models.map((model, mi) => {
          const colour = MODEL_COLOURS[mi % MODEL_COLOURS.length];
          const points: { x: number; y: number; v: number | null; tag: string }[] =
            tags.map((tag, i) => {
              const v = raw[i][mi];
              const a = angle(i);
              const r = v == null ? 0 : radius(v);
              return {
                x: cx + r * Math.cos(a),
                y: cy + r * Math.sin(a),
                v, tag,
              };
            });
          // Skip a model entirely if it has no data on any spoke — saves
          // the radar from being washed out by ghost polygons at zero.
          if (points.every(p => p.v == null)) return null;
          const path = points.map((p, i) =>
            `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`
          ).join(" ") + " Z";
          return (
            <g key={model}>
              <path d={path}
                    fill={colour} fillOpacity={0.18}
                    stroke={colour} strokeWidth={2} />
              {/* Vertex dots — keyed by tag so React reconciliation is
                  stable; tooltip exposes the raw value the polygon was
                  drawn from rather than the inverted-radius version. */}
              {points.map(p => p.v == null ? null : (
                <circle key={p.tag}
                        cx={p.x} cy={p.y} r={3}
                        fill={colour}>
                  <title>{`${model} · ${p.tag}: ${spec.fmt(p.v)}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
        {/* Unit annotation in top-left */}
        <text x={4} y={14}
              fontSize="0.65rem" fill="var(--fg-dim)"
              style={{ fontVariantNumeric: "tabular-nums" }}>
          {spec.unit}
        </text>
      </svg>
    </div>
  );
}
