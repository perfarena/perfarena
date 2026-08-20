import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, CompetitionDetail } from "../lib/api";
import { formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";
import LanguageLink from "../components/LanguageLink";

/* /competitions/:slug — the membership list (which problems are in this
 * competition) plus the per-competition leaderboard top-5 by best pass@1. */
export default function CompetitionDetailPage() {
  const { slug = "" } = useParams();
  const [d, setD] = useState<CompetitionDetail | null>(null);
  const [err, setErr] = useState("");
  // Which harness's leaderboard slice is being viewed. null = "All"
  // (server pools across every harness that has data).
  const [harnessTab, setHarnessTab] = useState<string | null>(null);

  useEffect(() => {
    api.competition(slug, harnessTab)
      .then(setD).catch((e: unknown) => setErr(String(e)));
  }, [slug, harnessTab]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;

  const byLevel = { Easy: 0, Medium: 0, Hard: 0 } as Record<string, number>;
  for (const p of d.problems) byLevel[p.level] = (byLevel[p.level] ?? 0) + 1;

  return (
    <div className="col">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1 style={{ marginBottom: 4 }}>{d.name}</h1>
        <div className="muted">
          <code>{d.slug}</code> · {d.kind} ·{" "}
          <span className={`badge ${d.status === "active" ? "badge-easy" : "badge-medium"}`}
                style={{ fontSize: "0.75rem" }}>{d.status}</span>
        </div>
        {d.description && (
          <p style={{ marginTop: 12, whiteSpace: "pre-wrap", maxWidth: 760 }}>{d.description}</p>
        )}
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="problems"    value={String(d.n_problems)}
              tip={`Easy ${byLevel.Easy} · Medium ${byLevel.Medium} · Hard ${byLevel.Hard}`} />
        <Stat label="submissions" value={String(d.n_submissions)} />
        <Stat label="models"      value={String(d.n_models)} />
        <Stat label="languages"   value={String(d.languages.length)}
              tip={d.languages.join(", ") || "no submissions yet"} />
      </div>

      {d.problems.length > 0 && (
        <CompetitionShapeCharts problems={d.problems} />
      )}

      <h2 style={{ marginBottom: 4 }}>Top models</h2>
      {d.harnesses_with_data.length > 0 && (
        <div className="row" style={{ gap: 4, flexWrap: "wrap", marginBottom: 4 }}>
          <HarnessTab
            active={harnessTab === null}
            onClick={() => setHarnessTab(null)}
            label="All harnesses"
            hint="Pool submissions from every harness that has data on this competition."
          />
          {d.harnesses_with_data.map(h => (
            <HarnessTab
              key={h}
              active={harnessTab === h}
              onClick={() => setHarnessTab(h)}
              label={h}
              hint={`Show only submissions measured by the ${h} harness. Same competition, different measurement backend.`}
            />
          ))}
        </div>
      )}
      {d.top_models.length === 0 ? (
        <div className="empty">
          {harnessTab
            ? `No submissions on this competition measured by ${harnessTab}.`
            : "No judged submissions for this competition yet."}
        </div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>#</th><th>Model</th><th>Version</th>
              <th title="pass@1 across this competition's problems only: n_accepted / n_problems_in_competition. Independent of any other competition this model also competed in.">pass@1</th>
              <th title="pass@k across this competition's problems: n_accepted / n_attempted. Differs from pass@1 when the model didn't attempt every problem in the set.">pass@k</th>
              <th title="Problems solved out of total in this competition.">Solved</th>
              <th title="Average LeetCode-reported runtime across the model's accepted attempts on this competition's problems only.">Avg runtime</th>
              <th title="Average peak (max resident) memory across accepted attempts. Peak on both oracles, never per-op.">Peak mem</th>
              <th title="Average runtime percentile across accepted attempts.">Runtime %ile</th>
              <th title="Average memory percentile across accepted attempts.">Memory %ile</th>
              <th title="Total submissions this model has against this competition.">subs</th>
            </tr></thead>
            <tbody>
              {d.top_models.map((m, i) => (
                <tr key={`${m.model_name}-${m.model_version}`}>
                  <td className="metric">{i + 1}</td>
                  <td><Link to={`/models/${encodeURIComponent(m.model_name)}`}><b>{m.model_name}</b></Link></td>
                  <td className="muted">{m.model_version}</td>
                  <td className="metric">{formatPassAt1(m.best_pass_at_1)}</td>
                  <td className="metric">{formatPassAt1(m.pass_at_k)}</td>
                  <td className="metric">
                    {m.n_solved != null && m.n_problems != null
                      ? `${m.n_solved}/${m.n_problems}`
                      : "–"}
                  </td>
                  <td className="metric">{formatRuntime(m.avg_runtime_ms ?? null)}</td>
                  <td className="metric">{formatMemory(m.avg_memory_mb ?? null)}</td>
                  <td className="metric">{formatPercentile(m.avg_runtime_percentile ?? null)}</td>
                  <td className="metric">{formatPercentile(m.avg_memory_percentile ?? null)}</td>
                  <td className="metric">{m.n_submissions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 style={{ marginBottom: 4 }}>Problems in this competition</h2>
      {d.problems.length === 0 ? (
        <div className="empty">No problems linked yet.</div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>Slug</th><th>Title</th><th>Level</th><th>Tags</th>
            </tr></thead>
            <tbody>
              {d.problems.map(p => (
                <tr key={p.id}>
                  <td><Link to={`/problems/${p.title_slug}`}><code>{p.title_slug}</code></Link></td>
                  <td>{p.title}</td>
                  <td>
                    <span className={`badge badge-${p.level.toLowerCase()}`}>{p.level}</span>
                  </td>
                  <td className="muted">{p.tags.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d.languages.length > 0 && (
        <div className="muted" style={{ fontSize: "0.9rem" }}>
          Languages with submissions in this competition:{" "}
          {d.languages.map((l, i) => (
            <span key={l}>{i > 0 && ", "}<LanguageLink name={l} /></span>
          ))}
        </div>
      )}
    </div>
  );
}

function HarnessTab({
  active, onClick, label, hint,
}: { active: boolean; onClick: () => void; label: string; hint: string }) {
  return (
    <button
      className={active ? undefined : "secondary"}
      onClick={onClick}
      title={hint}
      style={{
        padding: "4px 10px", fontSize: "0.85rem",
        // Tab visually attached to the table below when active.
        borderBottomLeftRadius: active ? 0 : undefined,
        borderBottomRightRadius: active ? 0 : undefined,
      }}
    >
      {label}
    </button>
  );
}

function Stat({ label, value, tip }: { label: string; value: string; tip?: string }) {
  return (
    <div className="card" style={{ minWidth: 140 }} title={tip}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}


/* Per-competition shape charts: shows what the problem set LOOKS like
 * before any model talks to it.
 *   * difficulty bar: 3 columns (Easy / Medium / Hard) — quick read of
 *     whether the set is bottom-heavy, top-heavy, or balanced.
 *   * tag distribution: horizontal bar list capped at the top N tags by
 *     occurrence (long-tail rolled into "other") — sorted descending so
 *     the dominant categories surface at the top of the chart.
 *
 * Pure inline SVG to match every other chart on the site; no library. */
function CompetitionShapeCharts({
  problems,
}: { problems: Array<{ level: "Easy" | "Medium" | "Hard"; tags: string[] }> }) {
  // ----- per-level counts (3 buckets, fixed order Easy → Medium → Hard) -----
  const LEVELS: ("Easy" | "Medium" | "Hard")[] = ["Easy", "Medium", "Hard"];
  const levelCounts: Record<string, number> = { Easy: 0, Medium: 0, Hard: 0 };
  for (const p of problems) levelCounts[p.level]++;
  const maxLevel = Math.max(1, ...LEVELS.map(l => levelCounts[l]));

  // ----- per-tag counts (rolled across all problems) -----
  const tagCounts = new Map<string, number>();
  for (const p of problems) {
    for (const t of p.tags) {
      const tag = t.trim();
      if (!tag) continue;
      tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1);
    }
  }
  const TAG_CAP = 16;
  const sortedTags = Array.from(tagCounts.entries())
    .sort((a, b) => b[1] - a[1]);
  const headTags = sortedTags.slice(0, TAG_CAP);
  const tailTags = sortedTags.slice(TAG_CAP);
  const otherCount = tailTags.reduce((s, [, c]) => s + c, 0);
  const chartTags = otherCount > 0
    ? [...headTags, ["other (" + tailTags.length + ")", otherCount] as [string, number]]
    : headTags;
  const maxTag = Math.max(1, ...chartTags.map(([, c]) => c));

  // Colour palette pulls from the theme's --easy/--medium/--hard so the
  // bars carry the same visual semantics as everywhere else (Easy = green,
  // Hard = red, Medium = amber).
  const levelColour: Record<string, string> = {
    Easy: "var(--easy)", Medium: "var(--medium)", Hard: "var(--hard)",
  };

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="row" style={{ alignItems: "baseline", flexWrap: "wrap", gap: 12 }}>
        <h2 style={{ margin: 0 }}>Problem set composition</h2>
        <span className="muted" style={{ fontSize: "0.85rem" }}>
          shape of {problems.length} problem{problems.length === 1 ? "" : "s"} in this competition
          · bar height/length = count
        </span>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(220px, 1fr) minmax(320px, 2fr)",
        gap: 18,
        marginTop: 12,
      }}>
        {/* ---- Difficulty ---- */}
        <div className="col" style={{ gap: 4 }}>
          <div style={{ fontWeight: 600 }}>Difficulty</div>
          {(() => {
            const W = 240, H = 180;
            const PAD_L = 28, PAD_R = 8, PAD_T = 18, PAD_B = 28;
            const plotW = W - PAD_L - PAD_R;
            const plotH = H - PAD_T - PAD_B;
            const slot = plotW / LEVELS.length;
            const barW = Math.max(20, slot * 0.6);
            return (
              <svg width={W} height={H} style={{ display: "block", overflow: "visible" }}>
                <line x1={PAD_L} y1={PAD_T + plotH} x2={W - PAD_R} y2={PAD_T + plotH}
                      stroke="var(--border)" />
                {[0.25, 0.5, 0.75, 1].map(frac => (
                  <line key={frac}
                        x1={PAD_L} y1={PAD_T + plotH * (1 - frac)}
                        x2={W - PAD_R} y2={PAD_T + plotH * (1 - frac)}
                        stroke="var(--panel-2)" strokeDasharray="2 4" />
                ))}
                {LEVELS.map((lvl, i) => {
                  const v = levelCounts[lvl];
                  const h = plotH * (v / maxLevel);
                  const x = PAD_L + i * slot + (slot - barW) / 2;
                  const y = PAD_T + plotH - h;
                  return (
                    <g key={lvl}>
                      <rect x={x} y={y} width={barW} height={Math.max(2, h)}
                            fill={levelColour[lvl]} rx={3}>
                        <title>{`${lvl} · ${v} problem${v === 1 ? "" : "s"}`}</title>
                      </rect>
                      <text x={x + barW / 2} y={Math.max(10, y - 4)}
                            textAnchor="middle" fontSize="0.75rem"
                            fill="var(--fg-dim)"
                            style={{ fontVariantNumeric: "tabular-nums" }}>
                        {v}
                      </text>
                      <text x={x + barW / 2} y={PAD_T + plotH + 14}
                            textAnchor="middle" fontSize="0.78rem" fill="var(--fg)">
                        {lvl}
                      </text>
                    </g>
                  );
                })}
              </svg>
            );
          })()}
        </div>

        {/* ---- Tags ---- */}
        <div className="col" style={{ gap: 4 }}>
          <div style={{ fontWeight: 600 }}>
            Tags
            <span className="muted" style={{ fontSize: "0.78rem", marginLeft: 6 }}>
              ({tagCounts.size} unique
              {otherCount > 0 && `; top ${TAG_CAP} shown, ${tailTags.length} rolled into "other"`})
            </span>
          </div>
          {chartTags.length === 0 ? (
            <div className="muted" style={{ fontStyle: "italic" }}>No tags on any problem.</div>
          ) : (
            <div className="col" style={{ gap: 3 }}>
              {chartTags.map(([tag, count]) => {
                const pct = (count as number) / maxTag * 100;
                return (
                  <div key={tag} className="row" style={{ gap: 8, alignItems: "center" }}>
                    <div style={{
                      width: 140, fontSize: "0.82rem",
                      whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                    }} title={tag}>
                      {tag}
                    </div>
                    <div style={{ flex: 1, minWidth: 0, position: "relative",
                                  height: 16, background: "var(--panel-2)",
                                  border: "1px solid var(--border)", borderRadius: 3 }}>
                      <div style={{
                        height: "100%", width: `${pct}%`,
                        background: "var(--accent, #5a8eff)",
                        borderRadius: 2,
                      }} title={`${tag} · ${count}`} />
                    </div>
                    <div className="metric" style={{ width: 36, textAlign: "right", fontSize: "0.82rem" }}>
                      {count}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
