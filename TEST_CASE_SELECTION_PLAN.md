# PerfArena — Test-Case Selection: Full Plan, Comparison, and Status

*Working document. Plain-language plan for selecting a reduced set of test cases
for energy/memory benchmarking of LLM-generated LeetCode solutions, how it
differs from the originally-proposed approach, current status, and the
future measurement-reliability work.*

---

## 1. The problem in one sentence

Measuring the energy of **every test case × every problem × every language ×
every model** does not scale. For each problem we keep a small handful of test
cases that still represent all the *kinds* of work the code does, and reuse that
handful everywhere. Solutions are already verified correct against the full
suite, so the reduced set is a **measurement workload, not a correctness gate**.

---

## 2. The plan, end to end (simple terms)

**Step 0 — Get good inputs for each problem.**
Each problem needs a set of test inputs, shared across all languages (they're
just data, so language-independent). Where LeetCode's own inputs were missing or
too small, we *generate* synthetic ones — including **big and worst-case
inputs**, because tiny inputs can't tell a good solution from a bad one.

**Step 1 — Make each test run long enough to measure.**
LeetCode inputs finish in milliseconds — too short for an energy meter. So we run
each input in a **loop for ~1 second**, count the iterations, and stop the
compiler from deleting "useless" work. Energy of one run = total energy ÷ loops.

**Step 2 — Fingerprint each test case.**
On one "reference" language (Python), on a dedicated Linux machine with energy
sensors, run each test and record **how the CPU behaved** (instructions, cache
misses, branch mispredictions) plus **the energy used**. This fingerprint
describes the *kind* of work each test does — not just its size.

**Step 3 — Group similar tests, keep one from each group.**
Per problem, group the fingerprints that look alike and keep **one
representative (medoid) per group**, plus **always keep the single hardest
(most-energy) input** so the worst case is never lost.

**Step 4 — Record how common each group is (weights).**
Each representative's weight = how many tests its group contains. A weighted
average over the kept cases then reproduces what the full set would have given.

