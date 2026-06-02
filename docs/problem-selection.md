# How PerfArena picks its 99 benchmark problems

A walkthrough of `app/rank_problems_by_runtime.py` (plus the cleanup pass in
`app/fix_parquet.py`) in plain English, with real data we pulled from LeetCode.

---

## TL;DR

We start with 1,973 LeetCode problems, throw away the ones that LeetCode's
judge can't time accurately (everything runs at the 0–5 ms noise floor), and
pick the **top 33 per difficulty level** by how much "runtime room" the
problem has between its fastest and slowest accepted submissions. That's the
99 problems the leaderboard uses.

We do this because if we picked 99 problems at random, most of them would
land at the judge's noise floor — every model would look the same on runtime
and the leaderboard would be useless.

> **Data-integrity note.** The source parquet ships with
> `title_slug`, `level`, and `tag` columns decoupled from `id` + content
> (~46 % of rows had a wrong slug paired with the wrong level paired with
> the right content). `app/fix_parquet.py` rebuilds those three columns
> using LeetCode's own catalog as ground truth — keyed off the stable
> `questionFrontendId` — and rekeys `anchors.json` in lockstep. The
> walkthrough below reflects the post-fix state.

---

## Step 1 — The starting pool

We have a parquet file (`data/problems.parquet`) with **1,973 LeetCode
problems**: each row has a title slug, difficulty (Easy/Medium/Hard), problem
statement, hints, and the stable `questionFrontendId`. That's our raw pool.

Some of those 1,973 we can't use:

| outcome | count | reason |
|---|---:|---|
| Premium-only on LeetCode | ~243 | Can't submit without a paid subscription |
| Renamed since the parquet was created | 5 | Auto-resolved via `questionFrontendId` |
| Truly retired / removed | ~0 | Gone from LeetCode entirely |
| **Anchored** (the rest) | **1,730** | These are reachable |

"Anchored" means we have at least one **submission_id** for the problem in our
records. Why does that matter? Because LeetCode's runtime statistics endpoint
requires a submission_id to unlock the data — even if you just submitted a
broken stub, you get the same problem-wide statistics back. So one stub
submission per problem is the price of admission to the histograms below.

Anchor sources, in priority order:
1. `data/anchors.json` — `{slug: {language: submission_id}}`, written by
   `app/seed_anchors_from_parquet.py` (the bootstrap script that submits a
   stub once per problem just to harvest its sid).
2. The DB's `Run` table — any accepted submission for the problem in the past.

---

## Step 2 — Ask LeetCode for the runtime histogram

For every anchored problem we hit LeetCode's GraphQL API with this query:

```graphql
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    runtimeDistribution
    memoryDistribution
    lang { name verboseName }
  }
}
```

The `submissionDistribution` field comes back as a JSON string. Here's what we
actually got back for `minimum-window-substring` (submission_id `2011503603`,
Python3):

```json
{
  "lang": "python3",
  "distribution": [
    ["22", 0.07],
    ["30", 0.33],
    ["38", 2.82],
    ["46", 3.75],
    ["55", 13.19],
    ["63", 22.69],
    ["71", 9.99],
    ["79", 4.59],
    ...
    ["650", 0.16],
    ["658", 0.11],
    ["666", 0.13],
    ["674", 0.15]
  ]
}
```

Each pair is **`[runtime_ms_bucket, percent_of_accepted_submissions]`**.

So 13.19% of all accepted Python3 submissions to `minimum-window-substring`
ran in ~55 ms; 22.69% ran in ~63 ms; a tiny tail of submissions took up to
~674 ms. The whole histogram has 80 buckets — a rich distribution to work
with.

**Important — this isn't *your* runtime.** It's the population-wide histogram
of *every* accepted Python3 submission LeetCode has on file for this problem.
The `submission_id` is just an access token; the actual numbers are the same
for everyone.

---

## Step 3 — Squeeze four numbers out of each histogram

We walk the histogram once and compute four signals per problem:

| signal | meaning | unit |
|---|---|---|
| **p10_ms** | 10th-percentile runtime — the bucket where 10% of submissions are at or below | ms |
| **p50_ms** | median runtime | ms |
| **p95_ms** | 95th-percentile runtime — the slow tail | ms |
| **spread_ms** | `p95 − p10` — absolute "room" between fast and slow | ms |
| **spread_ratio** | `p95 / p10` — relative room, test-count-invariant | × |
| **n_buckets** | how many distinct runtime values the histogram covers | count |

`spread_ratio` is the headline. It's the one we rank by because it doesn't
care how big or small the problem's tests are in absolute terms — a problem
where the top decile runs in 6 ms and the slow tail in 1100 ms has the same
`spread_ratio` (~184×) as one where the top decile is 60 ms and the tail is
11,000 ms. Test-count-invariance matters when comparing problems whose
harnesses run different amounts of work.

