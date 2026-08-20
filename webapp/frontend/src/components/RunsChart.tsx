import { AttemptRunDetail } from "../lib/api";

/* Per-run line charts for a single attempt's repeated dispatches.
 *
 * LeetCode's judge runs on shared infra; the same code submitted N times
 * gets N different runtime/memory readings (±10–30% per-shot variance is
 * normal). The chart makes that spread visible so reviewers can tell at a
 * glance whether the aggregate numbers come from a tight cluster or a
 * noisy run. Two side-by-side mini-charts so the y-axes stay honest
 * (different units; overlaying would distort one or the other).
 *
 * Hand-rolled inline SVG so we don't pull in a charting library for a
 * 100-line component. Theme-aware via CSS custom properties.
 */

interface MiniLineChartProps {
  label: string;
  values: (number | null)[];          // one slot per run_index, null for rejected
  color: string;                       // CSS color (var() works)
  formatValue: (v: number) => string;
}

function MiniLineChart({ label, values, color, formatValue }: MiniLineChartProps) {
  // Keep only runs that actually produced a measurement; skip rejected ones
  // (LeetCode returns null runtime/memory for compile errors / WA / TLE).
  const points = values
    .map((v, i) => ({ i: i + 1, v }))
    .filter((p): p is { i: number; v: number } => p.v != null);
  if (points.length === 0) {
    return (
      <div className="col" style={{ gap: 4, minWidth: 220 }}>
        <div style={{ fontWeight: 600 }}>{label}</div>
        <div className="muted" style={{ fontSize: "0.8rem" }}>
          no accepted runs to plot
        </div>
      </div>
    );
  }

  // Geometry — viewBox-based so CSS can stretch it to fit the parent.
  const W = 400, H = 200;
  const PAD_L = 44, PAD_R = 16, PAD_T = 14, PAD_B = 28;

  const xs = points.map(p => p.i);
  const ys = points.map(p => p.v);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minVal = Math.min(...ys);
  const maxVal = Math.max(...ys);
  const mean = ys.reduce((a, b) => a + b, 0) / ys.length;
  // Add 10% headroom on the Y axis so the line doesn't kiss the chart edge.
  // Guard against a degenerate range (all-equal values, or N=1).
  const yRange = (maxVal - minVal) || Math.abs(maxVal * 0.1) || 1;
  const yLo = minVal - yRange * 0.15;
  const yHi = maxVal + yRange * 0.15;
  const xRange = (maxX - minX) || 1;

  const x = (i: number) => PAD_L + ((i - minX) / xRange) * (W - PAD_L - PAD_R);
  const y = (v: number) => PAD_T + (1 - (v - yLo) / (yHi - yLo)) * (H - PAD_T - PAD_B);

  const meanY = y(mean);
  const path = points
    .map((p, idx) => `${idx === 0 ? "M" : "L"} ${x(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`)
    .join(" ");
  const spread = maxVal - minVal;
  const spreadPct = mean ? (spread / mean) * 100 : 0;

  // Pick a sensible number of x-axis ticks — every run if ≤ 10, otherwise
  // a sample so labels don't overlap.
  const tickStep = points.length <= 10 ? 1 : Math.ceil(points.length / 8);
  const ticks = points.filter((_, idx) => idx % tickStep === 0).map(p => p.i);

  return (
    <div className="col" style={{ gap: 4, minWidth: 260, flex: "1 1 320px" }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
        <div style={{ fontWeight: 600 }}>{label}</div>
        <div className="muted" style={{ fontSize: "0.72rem" }}>
          min {formatValue(minVal)} · mean {formatValue(mean)} · max {formatValue(maxVal)}
          {spreadPct > 0 && <> · spread {formatValue(spread)} ({spreadPct.toFixed(0)}%)</>}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}
           role="img" aria-label={`${label} across ${points.length} runs`}>
        {/* Background panel matches the surrounding card */}
        <rect x={0} y={0} width={W} height={H} fill="var(--panel-2)" rx={4} />
        {/* Axes */}
        <line x1={PAD_L} y1={H - PAD_B} x2={W - PAD_R} y2={H - PAD_B}
              stroke="var(--border)" />
        <line x1={PAD_L} y1={PAD_T} x2={PAD_L} y2={H - PAD_B}
              stroke="var(--border)" />
        {/* Mean reference line (dashed) */}
        <line x1={PAD_L} y1={meanY} x2={W - PAD_R} y2={meanY}
              stroke={color} strokeOpacity={0.45} strokeDasharray="4 4" />
        <text x={W - PAD_R - 4} y={meanY - 4} textAnchor="end" fontSize="10"
              fill={color} fillOpacity={0.85}>
          mean
        </text>
        {/* Y-axis labels: yHi (top), mean (middle, on the axis), yLo (bottom) */}
        <text x={PAD_L - 6} y={PAD_T + 4} textAnchor="end" fontSize="10"
              fill="var(--fg-dim)">{formatValue(yHi)}</text>
        <text x={PAD_L - 6} y={meanY + 3} textAnchor="end" fontSize="10"
              fill={color} fillOpacity={0.85}>{formatValue(mean)}</text>
        <text x={PAD_L - 6} y={H - PAD_B} textAnchor="end" fontSize="10"
              fill="var(--fg-dim)">{formatValue(yLo)}</text>
        {/* X-axis tick labels */}
        {ticks.map(t => (
          <text key={t} x={x(t)} y={H - PAD_B + 14} textAnchor="middle"
                fontSize="10" fill="var(--fg-dim)">#{t}</text>
        ))}
        {/* The line itself */}
        <path d={path} fill="none" stroke={color} strokeWidth={1.8}
              strokeLinejoin="round" strokeLinecap="round" />
        {/* Markers with native browser tooltips on hover (no extra deps) */}
        {points.map(p => (
          <g key={p.i}>
            <circle cx={x(p.i)} cy={y(p.v)} r={3.5} fill={color}
                    stroke="var(--panel-2)" strokeWidth={1} />
            <title>Run #{p.i}: {formatValue(p.v)}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

export function RunsChart({ runs }: { runs: AttemptRunDetail[] }) {
  // Single-run attempts have nothing to "spread" — hide the chart entirely.
  if (runs.length < 2) return null;

  const sorted = [...runs].sort((a, b) => a.run_index - b.run_index);
  const runtimes = sorted.map(r => r.runtime_ms);
  const memories = sorted.map(r => r.memory_mb);

  // Both metrics absent? Nothing useful to draw.
  if (runtimes.every(v => v == null) && memories.every(v => v == null)) return null;

  return (
    <div className="col" style={{ gap: 12, padding: "12px 14px" }}>
      <div className="muted" style={{ fontSize: "0.78rem" }}>
        Spread of measurements across the {runs.length} repeated dispatches.
        LeetCode runs submissions on shared infra, so the same code typically
        swings by 10 to 30% per shot. The dashed line is the mean over accepted runs.
      </div>
      <div className="row" style={{ gap: 20, flexWrap: "wrap" }}>
        <MiniLineChart
          label="Runtime per run"
          values={runtimes}
          color="var(--accent)"
          formatValue={(v) => `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`}
        />
        <MiniLineChart
          label="Peak mem per run"
          values={memories}
          color="var(--warn)"
          formatValue={(v) => `${v.toFixed(2)} MB`}
        />
      </div>
    </div>
  );
}
