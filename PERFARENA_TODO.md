# PerfArena — open tasks & decisions

Plain-language TODO for the per-language energy benchmark. Written 2026-08-04.

**One-line background:** we measure how much energy each programming language
burns to solve LeetCode problems. Every problem has a handful of chosen example
inputs ("curated test cases", ~5 per problem). Each case is run in a loop for
≥1 second so the timing/energy is stable, then normalized to a "per-op"
(per single solve) number that's fair to compare across languages.

Files referenced below live under
`Energy-Languages/selection/`.

---

## ✅ Already done (for context)

- Every language runs every curated case for ≥1 second (measured + confirmed).
- All 10 languages cover all 99 problems (C++/Rust/Go tree/list/design added).
- Each measured submission records its **energy tool** + **machine** + **per-op**
  numbers in the database.
- Fixed along the way: PHP memory cap, a slow Ruby solution, a Rust
  compile-time hang, several broken solution files.

---

## 🔲 TASK 1 — Switch to the cleaned test-case list

### What it is (simple)
We discovered **27 of the chosen test inputs are "illegal"** — they're bigger
than the problem's own rules allow. Example: `stickers-to-spell-word` says the
target word is at most **15** letters, but one test input uses a **26**-letter
word. These illegal inputs were quietly making the energy numbers dishonest
(the fast languages just ran them without complaining; PHP/Ruby crashed on them,
which is how we noticed).

I already made a **corrected copy** of the list with those 27 removed:
`selection/curated_selection.cleaned.json` (474 good cases; the original had 501).
The weights were re-balanced so each problem's cases still add up to 100%.
**Nothing is live yet** — the system still uses the old list.

### The decision
Do we make the corrected list the one the system actually uses?

| Option | What happens | Effort |
|---|---|---|
| **A. Adopt it (recommended)** | Replace the live `curated_selection.json` with the cleaned copy. From now on only valid inputs get measured. | ~1 min, this Mac. Reversible (keep a backup). |
| **B. Keep both, don't switch yet** | Nothing changes; revisit later. Measurements keep including the 27 bad inputs until then. | none |
| **C. Review the 27 first** | I show the full flagged list; you approve/veto case-by-case before adopting. | ~10 min review |

**How to adopt (Option A):**
```bash
cd Energy-Languages/selection
cp curated_selection.json curated_selection.json.bak-preclean   # safety backup
cp curated_selection.cleaned.json curated_selection.json
```

**Recommendation:** A. Illegal inputs shouldn't stay in a benchmark.

---

## 🔲 TASK 2 — Refill the 15 problems that got too thin

### What it is (simple)
Removing the 27 illegal inputs left **15 problems with too few test cases**.
We aim for ~5 valid cases per problem; some are now down to 2, and one
(`uncommon-words-from-two-sentences`) is down to **just 1**. Fewer cases = a
less representative energy number for those problems.

To fix this properly we need to **create fresh, valid test inputs** for those 15
problems and pick a good spread of them again ("re-fingerprinting"). That step
measures CPU behavior with a real hardware energy sensor, which **only exists on
the Linux box** (`rua@192.168.1.7`) — it can't be done on this Mac.

### The 15 problems and how thin they got
| problem | cases now |
|---|---|
| uncommon-words-from-two-sentences | 1 |
| check-if-word-equals-summation-of-two-words | 2 |
| number-of-ways-to-reorder-array-to-get-same-bst | 2 |
| stickers-to-spell-word | 2 |
| count-number-of-maximum-bitwise-or-subsets | 3 |
| longest-string-chain | 3 |
| minimum-number-of-work-sessions-to-finish-the-tasks | 3 |
| count-vowels-permutation | 4 |
| evaluate-the-bracket-pairs-of-a-string | 4 |
| find-all-k-distant-indices-in-an-array | 4 |
| minimum-number-of-days-to-eat-n-oranges | 4 |
| number-of-ways-where-square-of-number-is-equal-to-product-of-two-numbers | 4 |
| partition-to-k-equal-sum-subsets | 4 |
| pizza-with-3n-slices | 4 |
| verbal-arithmetic-puzzle | 4 |

### The decision (later, not today)
| Option | What happens | Effort |
|---|---|---|
| **A. Re-curate the 15 (needs box)** | Generate new valid inputs, re-measure their CPU fingerprints on the box, re-pick ~5 good cases each. Restores full coverage. | Box session; ~a few hours of runs |
| **B. Leave them thin** | Those 15 problems keep fewer cases. Everything still works, just slightly less representative for them. | none |
| **C. Quick top-up without the box** | Generate valid inputs and add them *without* fingerprint-based selection (less principled, but restores the count). | ~1 hr, this Mac |

**Recommendation:** A, next time the box is on. Not blocking anything today.

---

## 🔲 TASK 3 — Show the new info in the web UI (optional)

The database + API already store and expose each submission's **machine**,
**energy tool**, and **per-op** numbers, but the website pages don't display
them yet. Small frontend job whenever you want it surfaced in the UI.