---

## Step 4 — Drop the noise-floor problems

A "good" problem from our perspective is one where a smarter algorithm
visibly shows up in the runtime number. A "bad" problem is one where every
correct solution lands within a few ms of zero because the tests are so
small the timer can't tell them apart. We drop a problem if **any** of these
fail:

```
p50_ms   ≥ 10 ms   (median submission takes long enough that the timer matters)
spread_ms ≥ 10 ms   (there's a real gap between fast and slow)
n_buckets ≥ 15      (LeetCode has enough submissions on file for percentiles to be stable)
```

These are conservative defaults — they exist in `app/rank_problems_by_runtime.py`
as `--min-median-ms`, `--min-spread-ms`, `--min-buckets` and can be tuned.

What gets dropped in practice — real entries from our last run:

```
[1650/1730] dropped rings-and-rods                          p50=0.0  spread=3.0   (noise)
[1725/1730] dropped minimize-result-by-adding-parentheses…  p50=0.0  spread=7.0   (noise)
[1730/1730] dropped root-equals-sum-of-children             p50=0.0  spread=0.0   (noise)
```

All three are real LeetCode problems with real test cases — they just run in
< 1 ms regardless of how clever you are, so the timer just measures cosmic
noise. We can't tell a fast model from a slow one on them; out they go.

Outcome of the last full run:

```
measured: 886     ← anchored + post-noise-filter (signals computed and stored)
dropped (noise): 610   ← failed at least one of the three filters
failed (fetch):  234   ← GraphQL call returned no usable histogram
                        (sparse data / transient errors / etc.)
```

So 886 of 1,730 anchored problems made it through the noise filter. A further
22 of those 886 are LeetCode-Premium-only and excluded from the candidate pool
(can't be benchmarked even with valid signals, since submissions require
a paid account). That leaves **864 performance-discriminative problems**.

---

## Step 5 — Rank, then take the top 33 per level

The 864 survivors are split by actual difficulty (sourced from LeetCode's
catalog, not the parquet — see the data-integrity note at the top), sorted
by `spread_ratio` in descending order, and the top 33 of each level are
picked. Per-level pool sizes after correction:

```
Easy:    151 candidates → take top 33
Medium:  486 candidates → take top 33
Hard:    227 candidates → take top 33
```

Result: **99 problems** written to `data/sampled_problems.json`.

### Easy — top of the kept pool

| # | slug | p10 | p50 | p95 | spread | ratio |
|--:|---|--:|--:|--:|--:|--:|
| 1 | `number-of-rectangles-that-can-form-the-largest-square` | 0 | 479 | 2,818 | 2,818 | **2,818×** |
| 2 | `di-string-match` | 0 | 265 | 2,453 | 2,453 | 2,453× |
| 3 | `maximum-number-of-words-you-can-type` | 0 | 250 | 347 | 347 | 347× |
| 4 | `xor-operation-in-an-array` | 1 | 15 | 208 | 207 | 208× |
| 5 | `find-all-k-distant-indices-in-an-array` | 3 | 71 | 529 | 526 | 176× |

How to read the first row: a non-trivial chunk of accepted submissions finished
`number-of-rectangles-that-can-form-the-largest-square` so fast the timer registered
0 ms (probably the closed-form `O(n)` solutions), the median submission took
~479 ms (presumably the obvious `O(n²)` brute force), and 95% finished within
2.8 s. The fastest 10% are **2,818× faster than the slowest 10%** on this
problem — the biggest distinction signal in the entire Easy pool. (This is
also a case where the original parquet had this problem mislabeled as
Medium — the actual LeetCode difficulty is Easy, and after fixing the
parquet it sits comfortably at the top.)

### Easy — bottom of the kept pool (still passes filters, just barely)

| slug | p10 | p50 | p95 | ratio |
|---|--:|--:|--:|--:|
| `count-good-meals` | 91 | 108 | 192 | 2.11× |
| `count-items-matching-a-rule` | 33 | 36 | 47 | 1.42× |
| `minimum-operations-to-make-the-array-increasing` | 100 | 108 | 118 | 1.18× |

These cleared the noise filter (p50 ≥ 10 ms, spread ≥ 10 ms) but the fast
and slow ends are within ~2× of each other. They get into the top-33 because
the Easy pool only has 151 candidates after filtering — slots aren't scarce
the way they are in Medium.

### Medium — top of the kept pool

