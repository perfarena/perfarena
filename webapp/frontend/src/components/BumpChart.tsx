import { useEffect, useRef, useState } from "react";
import { EfficiencyRankEntry } from "../lib/api";

/* Per-language model-ranking "bump" chart, hand-rolled inline SVG to match the
 * rest of the site (no chart library). x-axis = languages, y-axis = rank
 * (1 = best / highest win probability). One line per model; each model keeps one color and
 * a direct label at its right end, so identity never rests on color alone.
 * The chart measures its card and lays out for that width, so the text keeps
 * one size from a phone to a wide screen. */

// Same 14-color categorical palette the other charts use (CorrectnessVsPerformance).
const PALETTE = [
  "#7c8cff", "#4ade80", "#fbbf24", "#f87171", "#22d3ee", "#c084fc",
  "#fb923c", "#34d399", "#f472b6", "#a3e635", "#60a5fa", "#facc15",
  "#fca5a5", "#2dd4bf",
];
const LANG_LABEL: Record<string, string> = {
  cpp: "C++", csharp: "C#", java: "Java", javascript: "JS", php: "PHP",
  python3: "Py", typescript: "TS",
};
const shortModel = (m: string) => (m.includes("/") ? m.split("/").pop()! : m);

type Ranked = Record<string, EfficiencyRankEntry[]>;     // language -> best-first list

/** One color per model across several charts, so a model keeps its color from
 *  one metric to the next. Models are ordered by mean rank over all charts. */
export function bumpColors(charts: Ranked[]): Map<string, string> {
  const ranks = new Map<string, number[]>();
  for (const ranked of charts) {
    for (const list of Object.values(ranked)) {
      list.forEach((e, i) => {
        if (!ranks.has(e.model)) ranks.set(e.model, []);
        ranks.get(e.model)!.push(i + 1);
      });
    }
  }
  const mean = (m: string) => { const v = ranks.get(m)!; return v.reduce((a, b) => a + b, 0) / v.length; };
  const ordered = [...ranks.keys()].sort((a, b) => mean(a) - mean(b) || a.localeCompare(b));
  return new Map(ordered.map((m, i) => [m, PALETTE[i % PALETTE.length]]));
}

export default function BumpChart({
  title, languages, ranked, colors,
}: {
  title: string;
  languages: string[];
  ranked: Ranked;
  colors?: Map<string, string>;     // shared model colors (default: this chart's own)
}) {
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(720);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(300, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const langs = languages.filter(l => (ranked[l]?.length ?? 0) > 0);
  if (langs.length === 0) {
    return <div className="empty">No {title.toLowerCase()} data for this competition.</div>;
  }

  // model -> language -> { rank, win probability }
  const byModel = new Map<string, Map<string, { rank: number; winProb: number }>>();
  for (const l of langs) {
    (ranked[l] ?? []).forEach((e, i) => {
      if (!byModel.has(e.model)) byModel.set(e.model, new Map());
      byModel.get(e.model)!.set(l, { rank: i + 1, winProb: e.win_prob });
    });
  }
  const nRank = Math.max(...langs.map(l => ranked[l]?.length ?? 0));
  const color = colors ?? bumpColors([ranked]);
  const ordered = [...byModel.keys()];

  // Geometry in pixels of the measured card width.
  const PADL = 18, PADR = 124, PADT = 26, PADB = 12, ROW = 24;
  const H = PADT + PADB + Math.max(1, nRank - 1) * ROW;
  const x = (i: number) => PADL + (langs.length === 1 ? 0 : (i / (langs.length - 1)) * (W - PADL - PADR));
  const y = (rank: number) => PADT + (nRank === 1 ? 0 : ((rank - 1) / (nRank - 1)) * (H - PADT - PADB));

  return (
    <div className="card" style={{ padding: 12 }}>
      <div ref={box}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}
           role="img" aria-label={`${title}: model rank by language`}>
        {/* rank guides on the left */}
        <text x={0} y={y(1) + 4} fontSize={11} fill="var(--fg-dim)">1</text>
        <text x={0} y={y(nRank) + 4} fontSize={11} fill="var(--fg-dim)">{nRank}</text>
        {/* language columns + headers */}
        {langs.map((l, i) => (
          <g key={l}>
            <line x1={x(i)} x2={x(i)} y1={PADT - 6} y2={H - PADB + 6}
                  stroke="var(--border)" strokeDasharray="2 3" />
            <text x={x(i)} y={12} fontSize={12} fill="var(--fg-dim)" textAnchor="middle">
              {LANG_LABEL[l] ?? l}
            </text>
          </g>
        ))}
        {/* one line per model */}
        {ordered.map(m => {
          const pts = langs
            .map((l, i) => { const r = byModel.get(m)!.get(l); return r ? { i, rank: r.rank, winProb: r.winProb } : null; })
            .filter((p): p is { i: number; rank: number; winProb: number } => p !== null);
          if (pts.length === 0) return null;
          const col = color.get(m) ?? PALETTE[0];
          const end = pts[pts.length - 1];
          return (
            <g key={m}>
              <polyline fill="none" stroke={col} strokeWidth={2}
                        strokeLinejoin="round" strokeLinecap="round" opacity={0.9}
                        points={pts.map(p => `${x(p.i)},${y(p.rank)}`).join(" ")} />
              {pts.map(p => (
                <circle key={p.i} cx={x(p.i)} cy={y(p.rank)} r={4}
                        fill={col} stroke="var(--panel-2)" strokeWidth={1}>
                  <title>{`${shortModel(m)} · ${LANG_LABEL[langs[p.i]] ?? langs[p.i]}\nrank ${p.rank} · win probability ${(p.winProb * 100).toFixed(1)}%`}</title>
                </circle>
              ))}
              <text x={x(end.i) + 8} y={y(end.rank) + 4} fontSize={12} fill="var(--fg)">
                {shortModel(m)}
              </text>
            </g>
          );
        })}
      </svg>
      </div>
    </div>
  );
}
