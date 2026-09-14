// UI feature flags. Flip and rebuild to change behaviour — no backend change.

/**
 * Show RELATIVE performance metrics (percentiles — LeetCode's "beats X% of
 * submissions", i.e. vs the reference/expert population). Turned OFF for now:
 * the platform surfaces ABSOLUTE metrics (runtime ms, memory MB, energy J)
 * only, until the relative methodology is finalized. Flip to `true` to restore
 * every percentile column/metric across the UI. The underlying data is still
 * collected and stored — this only hides it visually.
 */
export const SHOW_RELATIVE_METRICS = false;