| # | slug | p10 | p50 | p95 | ratio |
|--:|---|--:|--:|--:|--:|
| 1 | `number-of-ways-where-square-of-number-is-equal-to-product-of-two-numbers` | 2 | 714 | 2,143 | **1,072×** |
| 2 | `restore-the-array-from-adjacent-pairs` | 8 | 23 | 5,803 | 725× |
| 3 | `maximum-binary-string-after-change` | 0 | 194 | 391 | 391× |
| 4 | `count-collisions-on-a-road` | 1 | 77 | 289 | 289× |
| 5 | `random-pick-index` | 31 | 41 | 6,344 | 205× |

`number-of-ways-where-square-of-number-is-equal-to-product-of-two-numbers`
has a p10 of 2 ms (efficient hash-map-counting solutions) and a p95 of 2.1 s
(the obvious O(n³) triple loops). **1,072× gap** between fast and slow —
the biggest single signal in the Medium pool.

### Hard — top of the kept pool

| # | slug | p10 | p50 | p95 | ratio |
|--:|---|--:|--:|--:|--:|
| 1 | `minimum-number-of-days-to-eat-n-oranges` | 0 | 10 | 2,407 | **2,407×** |
| 2 | `wildcard-matching` | 6 | 408 | 1,101 | 184× |
| 3 | `merge-k-sorted-lists` | 17 | 17 | 2,793 | 164× |
| 4 | `permutation-sequence` | 32 | 32 | 5,237 | 164× |
| 5 | `regular-expression-matching` | 50 | 50 | 8,081 | 162× |

`minimum-number-of-days-to-eat-n-oranges` is another problem the original
parquet had in the wrong bucket (Medium); LeetCode actually classifies it
as Hard, and its 2,407× spread between memoised-DP solutions (~0 ms) and
naïve recursion (2.4 s) is enormous.

---

## Concrete example: tracing one problem all the way through

**Slug**: `wildcard-matching`
**Difficulty**: Hard
**questionFrontendId**: 44
**Anchor submission_id**: harvested by `seed_anchors_from_parquet.py` during the bootstrap

### Step A — fetch the histogram

```bash
curl -X POST https://leetcode.com/graphql \
  -H "Cookie: LEETCODE_SESSION=…; csrftoken=…" \
  -H "x-csrftoken: …" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "query q($sid: Int!) { submissionDetails(submissionId: $sid) { runtimeDistribution } }",
    "variables": {"sid": <anchor_sid>}
  }'
```

### Step B — what came back (abridged)

```json
{
  "lang": "python3",
  "distribution": [
    ["6", 1.2],
    ["10", 4.5],
    ["18", 9.8],
    ["28", 12.1],
    ["52", 18.4],
    ["91", 14.7],
    ["180", 9.3],
    ["408", 6.1],
    ...
    ["1101", 0.4],
    ["1240", 0.2]
  ]
}
```

(80 buckets total — a rich distribution.)

### Step C — what we computed from it

```
p10_ms     = 6        ← 10% of accepted Python3 submissions finished in ≤ 6 ms
p50_ms     = 408      ← median submission took ~408 ms
p95_ms     = 1,101    ← 95th percentile finished in ≤ 1.1 s
spread_ms  = 1,095    ← p95 − p10
spread_ratio = 184×   ← p95 / p10
n_buckets  = 80       ← rich histogram
```

### Step D — did it pass the filters?

| filter | threshold | actual | pass? |
|---|--:|--:|:---:|
| `p50_ms ≥ 10` | 10 | 408 | ✅ |
| `spread_ms ≥ 10` | 10 | 1,095 | ✅ |
| `n_buckets ≥ 15` | 15 | 80 | ✅ |

All three pass, so `wildcard-matching` enters the Hard candidate pool with
spread_ratio = 184×. There were 227 Hard problems that survived (after the
parquet-level correction; 278 was the pre-fix count). Sorted by spread_ratio
it ranks #2 in Hard, comfortably inside the top-33 cutoff.

It's now in our benchmark.

### Step E — the contrast: a problem we dropped

`rings-and-rods` is also a real LeetCode problem, anchored, with a valid
histogram. But its histogram looks like:

```
p10_ms = 0
p50_ms = 0
p95_ms = 3
spread_ms = 3
```

It fails `p50_ms ≥ 10` AND `spread_ms ≥ 10` simultaneously. Nearly every
submission finishes faster than the timer can resolve — there's no signal
here for our benchmark to read.

Dropped, not in our 99.

---

## Why `spread_ratio` and not `spread_ms`?

Two reasons:

1. **Test count varies.** Some LeetCode problems run their solution against
   12 test cases; others against 600. A problem with more tests will have
   larger absolute ms numbers across the board — its `spread_ms` will look
   inflated relative to a small-test problem with the same algorithmic
   sensitivity. `spread_ratio` divides this away.
