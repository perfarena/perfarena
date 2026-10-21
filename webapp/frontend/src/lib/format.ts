export const DASH = "–";

export function formatRuntime(ms: number | null | undefined): string {
  if (ms == null) return DASH;
  if (ms === 0) return "0 ms";
  // Sub-millisecond values (arena-local per-call measurements can be
  // 10⁻⁴ ms) would clip to "0.00 ms" under the old fixed-decimal rule.
  // Scale down to µs / ns using the same SI-prefix pattern as formatEnergy.
  const abs = Math.abs(ms);
  let unit = "ms";
  let scaled = ms;
  if      (abs >= 1)     { unit = "ms"; scaled = ms;        }
  else if (abs >= 1e-3)  { unit = "µs"; scaled = ms * 1e3;  }
  else if (abs >= 1e-6)  { unit = "ns"; scaled = ms * 1e6;  }
  else                   { unit = "ps"; scaled = ms * 1e9;  }
  const a = Math.abs(scaled);
  const dp = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return `${scaled.toFixed(dp)} ${unit}`;
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

/** Bradley-Terry win probability (0..1) as a percentage. */
export function formatWinProb(p: number | null | undefined): string {
  if (p == null) return DASH;
  return `${(p * 100).toFixed(1)}%`;
}

/** US dollars: 4 decimals below $0.01 (most LLM calls), fewer above. */
export function formatDollar(v: number | null | undefined): string {
  if (v == null) return DASH;
  if (v === 0) return "$0";
  if (v < 0.01)  return `$${v.toFixed(4)}`;
  if (v < 1)     return `$${v.toFixed(3)}`;
  if (v < 1000)  return `$${v.toFixed(2)}`;
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
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
  if (j === 0) return "0 J";
  // Scale to the SI prefix that puts the display value in ~1..999. Covers
  // MJ (LLM-scale accumulated cost) all the way down to nJ (per-call CPU
  // energy on tiny benchmarks like the arena-local harness on 2-keys-
  // keyboard, ~2.5e-6 J). Without this range the formatter clipped every
  // tiny per-call reading to "0.00 J".
  const abs = Math.abs(j);
  let unit = "J";
  let scaled = j;
  if      (abs >= 1e6)  { unit = "MJ";  scaled = j / 1e6;  }
  else if (abs >= 1e3)  { unit = "kJ";  scaled = j / 1e3;  }
  else if (abs >= 1)    { unit = "J";   scaled = j;        }
  else if (abs >= 1e-3) { unit = "mJ";  scaled = j * 1e3;  }
  else if (abs >= 1e-6) { unit = "µJ";  scaled = j * 1e6;  }
  else if (abs >= 1e-9) { unit = "nJ";  scaled = j * 1e9;  }
  else                  { unit = "pJ";  scaled = j * 1e12; }
  // Adaptive precision inside the chosen unit — keep ~3 significant figures
  // so "486 µJ" doesn't render as "486.00 µJ" and "2.58 µJ" doesn't drop
  // its meaningful digits to "3 µJ".
  const a = Math.abs(scaled);
  const dp = a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3;
  return `${scaled.toFixed(dp)} ${unit}`;
}

/** Per-function-call time is reported in MICROSECONDS. Reuse formatRuntime's SI-scaler
 *  (which expects ms) by converting µs → ms, so tiny per function call times render as
 *  ns / µs and larger ones as ms with the same adaptive precision. e.g.
 *  50 → "50 µs", 1200 → "1.20 ms". Returns DASH for null. */
export function formatMicros(us: number | null | undefined): string {
  if (us == null) return DASH;
  return formatRuntime(us / 1e3);
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

/* ---- chart series formatters -------------------------------------------
 * A single-value formatter (formatRuntime/formatEnergy) picks a unit per
 * value, which is right for a table cell but wrong for a chart axis (points
 * would render in different units). These pick ONE unit for the whole series
 * — from its largest magnitude — so every point/tick on a chart is consistent.
 */

/** Time series in ms → a formatter locked to one unit (ms/µs/ns/ps). */
export function timeSeriesFormatterMs(
  valuesMs: (number | null | undefined)[],
): (v: number) => string {
  const nums = valuesMs.filter((v): v is number => v != null && isFinite(v)).map(Math.abs);
  const ref = nums.length ? Math.max(...nums) : 1;
  let unit = "ms", scale = 1;
  if      (ref >= 1)    { unit = "ms"; scale = 1;   }
  else if (ref >= 1e-3) { unit = "µs"; scale = 1e3; }
  else if (ref >= 1e-6) { unit = "ns"; scale = 1e6; }
  else                  { unit = "ps"; scale = 1e9; }
  return (v: number) => {
    const s = v * scale, a = Math.abs(s);
    return `${s.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2)} ${unit}`;
  };
}

/** Energy series in Joules → a formatter locked to one SI unit (MJ…pJ). */
export function energySeriesFormatter(
  valuesJ: (number | null | undefined)[],
): (v: number) => string {
  const nums = valuesJ.filter((v): v is number => v != null && isFinite(v)).map(Math.abs);
  const ref = nums.length ? Math.max(...nums) : 1;
  let unit = "J", scale = 1;
  if      (ref >= 1e6)  { unit = "MJ"; scale = 1e-6; }
  else if (ref >= 1e3)  { unit = "kJ"; scale = 1e-3; }
  else if (ref >= 1)    { unit = "J";  scale = 1;    }
  else if (ref >= 1e-3) { unit = "mJ"; scale = 1e3;  }
  else if (ref >= 1e-6) { unit = "µJ"; scale = 1e6;  }
  else if (ref >= 1e-9) { unit = "nJ"; scale = 1e9;  }
  else                  { unit = "pJ"; scale = 1e12; }
  return (v: number) => {
    const s = v * scale, a = Math.abs(s);
    return `${s.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2)} ${unit}`;
  };
}
