export const DASH = "–";

export function formatRuntime(ms: number | null | undefined): string {
  if (ms == null) return DASH;
  return `${ms.toFixed(ms < 10 ? 2 : 1)} ms`;
}

export function formatMemory(mb: number | null | undefined): string {
  if (mb == null) return DASH;
  return `${mb.toFixed(2)} MB`;
}

export function formatPercentile(p: number | null | undefined): string {
  if (p == null) return DASH;
  return `${p.toFixed(1)}%`;
}

export function formatPassAt1(frac: number | null | undefined): string {
  if (frac == null) return DASH;
  return `${(frac * 100).toFixed(1)}%`;
}

/** Generation wall-clock (ms) → compact human duration: "820 ms", "12.3 s",
 *  "2m 05s". Reasoning models can take minutes, so scale the unit. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return DASH;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

/** Energy (Joules) → compact human unit: "12 J", "1.4 kJ", "3.2 MJ".
 *  Returns DASH for null so callers can render conditionally. */
export function formatEnergy(j: number | null | undefined): string {
  if (j == null) return DASH;
  if (j < 1000) return `${j.toFixed(j < 10 ? 2 : 1)} J`;
  if (j < 1_000_000) return `${(j / 1000).toFixed(2)} kJ`;
  return `${(j / 1_000_000).toFixed(2)} MJ`;
}

export function formatCoefficient(c: number | null | undefined): string {
  if (c == null) return DASH;
  return `${c.toFixed(2)}×`;
}

export function coefficientClass(c: number | null | undefined): string {
  if (c == null) return "";
  if (c >= 1.05) return "coef-good";
  if (c <= 0.95) return "coef-bad";
  return "coef-mid";
}