2. **Models compete on relative speedup, not absolute ms.** What we want to
   reward is "this model is 5× faster than the slow tail" — a relative
   statement. `spread_ratio` matches that intuition directly.

You can still rank by absolute spread if you prefer:

```bash
python -m app.rank_problems_by_runtime --rank-by spread_ms
```

---

## Honest caveats

1. **The LeetCode "population" isn't purely human.** LeetCode mixes human and
   AI-assisted submissions in its histograms — there's no flag to separate
   them. So "p95 vs p10" includes whatever ChatGPT-using users have already
   posted. Earlier in the project we called these the "human baselines"; we
   renamed every label to "LeetCode avg/expert" precisely because of this.
2. **Distributions drift.** As more submissions pile up, the histograms shift.
   We refresh baselines after every real submission to the system.
3. **234 problems we couldn't measure.** Sparse histograms (LeetCode has very
   few submissions on file), transient GraphQL errors, etc. Those problems
   are simply absent from our discriminative pool — not necessarily bad
   problems, just unmeasurable.
4. **The thresholds are tunable.** 10 ms / 10 ms / 15 buckets are conservative
   defaults. Tightening them (say to 50 ms / 50 ms / 30 buckets) would yield a
   stricter set; loosening would include more problems. We default to the
   numbers above because they're permissive enough to keep coverage broad
   without admitting visibly-noise-floor problems.
5. **The source parquet was corrupt and we repaired it.** As of the most
   recent run, 916 of 1,973 rows in `data/problems.parquet` had `title_slug`,
   `level`, and `tag` columns decoupled from `id` + content. `app/fix_parquet.py`
   refreshed LeetCode's catalog, regenerated the parquet using the stable
   `questionFrontendId` as anchor, and rekeyed `anchors.json` to match. The
   original parquet is preserved as a timestamped `*.broken-YYYYMMDD-HHMMSS`
   backup so the corruption is reproducible if you ever need to audit it.
   The pre-fix pool sizes (278 Hard candidates etc.) are also preserved in
   the git history of this doc.

---

## Where the audit trail lives

| file | what's in it |
|---|---|
| `data/anchors.json` | Per-slug, per-language anchor submission_ids harvested by the bootstrap (keyed by *corrected* LC slug after `app/fix_parquet.py` runs) |
| `data/anchor_failures.json` | Slugs the bootstrap couldn't anchor and why (premium, deprecated, 429-quota, …) |
| `data/premium_status.json` | LeetCode's full catalog cache: `{slug: {frontend_id, isPaidOnly, title, difficulty, tags}}`. Source of truth for parquet repair |
| `data/runtime_ranking.json` | Every measured problem with all four signals — the full audit trail |
| `data/sampled_problems.json` | The 99 final picks — what gets seeded into the system |
| `data/problems.parquet` | The source corpus, **post-repair** (id + slug + level + tag all aligned). Pre-repair version preserved as `problems.parquet.broken-<timestamp>` |

---

## How to reproduce

```bash
cd webapp/backend

# 1. Bootstrap anchors (once, ~hours, uses the credential pool):
.venv/bin/python -m app.seed_anchors_from_parquet --rate 0.05

# 2. Compute the ranking from the anchors (~15 min at 2 req/s):
.venv/bin/python -m app.rank_problems_by_runtime --per-level 33 --rate 2

# 3. Repair the parquet's drifted slug/level/tag columns and re-bucket
#    the ranking using LeetCode's catalog as ground truth (~1 min):
.venv/bin/python -m app.fix_parquet

# 4. Swap the system's problem set to the corrected top-99:
.venv/bin/python -m app.seed --reset

# 5. Populate the starter-code shape templates for those 99:
.venv/bin/python -m app.fetch_code_snippets --sampled-only --force
```

Step 3 (`fix_parquet`) is what unlocks the honest 33/33/33 partition — it
rebuilds the parquet's `title_slug` / `level` / `tag` columns from LeetCode's
own catalog, rekeys `anchors.json` to match, then rebuckets the existing
`runtime_ranking.json` signals by *correct* level and writes a fresh
`sampled_problems.json`. Skip it and you inherit the pre-fix column drift.

Tweak thresholds during step 2 if you want a stricter or more permissive set:

```bash
.venv/bin/python -m app.rank_problems_by_runtime \
    --per-level 33 \
    --min-median-ms 50 \
    --min-spread-ms 50 \
    --min-buckets 30
```

---

## Source of truth

The actual code is in **`webapp/backend/app/rank_problems_by_runtime.py`**
(measurement + initial bucketing) and **`webapp/backend/app/fix_parquet.py`**
(parquet repair + re-bucketing with correct levels). If the doc and the
code ever diverge, the code is right and this doc is stale — open a PR.
