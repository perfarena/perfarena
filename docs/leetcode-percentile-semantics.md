# LeetCode runtime / memory percentile — what it is, what it isn't

When PerfArena dispatches an attempt, LeetCode returns four numbers per
run: `runtime_ms`, `memory_mb`, `runtime_percentile`, `memory_percentile`.
The first two are wall-clock measurements on LC's grader; the last two
are LC's ranking of those measurements against "the LC submission
population for this (problem, language)". This document is an honest
account of how the percentile fields actually behave **in our data**,
what's documented vs. what we have to infer, and the engineering choices
that follow from it.

The TL;DR is at the bottom — read the verified findings first.

---

## 1. What we know for sure (verified from code or data)

### 1.1 The dispatch + aggregation pipeline (ours, fully verified)

For every Attempt we ship the same code to LC `ARENA_DISPATCH_REPEATS`
times (default 5). Each of those N submissions is a separate Run row
with its own `runtime_ms` and `runtime_percentile`. We do the
aggregation:

| Attempt field             | Definition                            |
|---------------------------|----------------------------------------|
| `avg_runtime_ms`          | arithmetic mean over N accepted runs   |
| `best_runtime_ms`         | `min` over N accepted runs             |
| `avg_runtime_percentile`  | arithmetic mean over N percentile values |
| `best_runtime_percentile` | `max` over N percentile values         |

Same shape for memory. Those aggregates feed the Submission-level
rollups (means over per-problem aggregates). So when the leaderboard
shows "Avg runtime" and "Runtime %ile" for a submission, it's a mean
of 5-run means over ~99 problems = ~500 underlying LC measurements.
The aggregation logic lives in
[`app/dispatch.py:_aggregate_attempt`](../webapp/backend/app/dispatch.py).

### 1.2 LC's distributions ARE bucket histograms

Confirmed empirically — we fetch the raw histograms ourselves in
`app/pull_baselines.py` via the `submissionDistribution` GraphQL query.
The response is literally a list of `[value, percent_of_population]`
tuples; we parse them in `app/leetcode_dist.py:derive`. So:

* The distribution is discrete (one entry per histogram bucket).
* Two close-but-distinct ms values can sit in adjacent buckets, and the
  population-fraction difference between adjacent buckets can be
  arbitrarily large.
* The "your runtime beats X %" number is *consistent with* but not
  *necessarily computed from* the same histogram — see §2.1 below.

### 1.3 The per-run percentile field varies even when ms is similar

Measured on the live PerfArena DB (6,327 accepted runs, 1,258 attempts
with ≥3 accepted runs):

| Metric                                                  | Value         |
|---------------------------------------------------------|---------------|
| Mean intra-attempt %ile spread (max − min over 5 runs)  | **3.97 pp**   |
| Median intra-attempt %ile spread                        | 3.71 pp       |
| 90th-%ile spread                                        | 6.58 pp       |
| Attempts where `ms ↑` ⇒ `%ile ↓` strictly monotone      | **1.3 %**     |
| Mean intra-attempt ms spread                            | 21.45 ms      |

In words: the same code, submitted 5 times in a single dispatch run
(seconds apart), produces percentile values that swing by ~4 pp on
average and almost never line up monotonically with the runtime. The
within-attempt percentile field is unambiguously noisy.

### 1.4 ms ranking and %ile ranking are uncorrelated across attempts

For every (problem, language) pair with ≥2 attempts in our DB
(212 pairs, 1,137 cross-attempt comparisons), we asked: does the attempt
with the lower mean ms also have the higher mean %ile?

| Aggregation used   | "faster ⇒ higher %ile" agreement |
|--------------------|----------------------------------|
| MEAN ms + MEAN %ile| **48.1 %**                       |
| BEST ms + BEST %ile| **49.6 %**                       |
| (random would be)  | 50.0 %                           |

The two signals are statistically independent at the per-attempt level
in our data. Switching from mean to best barely moves the needle —
which means whatever noise dominates, it's not specifically a
mean-vs-best problem.

### 1.5 Java distributions cluster hard at the fast end

Cross-checked by `app.measure_discrimination` on our DB *and* by
multiple LC community sources (see §3). Java problems frequently have
> 50 % of the population in a single histogram bucket (commonly 0 ms
or 1 ms), so the relationship between absolute ms and percentile is
sharply non-linear at the fast end — a 1 ms swing can move you 30 pp
in either direction depending on which side of the modal bucket you
land on.

---

## 2. What we INFERRED but COULD NOT VERIFY

These are honest open questions. Don't quote them as fact.

### 2.1 How LC computes its `runtime_percentile` field is undocumented

LeetCode does not publish how the `runtime_percentile` field on the
`submissionCheck` response is computed. The community has competing
hypotheses:

* **Live moving target**: percentile is recomputed against a current
  snapshot of the (problem, language) submission distribution at
  submission time. Plausible mechanism; **no source we could find
  documents this.**

