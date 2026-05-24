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