**Step 5 — Reuse the same inputs across all languages.**
Fingerprint on the reference language only. Check the grouping still holds for
1–2 other languages; if a problem behaves differently in another language, keep
the **union** of both languages' groups and run **identical inputs everywhere**
(otherwise cross-language comparison isn't fair).

**Step 6 — Prove the small set is trustworthy (pilot).**
On a few problems × languages × models, measure **both** the full set and the
reduced set, confirm they **rank solutions the same way** (Kendall/Spearman),
and sweep the "how many groups to keep" knob (τ) to pick an accurate-but-cheap
setting.

**Step 7 — Run the real benchmark.**
Measure only the kept inputs, at the real ~1s budget, with the energy sensor,
for every language and model. Combine per problem using the weights.

---

## 3. Comparison with the originally-proposed approach

The original 4-phase plan got the **core method right** and we kept it; building
it revealed gaps to fill and one thing to correct.

### Kept (unchanged)
- The heart: **fingerprint → cluster → keep one medoid per cluster** (SimPoint).
- **Fingerprint on one anchor language**, reuse across languages.
- A **validation pilot** proving the small set gives the same answers.
- The **τ knob** and the instinct to **keep rare/worst-case modes**.
- The **threats-to-validity** list still applies.

### Added (missing from the original)
- **Input generation (biggest addition).** The original assumed each problem
  already had a good, long-running suite. Reality: LeetCode inputs are tiny and
  can't discriminate good code from bad, so generating big/worst-case inputs
  became a required first step — arguably the most important one.
- **Loop-to-a-time-budget.** The original treated "runs long enough" as a
  checkbox; we found inputs run in ms, so we actively loop each to ~1s and had to
  defend the loop against dead-code elimination, JIT warmup, and cached state.
- **Measure energy and fingerprint in one pass** (original separated them).
- **A skeptical self-check:** *does the fancy method even beat sorting by size?*
  The original never questioned itself; we did, and it shaped the decisions.
- **Always keep the worst case**, and **explicit group weights** for correct
  averaging (the original noted the weighting as a "threat" only).
- A **cap of 100 test cases per problem** (seeded-random sample if more).

### Changed / corrected
- **Cross-language on disagreement.** Original said: pick *per-language* subsets
  for problems that behave differently. That **breaks fair language comparison**
  — you must run the *same* inputs everywhere. Corrected to: keep the **union**
  of the languages' groups, identical inputs for all.
- **Shared inputs are mandatory.** The old setup fed *different* inputs to
  different languages (a real bug); the plan now enforces one shared input file
  per problem.
- **Consistency over hybrid.** Rather than "fancy where it helps, simple
  elsewhere," we use the **fancy method everywhere** — it costs almost nothing
  extra (fingerprinting is one-time, already done) and never does worse.

### Confirmed (the original's bet paid off)
- Behavior modes that input-size **cannot see do exist**, and more often than a
  first glance suggests. A *fair* test — cluster each problem on the full
  fingerprint vs. on size alone, same k, compare with Adjusted Rand Index —
  gives **mean ARI 0.54 / median 0.60**: fingerprinting produces a **genuinely
  different grouping than size for ~41/99 problems** (ARI < 0.5) and is
  near-identical for only ~27/99 (ARI > 0.8).
- The dominant *axis* is still size (PC1↔size correlation median 0.94), but the
  *residual* behavior splits cases enough that the clustering differs — so this
  is not a 12–20% minority effect; it's ~40%.
- The lowest-ARI problems are exactly the ones with strong best/worst-case
  regimes: `word-ladder` (two same-size inputs, ~10× energy), `scramble-string`,
  `partition-to-k-equal-sum-subsets`, `verbal-arithmetic-puzzle`
  (backtracking/memoization). This is why **"fancy method everywhere"** is
  well-justified: it costs nothing extra and captures real structure on a large
  fraction of problems, while agreeing with size on the rest.

---

## 4. Current status

**Done:**
- Inputs for **all 99 problems** (incl. generated synthetic inputs for the 16
  that lacked them — trees/linked-lists/design classes got bespoke object-
  building harnesses).
- Loop-harness (Python) + fingerprint **and** RAPL-energy sweep over **all 99
  problems** on a bare-metal Intel Linux box (perf + RAPL validated).
- Clustering, medoid selection, weights, worst-case-always-kept, **maximin
  space-filling to a ≥5-case floor** → frozen curated set: **8,063 measured cases
  → 501 kept (6.2%, ~16× reduction)**, min 5 / median 5 / max 8 per problem.
- Empirical split: **79 size-dominated, 20 behavior-mode** problems.
- **Coverage guardrail — space-filling, not branch coverage.** The ≥5 floor is
  filled by **greedy farthest-first (maximin / k-center) sampling** over a
  COMBINED space: the PCA fingerprint axes **plus** the log-energy response axis.
  This is a recognized space-filling design (grounded in DoE, not ad-hoc), and it
  resolves the plan's own "diversity vs proportional" tension: **medoids** give
  proportional representation, **maximin** gives diversity/coverage, **worst-case**
  gives the extreme. It covers BOTH behavior and cost: fingerprint gap
  1.38→1.22, energy gap max 0.44→0.29 vs the 1-D-energy or fingerprint-only
  variants. The exact floor / weights are to be locked by the validation pilot
  (Step 6); soundness is proven by subset-vs-full ranking agreement, not by the
  coverage number itself.

**To do (needs the measurement box):**
- Cross-language harnesses + the **transfer check** (Step 5).
- The **validation pilot** (Step 6) — ratifies the whole approach.
- The **real full benchmark** (Step 7).

---

## 5. Future work — reliable *and* fast benchmarking

To be added once the test cases are selected. The core trade-off: each benchmark
run must be **long enough to be reliable** (variance, interference, thermal) but
**fast enough not to delay populating the rankings**. Reliability and speed are
not purely opposed — lowering the noise floor means fewer repetitions.

1. **Kill variance at the source (helps reliability *and* speed).**
   Pin to one physical core (`taskset`, disable the sibling HT); fix the CPU
   frequency (disable turbo, `performance` governor / fixed clock — DVFS is the
   biggest energy-variance source); quiet the machine (no desktop, stop
   services); manage thermals (plugged in, run below the throttle point, cooldown
   gaps, monitor temp/freq).

2. **Calibrate the noise floor once.** Run one solution ~30× and compute the CV.
   Good control → CV ~1–2% → ~3–5 reps suffice. Poor control → improve step 1.
   Target: **measurement uncertainty < ranking resolution** (to distinguish >5%
   energy differences, need a 95% CI narrower than ~2–3%).

3. **Adaptive stopping (main speed trick).** Not a fixed rep count — after each
   rep, compute the running CI and **stop when it's tight enough** (floor ~3,
   cap ~8–10). Interleave reps across solutions (drift → noise, not bias). Use
   the **median** (robust to interrupted runs).

4. **Auto-detect interference.** Record CPU frequency + temperature per run; if
   the core throttled or a background task stole time, **discard and redo** —
   don't poison the median.

5. **Two-tier measurement (keeps rankings fast to populate).** A **provisional**
   pass (min reps, short window) gives a submission a ranking within minutes; a
   background **confirmation** pass upgrades it to "confirmed" at full budget.
   Show a provisional/confirmed badge in the UI.

6. **Report uncertainty.** Publish each number with its CI; differences smaller
   than the noise are shown as **ties**, not a false ordering.

7. **The hard constraint: energy measurement is serial.** RAPL measures the
   whole package (this chip exposes only package-level energy), so parallel
   measurements contaminate each other — reliable energy is **one core at a
   time**. Mitigate with adaptive stopping + two-tier; if submission volume
   exceeds one box, **scale horizontally** (more identical boxes, shard
   problems/submissions).

**Rough time math:** ~3.3 cases/problem × ~1.5s (warmup+window) × ~4 reps ≈ 20s
per (problem, language, solution); a 99-problem submission in one language ≈
**~30 min confirmed / ~10 min provisional**. If too slow for volume → add a box.

**Build items (when the box is up):** a measurement driver (core pinning + fixed
frequency + warmup + adaptive reps with CI stopping + interleaving + throttle
gating); a noise-floor calibration run to set the CI target from real data; wire
the two-tier flow into how the arena populates rankings.

---

## 6. Reference: the reduced-set artifact

`curated_selection.json` — per problem: the kept cases with `role`
(`medoid` / `worst-case` / `medoid+worst`), `cluster`, `weight` (medoid weights
sum to 1.0 → correct aggregation; worst-case weight 0 = peak metric, not folded
into the mean), `size`, and `energy_uj_per_op`. Reproducible from the fingerprint
dataset (`fp_all.jsonl`) via `curate.py` (fixed seeds, versioned).