* **Fixed historical reference**: percentile is computed against a
  snapshot taken at some past moment, never re-baselined. Argued in
  [Why LeetCode Shows 100% Beat Rate](https://medium.com/@ismoil.793/why-leetcode-shows-100-beat-rate-and-0ms-runtime-the-real-reason-behind-it-847ca256e304),
  though that source cites no LC documentation either.

* **Per-run drift**: percentile is recomputed each time but the
  denominator is small/skewed enough that rounding boundaries dominate
  the noise.

[GitHub Feedback #23242](https://github.com/LeetCode-Feedback/LeetCode-Feedback/issues/23242)
documents a case where the percentile field doesn't even match the
visible histogram: 23 ms runtime reported at the 25.39th percentile
when 85.77 % of the visible population was ≥ 25 ms. So **at minimum
the percentile field doesn't behave as a clean function of (your
runtime, current histogram)**. The cause is undocumented and we don't
know it. **All we know is the field is noisy and sometimes inconsistent
with what the histogram would predict.**

### 2.2 What dominates the runtime ms noise

Two independent sources of variability:

* Server load on LC's grader (well-attested across multiple bug reports:
  [#10289](https://github.com/LeetCode-Feedback/LeetCode-Feedback/issues/10289),
  the [identical-code thread](https://leetcode.com/discuss/general-discussion/136683/different-run-time-with-same-code/) —
  identical code logged 140 ms ↔ 306 ms swings).

* JVM warmup (well-attested for Java specifically — see the
  [Java Runtime Optimization Meta](https://leetcode.com/discuss/post/6274472/New-Java-Runtime-Optimization-Meta-Please-Fix/) thread).
  The author of that exploit documents that running the solution 200×
  in a static initializer reproducibly lowers reported runtime.

We can't separate these from our DB alone. The practical implication
is the same: **the ms PerfArena receives back from LC has high
per-run variance**, and any single run is a noisy data point.

---

## 3. Engineering decisions this drives in PerfArena

### 3.1 Per-problem view ("Top solutions on this problem")

* Headline runtime / memory / %ile are the **best of the 5 dispatch
  repeats** (`Attempt.best_runtime_ms`, `Attempt.best_runtime_percentile`,
  etc.). Rationale: a single run's number — which is what the page
  used to show — is observably noisy enough to flip rankings (1.3 %
  monotone agreement within a single attempt). Best-of-5 is the most
  stable estimator of "what this code can do".
* The mean across the 5 is shown next to each headline value in muted
  text (`113.4 ms · 102.3 ms` ← best · avg) so the noise band is
  visible without dominating the column.
* A one-line caveat above the table points users at this document.

### 3.2 Platform leaderboard (`/rankings`)

**Unchanged.** Each row aggregates ~500 underlying LC measurements,
which smooths the per-run noise enough that the mean is the right
summary at that level of aggregation. Demoting it to a tooltip would
falsely imply the underlying signal is *more* reliable in "best" form
than the data supports. Tooltips on the %ile columns now mention the
per-run noise floor and link to this doc instead of presenting
percentile as an unconditional ranking.

### 3.3 Submission detail (per-attempt detail panel)

The "spread of measurements" runtime/memory plots already show the 5
individual runs and call out the spread explicitly. They are the
right tool for "is this attempt's number stable or noisy?" and need
no change.

### 3.4 What we deliberately did NOT do

* We did **not** rewrite anywhere in the UI to claim LC's percentile
  is computed against a live snapshot, against a historical reference,
  or with any specific denominator. The mechanism is unverified.

* We did **not** change the platform-leaderboard headline columns to
  best-of-5. With ~500-sample averaging the mean is fine; flipping it
  would suggest a precision the data doesn't support.

* We did **not** drop the percentile columns entirely. Per-run %ile is
  noisy, ~500-sample mean is much less noisy, and it's still the only
  comparable cross-language signal we have (raw ms isn't comparable
  across languages at all).

---

## 4. Sources used to verify (and to discard) the above

* [Why LeetCode Shows 100% Beat Rate and 0ms Runtime — Medium](https://medium.com/@ismoil.793/why-leetcode-shows-100-beat-rate-and-0ms-runtime-the-real-reason-behind-it-847ca256e304) (community speculation, no LC source)
* [Different run time with same code? — LeetCode Discuss](https://leetcode.com/discuss/general-discussion/136683/different-run-time-with-same-code/) (community, multiple cases of same code ↔ different ms)
* [Inconsistent submitted Runtime in python 3 — Feedback #10289](https://github.com/LeetCode-Feedback/LeetCode-Feedback/issues/10289) (140 ms ↔ 306 ms documented; no LC staff response)
* [Runtime percentile calculation incorrect — Feedback #23242](https://github.com/LeetCode-Feedback/LeetCode-Feedback/issues/23242) (23 ms → 25th %ile when 85 %+ of population was ≥ 25 ms; no LC staff response)
* [New Java Runtime Optimization Meta — LC Discuss](https://leetcode.com/discuss/post/6274472/New-Java-Runtime-Optimization-Meta-Please-Fix/) (JIT-pre-warming exploit, direct evidence Java ms is JIT-state dependent)
* [How to cheat at LeetCode and achieve 0ms runtime — Medium](https://medium.com/@monkey_types/how-to-cheat-at-leetcode-and-achieve-0ms-runtime-for-every-problem-f90945571d36) (Python-side exploit, evidence ms is wall-clock and writable from process state)

LeetCode's own help center / API docs do not document the percentile
computation. If you find a primary LC source that does, please update
this file.

---

## TL;DR

* **Verified:** Within a single attempt's 5 dispatch runs, LC's
  reported percentile swings ~4 pp on average and is non-monotonic
  with runtime in 98.7 % of attempts. Across attempts on the same
  (problem, language), the ranking by mean ms and the ranking by
  mean percentile agree only 48 % of the time — essentially random.

* **Verified:** LC distributions are bucket histograms (we fetch
  them). Java distributions cluster hard at the fast end (frequent
  > 50 % single-bucket dominance), making percentile a sharply
  non-linear function of ms in Java specifically.

* **Inferred but unverified:** Whether LC's percentile uses a live
  snapshot, a fixed historical reference, or something else.
  Whether the noise sits in the percentile field itself, in the
  underlying ms measurement, or both. Don't quote these as fact.

* **What we do about it:** Per-problem view shows best-of-5 headline
  with mean alongside. Platform leaderboard keeps the mean because
  ~500-sample aggregation smooths the noise floor. Tooltips link to
  this doc instead of presenting percentile as a clean rank.
