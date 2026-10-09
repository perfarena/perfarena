import { EfficiencyRankEntry } from "../lib/api";

/* Per-language model-ranking "bump" chart, hand-rolled inline SVG to match the
 * rest of the site (no chart library). x-axis = languages, y-axis = rank
 * (1 = best / lowest ratio). One line per model; each model keeps one color and
 * a direct label at its right end, so identity never rests on color alone. */

// Same 14-color categorical palette the other charts use (CorrectnessVsPerformance).
const PALETTE = [
  "#7c8cff", "#4ade80", "#fbbf24", "#f87171", "#22d3ee", "#c084fc",
  "#fb923c", "#34d399", "#f472b6", "#a3e635", "#60a5fa", "#facc15",
  "#fca5a5", "#2dd4bf",
];
const LANG_LABEL: Record<string, string> = {
  cpp: "C++", csharp: "C#", java: "Java", php: "PHP",
  python3: "Py", typescript: "TS",
};
const shortModel = (m: string) => (m.includes("/") ? m.split("/").pop()! : m);

export default function BumpChart({
  title, languages, ranked,
}: {
  title: string;
  languages: string[];
  ranked: Record<string, EfficiencyRankEntry[]>;   // language -> best-first list
}) {
  const langs = languages.filter(l => (ranked[l]?.length ?? 0) > 0);
  if (langs.length === 0) {
    return <div className="empty">No {title.toLowerCase()} data for this competition.</div>;
  }

  // model -> language -> { rank, ratio }
  const byModel = new Map<string, Map<string, { rank: number; ratio: number }>>();
  for (const l of langs) {
    (ranked[l] ?? []).forEach((e, i) => {
      if (!byModel.has(e.model)) byModel.set(e.model, new Map());
      byModel.get(e.model)!.set(l, { rank: i + 1, ratio: e.ratio });
    });
  }
  const nRank = Math.max(...langs.map(l => ranked[l]?.length ?? 0));
  // Order models by average rank: stable color assignment + fewer line crossings.
  const avgRank = (m: string) => {
    const v = [...byModel.get(m)!.values()].map(x => x.rank);
    return v.reduce((a, b) => a + b, 0) / v.length;
  };
  const ordered = [...byModel.keys()].sort((a, b) => avgRank(a) - avgRank(b));
  const color = new Map(ordered.map((m, i) => [m, PALETTE[i % PALETTE.length]]));

  const PADL = 26, PADR = 148, PADT = 26, PADB = 22;
  const W = 720, H = PADT + PADB + Math.max(1, nRank) * 26;
  const x = (i: number) => PADL + (langs.length === 1 ? 0 : (i / (langs.length - 1)) * (W - PADL - PADR));
  const y = (rank: number) => PADT + (nRank === 1 ? 0 : ((rank - 1) / (nRank - 1)) * (H - PADT - PADB));

  return (
    <div className="card" style={{ padding: 8, overflowX: "auto" }}>
      <div className="muted" style={{ fontSize: "0.85rem", marginBottom: 4 }}>
        {title} — rank by language (1 = best; each cell is the median ratio to the best correct solution)
      </div>
      <svg width={W} height={H} style={{ display: "block" }}>
        {/* rank guides on the left */}
        <text x={2} y={y(1) + 3} fontSize="0.65rem" fill="var(--fg-dim)">1</text>
        <text x={2} y={y(nRank) + 3} fontSize="0.65rem" fill="var(--fg-dim)">{nRank}</text>
        {/* language columns + headers */}
        {langs.map((l, i) => (
          <g key={l}>
            <line x1={x(i)} x2={x(i)} y1={PADT - 6} y2={H - PADB + 6}
                  stroke="var(--border)" strokeDasharray="2 3" />
            <text x={x(i)} y={14} fontSize="0.72rem" fill="var(--fg-dim)" textAnchor="middle">
              {LANG_LABEL[l] ?? l}
            </text>
          </g>
        ))}
        {/* one line per model */}
        {ordered.map(m => {
          const pts = langs
            .map((l, i) => { const r = byModel.get(m)!.get(l); return r ? { i, rank: r.rank, ratio: r.ratio } : null; })
            .filter((p): p is { i: number; rank: number; ratio: number } => p !== null);
          if (pts.length === 0) return null;
          const col = color.get(m)!;
          const end = pts[pts.length - 1];
          return (
            <g key={m}>
              <polyline fill="none" stroke={col} strokeWidth={2}
                        strokeLinejoin="round" strokeLinecap="round" opacity={0.9}
                        points={pts.map(p => `${x(p.i)},${y(p.rank)}`).join(" ")} />
              {pts.map(p => (
                <circle key={p.i} cx={x(p.i)} cy={y(p.rank)} r={4}
                        fill={col} stroke="var(--panel-2)" strokeWidth={1}>
                  <title>{`${shortModel(m)} · ${LANG_LABEL[langs[p.i]] ?? langs[p.i]}\nrank ${p.rank} · ratio ${p.ratio.toFixed(2)}`}</title>
                </circle>
              ))}
              <text x={x(end.i) + 8} y={y(end.rank) + 3.5} fontSize="0.7rem" fill="var(--fg)">
                {shortModel(m)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
