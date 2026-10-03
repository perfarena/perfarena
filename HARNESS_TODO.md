# Harness problems to address

Running list of oracle/harness issues found while auditing CLBG submissions and
building the reference-solution smoke test. Newest context at the bottom of each
item. Status: OPEN / IN PROGRESS / FIXED (deploy pending) / VERIFY / DONE.

## number-of-ways data bug — FIXED LOCALLY (2026-10-02); prod left as-is per decision

**DONE (local data fix):** dropped the 5 out-of-constraint duplicate-input cases
(18/23/44/53/56) from `reference/leetcode/outputs/<slug>.json` (expected),
`workloads/<slug>.json` (cases + the matching `test` assert lines), and recomputed
+ synced the hash-guarded `workload_hash` on both files (integrity check still
passes; diff is only the removed cases + hash). Re-validated: all six languages
(python3, c, kotlin, swift, scala, golang) now PASS 69/69.
**Prod: untouched** — the recorded submissions were verified unaffected (see
below), so no re-score. **Remaining (optional, durability):** add a
distinct-values constraint to the curation in `perfarena/leetcode_energy.py` so a
future regeneration doesn't reintroduce the 5 cases (generalize to the ~15-20
known out-of-constraint problems). Deploy the two edited reference files to bicho
with the submodule when convenient.

**`number-of-ways-to-reorder-array-to-get-same-bst`: 5 test cases have
out-of-constraint DUPLICATE-value inputs (case 18/23/44/53/56), see item 38.**
CONFIRMED a data bug (not a solution bug): the problem constraints say "All
integers in nums are distinct"; the reference matches LeetCode's own published
examples ([2,1,3]→1, [3,4,5,1,2]→5, [1,2,3]→0). The stale golden would falsely
reject a correct solution on those 5 cases — but ONLY under the `arena-local`
oracle (the `leetcode` oracle bypasses our golden).

**VERIFIED prod impact (2026-10-02) = NONE so far.** Of 133 recorded
`number-of-ways` attempts, acceptance is 107/133 = 80.5%. 127 used the `leetcode`
oracle (unaffected). The 6 `arena-local` ones are all `gemma4:e4b` and every one
failed GENUINELY — on case_1 (a valid distinct input, expected 21964799) or a
runtime crash — none reached the out-of-constraint cases (18/23/44/53/56). So no
recorded verdict is corrupted and NO re-score is needed today. This is a LATENT
bug: it would false-reject a *correct* `arena-local`-judged solution, so still
fix it for future arena-local runs + data hygiene (downgraded from results-urgent).
TODO: (1) drop the 5 out-of-constraint cases from
`reference/leetcode/outputs/<slug>.json` (correctness oracle) AND regenerate the
hash-guarded `workloads/<slug>.json`; (2) add a distinct-values constraint to the
curation in `perfarena/leetcode_energy.py` (generalize to the ~15-20 known
out-of-constraint problems); (3) redeploy the submodule; (4) re-validate affected
submissions to correct the leaderboard/paper. Live LeetCode confirm (Accept +
runtime/memory) pending creds.

## Energy metric (HIGH PRIORITY)

0. **Reported energy = RAPL package + codecarbon RAM ESTIMATE (~40%) — OPEN.**
   Both measure paths (`codecarbon_runner.py` and the handler's `_CodecarbonEnergy`)
   use codecarbon's total `data.energy_consumed` = `cpu_energy + ram_energy + gpu_energy`.
   On bicho (Intel i7-7600U, codecarbon 3.3.0), over a 2s CPU-busy window:
   - `cpu_energy` = RAPL package, real (~13.9 W, matches RAPL package 14.8 W).
   - `ram_energy` = codecarbon ESTIMATE ~9.3 W, which is ~23x the real RAPL DRAM
     domain (0.41 W). It is not measured.
   - `gpu_energy` = 0 (no discrete GPU).
   So ~40% of every reported energy value is a heuristic RAM estimate that scales
   roughly as constant x time, not real memory energy.
   Impact:
   - Energy numbers are inflated ~40% and are not clean RAPL measurements.
   - The estimate being proportional to time likely INFLATES the "energy tracks
     runtime" / "energy adds no separate signal" finding (RQ1) - part of the
     energy-runtime correlation is an artifact of the RAM estimate. Re-check RQ1
     energy claims after the fix.
   Fix: report `cpu_energy` (RAPL package, measured), optionally plus the real
   RAPL DRAM domain (`intel-rapl:0:2`, tiny here); do NOT use codecarbon's total.
   This matches the direct `_RaplEnergy` backend (package-only, already skips
   dram), threats I1 ("whole processor package"), and Pereira et al.'s RAPL
   method. Requires re-measuring existing energy data (values change ~40%).
   Also: the two backends disagree today - `_RaplEnergy` = package only,
   codecarbon = package + estimated RAM - so switching energy tools silently
   changes the metric. Make them consistent.
   Paper: state precisely that energy = RAPL CPU package on a bare-metal Intel
   host (once the fix lands), and drop any wording implying more.

## CLBG oracle

1. **TypeScript: Node types missing for model code — FIXED.**
   Models write canonical parallel CLBG code (`import { Worker } from 'worker_threads'`,
   `process`, `Buffer`), but the oracle's `tsc` failed: `TS2307: Cannot find
   module 'worker_threads'` and strict type errors (`TS2451` redeclare `process`,
   `TS2345`, `TS2769`). Root cause: bicho's `@types/node` was 7.0.13 (Node 7,
   pre-worker_threads). Fix: added `--noCheck` (tsc 5.6+, bicho has 5.9.3) to the
   TS `COMPILE_CMD` in all 10 `TypeScript/clbg/*/Makefile`, so the compile
   transpiles without type-gating (correctness is judged by the oracle, not tsc).
   Also upgraded bicho's `@types/node` to 20.19.43 (harmless bonus; `--noCheck`
   makes the version irrelevant). Verified on bicho: model worker_threads code
   transpiles, and all 10 TS reference cells compile+validate (fasta included).
   `--noCheck` is the committed durable fix (works on a fresh checkout).
   Follow-up: LeetCode TS uses a separate `harness_ts.js` compile; check whether
   it also needs transpile-only for strict model type errors.
   REGRESSION + fix (later): the runner hit `TS5023: Unknown compiler option
   '--noCheck'` because its environment exports an old `TSC` (<5.6), and the
   cell Makefile used `TSC ?= npx typescript@5.9.3` so the stale env value won.
   Fixed by forcing the modern pin: `override TSC := npx --yes -p
   typescript@5.9.3 tsc` in all 10 TS cell Makefiles (keeps `--noCheck`).
   Verified locally that it compiles even with a stale `TSC` env. Needs to reach
   bicho (git pull of the public EL, or direct deploy when reachable).

9. **CLBG dispatch timeouts — FIXED.**
   Backend gave up on a measure at the harness `config.timeout_s` (900s) while
   the runner watchdog is 1280s, so slow CLBG measures (fasta 12M, n-body 15M)
   returned "Dispatch timeout" even though the runner was still working. Raised
   arena-linux-local `timeout_s` 900 -> 1800 (> the runner watchdog) via the
   admin API; live in prod. On the next runner relaunch, bump `--request-timeout`
   above 1280 too if measures legitimately need longer than the watchdog.

2. **C# mandelbrot reference produced wrong output — FIXED.**
   `getCanonicalByte` (the N<=200 path used by validate) started z at c and ran
   7x7=49 iterations with a coarse `|z|>=2` check every 7, instead of the
   canonical CLBG algorithm (z from 0, 50 iterations, `|z|^2<=4`). Rewrote it to
   the canonical form. Verified validate PASS at N=200 locally and on bicho; the
   fixed `Program.cs` is deployed to bicho and the test cell regenerated.
   Follow-up (minor): the `getByte` fast path (N>200, measurement only, not
   validated) should be checked for the same output, for consistency.

3. **Python pidigits needs `gmpy2` — VERIFIED present + recorded.**
   The reference does `from gmpy2 import xmpz, t_div, mul, add`. `gmpy2` 2.3.1 is
   already installed on bicho (system python3 and the perfrunner venv), and all
   10 Python reference cells validate on bicho. The local-only failure was my Mac
   lacking gmpy2. Recorded the dependency in
   `Energy-Languages/perfarena/requirements-oracle.txt` so a fresh oracle install
   does not regress.

4. **C CLBG cells not integrated — FIXED.**
   Converted all 10 `C/clbg/<problem>/` cells from the raw upstream Makefiles to
   the perfarena harness (`selection/gen_reference_tests.py` was not used here; a
   one-off converter mirrored each problem's ARG/VALIDATION_N/golden/STDIN from
   the C++ cell and took the gcc compile line from the raw C Makefile; the raw
   Makefile is kept as `Makefile.upstream`). On bicho, 7/10 compiled+validated
   immediately; the other 3 needed system libraries the upstream C variants use:
   `libapr1-dev` (binary-trees), `libpcre3-dev` (regex-redux), `libhts-dev`
   (k-nucleotide). Installed those on bicho, after which all 10 C references
   compile+validate. Recorded the libs in
   `Energy-Languages/perfarena/requirements-oracle.txt`. Also generated the C
   reference test cells (`C/clbg/test/<problem>/`).

5. **binary-trees runs too fast — WON'T FIX (Python-budget-locked).**
   Host-calibrated on bicho: C++ reference at ARG=18 = 1.6s (above the 1s floor),
   but a well-optimized model solution (gpt-5.6-sol) runs it in ~191ms and Python
   runs it in ~54s/run (x8 ~= 400s, near the 1200s measure ceiling). Bumping to
   ARG=20 would push Python to ~250s/run -> ~2000s -> TLE. So ARG=18 is the max
   the slowest measured language tolerates; the fast C++ models near the energy
   floor is unavoidable language spread. Keep ARG=18; document as a threat.

20. **stdin problems (revcomp/knucleotide/regex) too fast + validate coupled to a
    tiny input — FIXED (host-calibrated + validate decoupled), deploy pending.**
    All three read `fasta-10000` (~100 KB) for BOTH measure and validate, so C++
    finished in ~33 ms (far below the 1s floor) and a big measure input would also
    bloat validate (254 MB reference). Host-calibrated on bicho to C++ ~1s and
    DECOUPLED validate from measure:
    - MEASURE (`STDIN_FILE` in the cell Makefiles): reverse-complement fasta
      25M (C++ 1.2s / Python ~20s), k-nucleotide fasta 2.5M (C++ 1.3s),
      regex-redux fasta 500K (C++ 1.55s / Python 1.18s — sized so even the
      fastest measured language, Python's `re`, clears the 1s floor; C++'s
      std::regex is the slow one here). Big inputs host-generated by
      `gen-input.sh` and gitignored.
    - VALIDATE: small committed `fasta-1000..10000` via
      `reference/clbg/outputs/<problem>/cases.txt` (5 golden cases each, from the
      TRUSTED C++ reference — the Python k-nucleotide ref is broken). perfarena.mk
      uses cases.txt when present, so validate ignores the big `STDIN_FILE`.
    Also: `run:` now pipes `STDIN_FILE` (was raw `RUN_CMD`, the memory-pass hang,
    item 19); `gen-input.sh` rewritten for the calibrated sizes; `problems.yaml`
    `stdin_input_file` updated; `clbg_gen_refs.py` stdin golden switched to C++.
    Verified on bicho: all 3 validate 5/5 on small cases; C++ measure times ~1s.
    Deploy: commit EL (Makefiles, cases.txt+golden, gen-input.sh, .gitignore,
    perfarena.mk, clbg_gen_refs.py) + problems.yaml; on bicho `git pull` then run
    `./gen-input.sh` to (re)create the big inputs; reseed prod for the yaml.

6. **CLBG PHP `<?php` stripping — FIXED, deploy pending restart.**
   The client stripped a leading `<?php` (correct for the LeetCode wrapped runner,
   wrong for CLBG standalone), so every PHP CLBG program printed its source as
   text and got Wrong Answer. Fixed CLBG-aware in `perfarena_handler.py`
   (`_normalize_solution_code`) and `submit_with_local_model.py`
   (`_normalize_code_for_lc`). Handler deployed to bicho; needs the runner
   restart to load. See memory `clbg-php-open-tag-bug`.

15. **k-nucleotide: stdin/statement mismatch — OPEN (attempts purged).**
    0/14 accepted across every model and language (gpt-4.1-mini, deepseek, grok,
    gpt-5.5, fable...). The statement says stdin is "the THREE sequence emitted by
    the fasta benchmark" / "the THREE section", but the harness pipes the FULL
    fasta (>ONE, >TWO, >THREE + headers; the fed fasta-10000.txt carries ~280-340
    IUB codes B/D/H/K/M/N/R/S/V/W from >TWO). Every model reads all of stdin and
    counts the >ONE/>TWO/header garbage, so nobody matches the THREE-only
    reference (A 30.284 ...). The reference output itself is correct. Fix (pick
    one): pipe only the extracted >THREE section to k-nucleotide's stdin (matches
    the statement + reference), or change the statement to require extracting the
    >THREE section from the full fasta. All 17 k-nucleotide attempts purged from
    prod so re-runs start clean.

16. **reverse-complement: statement lacked the IUPAC complement table — FIXED
    (yaml), prod reseed pending; attempts purged.**
    1/12 accepted. The >ONE section (ACGT) matched for everyone; only the >TWO IUB
    section diverged, so basic A/C/G/T complement was fine and the failures were
    all in the ambiguity-code complement. Verified the reference is CORRECT (the
    canonical reverse-complement of fasta-10000.txt is byte-identical to
    reference/outputs/reverse-complement.txt), so this is NOT a harness bug: the
    statement only said "IUPAC ambiguity codes handled symmetrically" without the
    table. Added the explicit table to problems.yaml (A<->T, C<->G, U->A, R<->Y,
    K<->M, B<->V, D<->H; S, W, N map to themselves). Needs a prod reseed
    (docker cp problems.yaml + `python -m app.seed_clbg_competition`) to go live.
    All 13 reverse-complement attempts purged from prod.

17. **C++ k-nucleotide reference missing `<cstring>` — FIXED, deploy pending.**
    `knucleotide.gpp-3.c++` calls `strlen` but only included `<cstdio>`/`<cctype>`;
    modern g++ no longer pulls `<cstring>` in transitively, so the reference failed
    to compile (`error: 'strlen' was not declared in this scope`). Added
    `#include <cstring>` to both the working cell
    (`C++/clbg/k-nucleotide/`) and the test cell (`C++/clbg/test/k-nucleotide/`).
    Verified with `g++ -std=c++17 -fsyntax-only`. Needs committing to EL and a
    bicho recompile of the reference.

## Measurement

7. **First-run measure cap — DONE, deploy pending restart.**
   The LeetCode measure loop now caps run 1 at `max(ceiling/n_runs, ceiling/3)`
   and marks all runs Time Limit Exceeded if it trips, instead of waiting out the
   full ceiling. `PERFARENA_FIRST_RUN_CAP_FRACTION=0` disables it. Handler
   deployed to bicho; needs the runner restart.

18. **codecarbon online geolocation hangs ~20 min per measure — FIXED, deploy
    pending.**
    Every measure stalled silently for ~20 minutes before the first run, then ran
    the 8 warmup+measure runs in ~29s and was marked Accepted (e.g., python3
    reverse-complement: 1239.5s total, all in the pre-run gap; php shows the same
    stall). Language-independent, so it is the tracker init, not the workload.
    Root cause: both measure paths built codecarbon's ONLINE `EmissionsTracker`,
    which does an IP-geolocation / carbon-intensity network lookup on first
    `.start()` (`save_to_api=False` disables only result upload, not the geo
    fetch). On bicho's restricted/flaky egress that call blocks for ~20 min before
    falling back. Fix: use `OfflineEmissionsTracker(country_iso_code=...)`, which
    skips the network entirely — we report measured energy (energy_consumed),
    never CO2, so the country is an unused coefficient. Changed
    `Energy-Languages/perfarena/runners/codecarbon_runner.py` (CLBG whole-program,
    all languages; idle-baseline + per-run trackers) and the handler's
    `_get_tracker_cls` + `_new_tracker` (`perfarena_handler.py`, LeetCode per-op).
    Overridable via `PERFARENA_COUNTRY_ISO` (default `USA`). Syntax-checked; NOT
    smoke-tested (codecarbon not importable locally) — verify one measure on bicho
    has no 20-min gap. Deploy: codecarbon_runner.py is a subprocess so it is live
    on bicho once the file lands (git pull EL / scp), no runner restart; the
    handler change needs a runner restart (imported at startup). Commit
    codecarbon_runner.py to EL and perfarena_handler.py to the main repo.

19. **stdin-input problems hung ~20 min in the MEMORY pass (`make run` had no
    stdin redirect) — FIXED, deploy pending.** This is the real cause of the
    ~20-minute silent gap before measurement (not codecarbon — item 18 is a
    separate, defensive fix). Before `make measure`, the handler runs
    `_measure_mem` -> `make run` once for peak RSS, with a timeout equal to the
    full CLBG ceiling (~1200s) and no progress log. The `run:` target used raw
    `$(RUN_CMD)`, while `measure`/`validate`/`mem` use `$(_FULL_RUN_CMD)`, which
    appends `< $(STDIN_FILE)`. So for the three stdin problems
    (reverse-complement, k-nucleotide, regex-redux) `make run` executed the
    program with no input, it blocked on stdin, hit the ~1200s timeout, and
    returned memory_mb=None; only then did the energy measure run (fast, with the
    redirect). Fix: `run:` now uses `$(_FULL_RUN_CMD)` (identical to arg problems;
    adds the redirect for stdin ones). Also added a timed "measuring peak memory
    (make run)..." / "peak memory = ... in Xs" log around `_measure_mem`. Deploy:
    perfarena.mk is read fresh per `make`, so live on bicho once the file lands
    (git pull EL / scp), no restart; the handler log needs a runner restart.
    Commit mk to EL, handler to main.

## Reference-solution smoke test (new infrastructure)

8. **Reference smoke test — IN PROGRESS.**
   `selection/gen_reference_tests.py` isolates each reference solution into
   `<Lang>/clbg/test/<problem>/`; `selection/run_reference_tests.sh` runs
   `make clean compile validate` per cell on the oracle and reports PASS/FAIL.
   60 cells generated (C++, C#, Java, PHP, Python, TypeScript x 10 problems).
   Local baseline: C++ 10/10, Java 10/10, PHP 10/10, TypeScript 10/10, C# 9/10
   (mandelbrot, item 2), Python 9/10 (pidigits, item 3).
   TODO:
   - Make the test runs fast with small inputs + self-contained small goldens
     (do not depend on the measurement-sized goldens).
   - Run on bicho for authoritative results (real gcc/dotnet/@types/node/gmpy2).
   - Extend to more CLBG languages (Go, Rust, JavaScript, Ruby) if wanted.

## Tooling / observability

10. **TS compiler version hardcoded in every cell Makefile — FIXED.**
    The `typescript@5.9.3` pin was duplicated in 20 TS Makefiles. Moved it to a
    single config `Energy-Languages/perfarena/toolchain-versions.mk`
    (`TS_VERSION ?= 5.9.3`), which `perfarena.mk` includes and uses to force
    `override TSC := npx --yes -p typescript@$(TS_VERSION) tsc`. Cell Makefiles
    no longer set TSC. Version is overridable via `TS_VERSION` (env/CLI/config).

11. **Show expected-vs-produced diff for CLBG validation failures — FIXED
    (all clbg problems + side-by-side SPA panel).**
    `selection/clbg_validate.sh` now captures, on the first failing case, a
    truncated diff (unified diff for text, hex head for binary, stderr for
    crashes) and emits it after the FAIL line. The handler's `_fail_dict` already
    funnels validate stderr into `raw_stderr`/`full_runtime_error`, which
    `_build_error_log` surfaces (both are in `_ERROR_FIELDS`), so the SPA's
    existing error panel shows it with no backend/frontend change. Verified
    locally (spectral-norm: expected `1.271844019` vs produced `-1.5`).
    Now covers ALL clbg problems: `perfarena.mk`'s single-file validate path also
    emits a diff (unified for text, hex head for binary/mandelbrot).
    Side-by-side SPA panel DONE: `clbg_validate.sh` and `perfarena.mk` emit
    base64 `PERFARENA-DIFF-{CASE,EXPECTED,PRODUCED}` markers (text problems); the
    handler's `_parse_clbg_diff` decodes them into
    `expected_output`/`code_output`/`last_testcase` and strips the raw markers
    from the stderr box; `AuditPanels.tsx` renders a side-by-side `DiffBox` with
    per-line highlight when both are present (also benefits LeetCode Wrong
    Answers). Handler compiles, frontend typechecks, parse verified end-to-end.
    Binary (mandelbrot) emits no markers and keeps the hex diff in the error
    panel. Deploy pending: bicho (harness/handler) + frontend rebuild.

## Lower priority

12. **C/pidigits reference formats partial rows differently — FIXED.**
    The C pidigits variant printed `\t:<count>` only after each full 10-digit row
    and never flushed the final partial row, so at the validation Ns (8/16/28) it
    emitted `31415926` where the golden has `31415926  \t:8`. Added the standard
    partial-row flush after the loop
    (`if (i % 10) printf("%*s\t:%u\n", 10 - i % 10, "", i);`). Deployed to bicho;
    C/pidigits now validates 3/3 (N=8 -> `31415926  \t:8`), so the reference smoke
    test is 70/70. Needs committing to EL.

13. **Reference test cells used the wrong validate path (depth) — FIXED.**
    `perfarena.mk` hardcoded `../../../reference` / `../../../selection`, correct
    only for depth-3 working cells; the depth-4 `test/` cells fell through to the
    single-file golden instead of the multi-case `clbg_validate.sh`. Added
    `_EL_ROOT := $(dir $(lastword $(MAKEFILE_LIST)))` and used it for the cases.txt
    check, the `clbg_validate.sh` call, and the toolchain include, so the paths are
    depth-independent. Deployed to bicho; needs committing to EL.

## Tooling / observability (cont.)

14. **clbg_validate.sh had no per-case timeout — FIXED.**
    A hanging or pathologically slow model solution (seen on gpt-4.1-mini
    fannkuch-redux in TypeScript and PHP, subs 361-366) stalled the whole
    `make validate` until the handler's 120s watchdog, surfacing as an opaque
    `Runtime Error: handler raised: TimeoutExpired`. Added a per-case
    `timeout "$CASE_TO"` wrap (default 20s, `PERFARENA_VALIDATE_CASE_TIMEOUT_S`),
    with a clean "case N timed out after Ns (hanging or too slow)" message on
    rc=124. Deployed to bicho and verified (killed at the cap, no 120s hang).
    Live immediately (validate shells out per call). Needs committing to EL.
    Follow-up: map a validate timeout to a "Time Limit Exceeded" verdict rather
    than the generic "Wrong Answer".

## CLBG validator strictness — false-negatives (prod sweep 2026-09-29)

Swept the 40 most recent prod `clbg-10` submissions and auto-classified every
failed attempt. The validator (`clbg_validate.sh` + `perfarena.mk`) compared with
exact `diff -q` / `cmp -s` and no normalization. This rejected 15 attempts that are
computationally correct and differ only in output formatting. Every problem has
`cases.txt`, so all of these route through `clbg_validate.sh` and one fix point
covers them.

**FIX for 21-24 — IMPLEMENTED + verified, deploy pending.** In `clbg_validate.sh`,
whitespace normalization is now UNIVERSAL for every text problem (applied whether
or not a `normalize` file exists): `_apply_norm` collapses runs of blanks to one
space, trims each line end (which also neutralizes internal padding like pidigits'
last row), drops blank lines (CLBG uses them only as section separators, never as
data), and canonicalizes the end-of-file newline (`awk '{print}'`, so a
missing/extra trailing newline compares equal). Non-blank lines and their order are
KEPT, so line order and per-line field structure are still compared. (The blank-line
drop was added after submission 378 k-nucleotide was found correct in every
frequency and k-mer count but failing only because the model omitted one blank-line
separator before the count block; the earlier k-nucleotide "REAL-diff" count
therefore mixed genuine value errors with this false-negative.) A per-problem `normalize` file
adds only the transforms that are NOT universally safe: `casefold`
(reverse-complement outputs uppercase) and `sort-lines` (binary-trees prints the
same self-labeled lines in a different order). The comparison order is: binary
`cmp` (mandelbrot) -> numeric `tolerance` (n-body, spectral-norm) -> normalized
text compare (everything else). The failure diff still captures the ORIGINAL bytes
so a real difference is never hidden. Verified with 16 cases: exact matches still
pass (no regression on any text problem), whitespace/pad/case/order false-negatives
accepted, and every genuine wrong answer (wrong digit/value/base/checksum/count)
still rejected — including that reordered fasta and reverse-complement (ordered,
multi-line) still FAIL because `sort-lines` is not applied to them. Files:
`reference/clbg/outputs/reverse-complement/normalize` = `casefold`,
`.../binary-trees/normalize` = `sort-lines` (the earlier `pidigits` and
`regex-redux` files were removed — the universal whitespace path covers them).
Deploy: git pull on bicho (script + 2 normalize files); no restart, no reseed.

**Ordering / parallelism (why `sort-lines` is NOT universal).** A parallel solution
can emit independent lines out of order, but making the compare order-insensitive
everywhere would ACCEPT scrambled output where order is part of the answer (proved:
a reversed fasta / reverse-complement must fail, and it does). So `sort-lines` stays
per-problem. Only binary-trees qualifies today: its depth-loop lines are independent
and self-labeled, so a parallel `for` that prints them out of order is still correct.
Every other parallel CLBG benchmark has a SPEC-MANDATED output order that a correct
parallel implementation must reproduce (k-nucleotide: frequency desc then k-mer asc,
plus the fixed specific-k-mer block; regex-redux: the fixed pattern list then three
positional lengths; mandelbrot: binary image rows), so relaxing them would risk
false positives. k-nucleotide equal-count tie-breaking is a theoretical edge case;
if it ever surfaces it needs a section-aware sort, not the global `sort-lines`.

21. **reverse-complement: 9 case-only rejections — FIXED (casefold).**
    The model emits the reverse complement in lowercase; the golden is uppercase.
    All letters match. Affected `gpt-5.6-luna` in all 6 languages (C++, C#, Java,
    PHP, Python, TS), `gpt-5.6-sol`/PHP, and `gpt-4.1-mini` in C++ and C#. The CLBG
    reference output is uppercase, so case carries no meaning here. (`gpt-4.1-mini`
    Java and PHP are genuine wrong answers, not case-only, and still fail.)

22. **pidigits: 3 padding-only rejections — FIXED (collapse-ws).**
    The model prints `31415926\t:8`; the golden is `31415926  \t:8` (the last
    partial row is space-padded to 10 columns). The digits and the count are
    correct. This is the same pad that item 12 ADDED to the golden, so the golden
    is right and the model omits it. Collapsing whitespace runs makes these pass
    without accepting wrong digits.

23. **regex-redux: trailing-whitespace + missing-final-newline rejections — FIXED
    (trim-trailing-ws + EOF-newline normalization).**
    `gpt-5.6-luna` PHP and TS differed from the golden only by trailing whitespace
    on some lines. A later look at `gpt-5.6-luna`/python3 (submission 378) showed a
    second sub-case: the output was byte-identical (counts 10245/10000/5262 all
    correct) but omitted the FINAL newline. `sed`-based `trim-trailing-ws` works
    line-by-line and does not touch the end-of-file newline, so `_apply_norm` now
    ends with an always-on `awk '{print}'` stage that canonicalizes the EOF newline
    (a missing or extra trailing newline compares equal). Verified: 10/10 unit
    cases, including missing-final-newline accepted and wrong counts still rejected.
    NOTE: blanket "remove all newlines and spaces" was considered and rejected — it
    would break the binary-trees order check (needs line structure) and could accept
    wrong answers where whitespace separates semantic fields (regex-redux counts,
    pidigits `\t:count`).

24. **binary-trees: 1 line-order rejection — FIXED (sort-lines).**
    `gpt-5.6-luna` C++ prints the `stretch tree of depth 7   check: 255` summary
    line AFTER the depth loop instead of before it. All four check values match,
    and each line is self-labeled, so an order-insensitive compare is safe here.
    (`gpt-4.1-mini` C++ is a genuine wrong answer: `check: 127` not `255`, still
    fails.)

25. **No `tolerance` files deployed → float last-digit diffs may be
    false-negatives — CHECK.**
    `clbg_validate.sh` supports a per-problem `tolerance` file (numeric compare via
    awk) for summation-order rounding, but no problem has one, so n-body and
    spectral-norm use exact diff. The sampled failures were genuinely wrong (not
    last-digit), but the 8 n-body and 5 spectral-norm real-diffs should be
    re-checked for last-digit rounding; add a small tolerance if it appears.

26. **TypeScript compile failures surface only `make Error 2` — NOT a code bug;
    deploy pending.** k-nucleotide TS compile failed for `gpt-5.6-sol` and
    `gpt-5.4-mini` and the deployed handler stored only
    `make: *** [perfarena.mk:128: compile] Error 2`. But `tsc` writes its
    diagnostics to STDOUT, and the CURRENT handler already handles this:
    `_first_error_line` searches BOTH streams and skips the `make:` wrapper, and
    `_fail_dict` stores `both_tail` (stdout+stderr) in `full_compile_error`. The
    live data proves the runner already captures it — `raw_stdout` held
    `knucleotide.ts(6,31): error TS2345: ...` while `compile_error` showed the make
    line. So the local/bicho container runs an OLDER handler; deploying the current
    `perfarena_handler.py` surfaces the real `tsc` error. Verified with the real
    captured streams (summary => the TS2345 line). No code change needed.

27. **Validate `TypeError` is the MODEL's code, not the validator — reclassified.**
    `gpt-5.6-luna`/Python mandelbrot: the traceback is in
    `Python/clbg/mandelbrot/mandelbrot.py` line 68 (`out.write(row)` with
    `row=None`) — the model's program crashed and exited 1, and the harness
    reported it correctly (`validate: FAIL (program exited 1)` + full traceback).
    Not a harness bug. The real defect was the VERDICT: the CLBG path collapsed
    every non-zero validate to "Wrong Answer", so a crash looked like a wrong
    answer. FIXED: added `_make_validate_status(rc, stderr)` and used it at all four
    `make validate` classification sites — both CLBG sites and both LeetCode rc-2
    legacy-fallback sites, in `validate_once` and the `/measure` path. It reads the
    cause the validator already prints — `program exited` / `crashed rc=` ->
    "Runtime Error", `timed out after` -> "Time Limit Exceeded" (this also closes
    the item-14 follow-up) — and keeps "Wrong Answer" only for a real output
    mismatch. pass@1 is unchanged (all non-accepted); only the label is now
    accurate. The LeetCode fallback only fires on rc 2 (full-suite validator can't
    run) and reclassifying a crash there does not change pass/fail, so leetcode-99
    numbers are unaffected. Handler compiles; classifier verified on 6 real stderr
    strings. Deploy pending: bicho handler.

28. **mandelbrot never records peak memory — FIXED, deploy pending.**
    Accepted mandelbrot attempts had `avg_memory_mb` null while runtime and energy
    were present (19 of 20 accepted-no-memory attempts on prod were mandelbrot).
    Cause: `_MEM_HELPER` ran `make run` with `capture_output=True, text=True`, which
    decodes the program's stdout as UTF-8. Mandelbrot is the only CLBG benchmark
    that writes a BINARY PBM image to stdout, so the decode raised
    `UnicodeDecodeError`; only `TimeoutExpired` was caught, so the helper crashed
    and `_measure_mem` returned `memory_mb = None`. FIXED: the mem helper now uses
    `stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors='replace'`.
    The helper only reads `returncode` and `stderr`, and the mem pass only needs
    peak RSS from `getrusage`, so discarding stdout is safe and does not change the
    child's RSS. Reproduced the crash and verified the fix. Deploy pending: bicho
    handler. After deploy, re-run mandelbrot cells to backfill memory.

29. **pidigits validation cases exercise a code path the measurement does not —
    OPEN (policy).** The three pidigits validation Ns are 8, 16, 28, all with
    N%10 != 0, so each requires the final partial-row flush (`printf("%*s\t:%u\n",
    10 - i%10, "", i)`). The measurement N is 10000, a multiple of 10, so it never
    prints a partial row. A solution that prints only full 10-digit rows and skips
    the partial flush therefore FAILS validation (e.g. `gpt-5.6-luna/cpp` produced
    empty output at N=8) but would run correctly at N=10000. This is the same bug
    the C reference had (item 12), so validation is catching a real spec violation,
    but validation and measurement no longer test the same paths. Options: leave as
    a strict-spec rejection; or change the measurement N to leave a partial row
    (10000 -> 10004) so validation faithfully predicts measurement correctness. No
    code changed yet — policy call. Found during the 2026-09-30 audit.

## Audit 2026-09-30 (latest clbg-10 submissions)

Swept the 45 latest clbg-10 submissions through the current validator logic
(per-problem normalization + numeric tolerance). No NEW false-negative classes:
normalization covers whitespace, blank lines, case, order, trailing newline, and
float tolerance. Remaining failures are genuine (n-body/spectral-norm value errors,
regex-redux `5262` vs `5305`, deepseek/TS compile, mandelbrot wrong images) or
legitimate TLEs (`gpt-4.1-mini` spectral-norm all langs, grok n-body). The one
observation worth acting on is item 29. Also confirmed: a PHP parse-error crash was
labelled `Runtime Error` (not `Wrong Answer`), so the crash-reclassification is live
in prod.

30. **TypeScript solutions wrongly prefailed as `wrong_language_extracted` — FIXED
    (client) + display FIXED (backend).** Many TS clbg attempts across top models
    (opus-4.8, gpt-5.5, gpt-5.6-luna, gpt-6-astra, deepseek) were flagged
    `wrong_language_extracted` by the client's pre-submit check and so were NEVER
    measured: the server records a prefailed attempt as one synthetic failure run
    and skips dispatch (`dispatch.py:1606`, reached only when `prefailed_reason` is
    set; the bicho branch is the `elif` after it). The `wall_clock_ms` on these
    attempts (6-325s) is GENERATION time, not measurement time. (An earlier note
    here wrongly said they were measured on bicho.) They were shown as "queued".
    Root cause: `_looks_like`'s TypeScript
    fingerprints (`submit_with_local_model.py`) required TS-only syntax
    (`: number`, `interface`, typed function). TypeScript is a SUPERSET of
    JavaScript, so a valid TS solution can carry zero TS-specific syntax — common
    in terse CLBG numeric code — and was rejected. FIX: added the JavaScript shapes
    (`function`/`const`/`let`/`var`/`=>`) to the TS fingerprint. Verified: JS-shaped
    TS passes, Python/Java-as-TS still rejected. Second bug: the `/attempts`
    endpoint showed any runless attempt as "queued", even when
    `prefailed_reason`/`final_status` was set. FIX (`main.py`): a runless attempt
    now reports its `prefailed_reason`, then `dispatch_warning`, then `final_status`,
    before falling back to "queued". Deploy: client file where the sweep runs;
    `main.py` to the prod + local backends. The 11 already-affected TS attempts
    were measured-then-discarded, so re-run them (with the fixed client) to record
    real results.

31. **A measured attempt persisted no Run rows — OPEN (1 case, watch).**
    `x-ai/grok-4.6`/python3 fannkuch-redux (sub 348) has `n_runs=7` (a full
    7-repeat measurement) and `prefailed_reason=None`, but zero Run rows, so it
    showed "queued". This is not the TS fingerprint bug — it is a genuine
    measured-but-unpersisted attempt (aggregate/`n_runs` written, Run rows absent),
    likely a re-dispatch or transaction edge. The item-30 display fix now surfaces
    its `final_status` if one was set; otherwise it stays "queued" and needs a
    re-run. Only one case seen so far — watch for more after the next sweep.

32. **Client re-dispatch created duplicate attempts for slow CLBG measures —
    FIXED (idempotent retry), deploy to sweep host.** The sweep client prefetches
    generation ahead but submits/polls serially. A CLBG measure can outlast
    `--poll-seconds`; on `poll_timeout` the client moved on, bicho kept measuring
    and recorded the attempt Accepted late, and the next run re-submitted the tuple
    -> a DUPLICATE attempt (documented at matrix_sweep.py:1615-1625; the 900->1800s
    bump only raised the threshold). Compounded with the TS fingerprint bug: the
    duplicate re-run was often prefailed `wrong_language_extracted` and shown queued
    while the measured-Accepted original was orphaned (e.g. astra fannkuch measured
    on bicho as att=17816, current DB attempt is the prefailed att=18189). FIX
    (`matrix_sweep.py`): added `_find_inflight_attempt(base, key, sub, slug, round)`
    and a guard before the submit — if the server already has an in-flight attempt
    for (submission, problem, round) (status queued/Pending, no `prefailed_reason`,
    no `dispatch_warning`), the client resumes polling it instead of submitting a
    duplicate; `poll_timeout` now records the in-flight attempt id. Prefailed /
    dispatch-warned / terminal attempts are NOT treated as in-flight, so those
    still get a fresh submit; a genuinely stuck attempt is freed when the server's
    stale-marker stamps `dispatch_warning`. Verified: 11 unit cases + real-API
    contract check (prod 388 prefailed pidigits, status=queued but
    prefailed_reason set, correctly returns None). Deploy: the client file wherever
    the sweep runs. Optional follow-up: a server-side guard rejecting a duplicate
    non-terminal attempt for the same (submission, problem, round).

33. **Language fingerprint `wrong_language_extracted` also misfired on python3 —
    FIXED (broaden) + audited all langs.** Same class as item 30 (TS): the client's
    `_looks_like` fingerprints are tuned for LeetCode method-shape code and reject
    script-style CLBG programs. Found live: gemini-3.8-flash python3 fasta
    (att 18518) prefailed `wrong_language_extracted`. Python is the worst case —
    whitespace-structured, no mandatory braces/`#include`/package line — so a valid
    CLBG program can be pure top-level script with no `def`/`class`/`from import`/
    `->`. FIX (`submit_with_local_model.py`): added python-specific script signals
    to the python3 fingerprint — `^print(`, `sys.argv/stdin/stdout`, `range(`, and
    bare stdlib imports (`import sys|math|...`). Kept specific so Java
    (`System.out.print(`, `import java.`), C (`printf(`), Rust still fail.
    **Audit of every supported language** (representative CLBG script run through
    `_looks_like`): the 6 campaign langs are all safe — C++ always has
    `#include`/`std::`, C# `using System`/`Console.`, Java `public class`/
    `System.out`, PHP `$var`, TS now accepts JS shapes (item 30), python3 fixed
    here. Non-campaign: c/js/go/kotlin/scala/rust all carry a mandatory token
    (`#include`, `const`/`let`, `package`, `fun`, `object`, `fn`/`use std::`);
    ruby is fine via `end$`/`puts`/`def`. Only residual edge: a swift program that
    is print-only with no `let`/`func`/`class` fails — implausible for CLBG and
    swift is not in the campaign; add `^print(` / `CommandLine.arguments` if swift
    is ever run. Verified: 12 + 10 unit cases. Deploy: client file where the sweep
    runs. One queued attempt (18518) is from this bug and can be purged to re-run.

## Model-side (not a harness bug, no action)

- C# reverse-complement `CS0136` (grok redeclared `i` in a nested scope) — invalid
  C# from the model, judged correctly as Compile Error.
- mandelbrot: 12 of 27 attempts accepted, so the per-language `.pbm` references
  work; the 13 failures are model-side (wrong image), not a reference bug.
- Genuine wrong answers confirmed by the sweep (harness correct): n-body wrong
  energy, spectral-norm wrong value, fannkuch-redux wrong checksum, fasta one-char
  PRNG divergence, binary-trees `gpt-4.1-mini` wrong checksum, reverse-complement
  `gpt-4.1-mini` Java/PHP wrong sequence.

## LeetCode reference suites for C / Kotlin / Swift / Scala (2026-10-02)

Filled every empty-body LeetCode reference `solution.<ext>` for the four
languages that had partial suites, by translating the committed Python reference
(falling back to C++ / Java / Go / JS where no Python ref exists), each validated
with `selection/run_validate.sh <lang>` (exit 0 = all reference cases pass).
Translation done test-suite-by-test-suite with parallel subagents; the stock
harness is the oracle. All stubs kept their exact signature/types/method name;
only bodies were filled.

34. **Stub detector must be body-aware, not byte-count — FIXED (process).**
    The first scope pass flagged "unfilled" cells by file size (`< 120 bytes`),
    which silently MISSED empty-body stubs whose comment header or long signature
    pushes the file past 120 B (e.g. `middle-of-the-linked-list` = 179 B but an
    empty body; `maximum-sum-of-two-non-overlapping-subarrays` = 121 B). That
    under-counted the real stub set in every language (Kotlin 14, Swift 13, C 22,
    Scala 91 were missed/under-counted). Correct oracle: a solution is unfilled iff
    it has an empty-bodied function — detect `\{\s*\}` after stripping comments.
    This reproduced the compiler's exact "missing return statement" set, so it is
    reliable. Anyone extending the suites should use the body-aware check, never a
    size threshold.

35. **Codegen harnesses crash on DESIGN problems with no C++ ref — FIXED (added 4
    C++ refs).** The Swift/Scala/C codegen harnesses derive the design class
    signature by reading `C++/leetcode/<slug>/solution.cpp` in their
    `_design_info()`. Four design slugs have NO C++ ref AND no class signature in
    the workload `starter_code`, so `run_validate.sh {swift,scala,c}` crashed with
    `FileNotFoundError` (a Python traceback, not a clean exit) on:
    `design-a-stack-with-increment-operation`, `finding-mk-average`,
    `random-pick-index`, `stream-of-characters`. (The Java/Kotlin harness is fine —
    it reflects on the compiled solution, no C++ needed.) FIX: wrote the 4 missing
    `C++/leetcode/<slug>/solution.cpp` (class names equal `ops[0]`: CustomStack,
    MKAverage, Solution, StreamChecker), each validated via `run_validate.sh cpp`
    (48/49/54/53 cases). This unblocked C++ itself AND stock Swift/Scala design
    validation (verified: the 3 Swift design slugs now pass via the stock wrapper,
    49/54/53). Alternative fix not taken: give `_design_info` a starter-code
    fallback like `harness_cpp._cpp_stub_from_starter` — but the design
    `starter_code` carries no class signature, so adding the C++ refs was the only
    viable path and also fills a real C++ coverage gap.

36. **Codegen node-injection fooled by doc-comments — OPEN (worked around
    per-cell).** `harness_swift.py` (~L531), `harness_scala.py` (L557-558) and
    `harness_c.py` (~L497) decide whether to inject `TreeNode`/`ListNode` by a
    substring test (`"class TreeNode" not in sol`). A stub whose doc-comment
    literally contains `class ListNode { ... }` (the standard LeetCode starter
    comment) makes the test think the solution defines the node type, so injection
    is skipped and the cell fails to compile ("cannot find type"). Hit on
    `linked-list-components` and `minimum-depth-of-binary-tree` (Swift + Scala).
    Worked around by deleting the comment block in those `solution.*` files. Proper
    fix: strip comments before the substring check (or match a real declaration,
    not the string), in all three codegen harnesses.

37. **`check-if-word-equals-summation-of-two-words` test data exceeds the problem
    constraint — NOTE (handled in-solution).** The reference cases include 20-char
    words; LeetCode caps word length at ~8. A naive base-10 translation overflows
    64-bit ints and traps (Swift SIGTRAP). Solutions use digit-array / BigInt
    addition to stay correct. Same out-of-constraint synthetic-data class as the
    earlier sweep finding; the data could be regenerated within constraints.

38. **`number-of-ways-to-reorder-array-to-get-same-bst` — out-of-constraint
    DUPLICATE-VALUE inputs on 5 cases (root cause found).** case_18 golden =
    677042095, but C, Kotlin, Swift, Scala AND the committed Python reference all
    produce 591438322. Root cause (found while fixing the Go cell): case_18's input
    array contains a DUPLICATE value (two 25s), which violates the problem's
    distinct-values constraint. The Python reference partitions with `right = [x
    for x in arr[1:] if x > root]`, silently dropping values equal to the root; a
    solution that keeps equals (`>=`) counts an extra element and diverges. Exactly
    5 cases carry such duplicates: 18, 23, 44, 53, 56 — the same 5 where the golden
    disagrees with the Python reference. So the golden was generated by a variant
    that handles the out-of-constraint duplicates differently, not by a wrong
    algorithm. Fix: regenerate the golden from the constraint-respecting reference,
    or drop the 5 out-of-constraint cases from the test data. Every language's
    solution now matches the Python reference on all 74 cases (the Go cell was
    corrected from `>=` to `> root` to match); each still reports FAIL on case_18
    against the stale golden and is marked DATA-BUG for this slug.

39. **Local `gcc`/`libomp` brew install for C clbg failed — OPEN (C clbg only).**
    The one-off `gcc libomp` brew install exited 1 on this macOS host. Affects
    ONLY C clbg whole-program local validation (needs GNU gcc for
    `-fopenmp`/`-march=native`); LeetCode C uses clang and is unaffected. C clbg
    already validates on bicho. Retry the brew build or run C clbg on bicho.
    (NOTE 2026-10-03: the standalone `scripts/install_langs.sh` was merged into
    the repo-root `setup-harness-host.sh` and removed; the harness installer now
    adds the C CLBG libs — `libapr1-dev libaprutil1-dev libhts-dev libpcre3-dev`
    on apt, equivalents on dnf/brew — and installs `libomp` via brew on macOS.)

## CLBG reference suites for Scala and Kotlin, plus Go fixes (2026-10-02)

The original Energy-Languages repo carries NEITHER Scala NOR Kotlin, and the
current benchmarksgame site has retired Scala, so neither CLBG language could be
sourced upstream. Per the user's decision: Scala is translated from the repo's
own C++/Java reference, and Kotlin wraps (ports) each Java solution. Validated
locally with `make validate` (the CLBG oracle); a from-scratch clean+compile+
validate confirm sweep is the authoritative check.

40. **Scala + Kotlin CLBG cells added (10 each).** Cells at `Scala/clbg/<p>/`
    (`<prog>.scala` + Makefile) and `Kotlin/clbg/<p>/` (`<prog>.kt` + Makefile),
    for all 10 problems. Makefile patterns, verified against `perfarena.mk`:
    - Kotlin: `COMPILE_CMD = kotlinc $(SOURCE) -include-runtime -d $(OUTPUT)` (fat
      jar), `RUN_CMD = java -jar $(OUTPUT) $(ARG)`.
    - Scala: `COMPILE_CMD = scalac $(SOURCE) -d .`, `RUN_CMD = java -cp
      ".:$(SCALA_CP)" <object> $(ARG)`. Scala 3's `scala` runner is scala-cli-based
      and WILL NOT run a bare compiled object off a classpath, so the cell runs the
      main class on the JVM with the Scala runtime library on the classpath. The
      Makefile derives `SCALA_CP` from the resolved `scalac` path:
      `SCALA_LIBDIR := $(shell cd "$(dirname $(readlink -f $(command -v scalac)))/.." && pwd)/libexec/maven2/org/scala-lang`
      then globs `scala-library-*.jar` (the ~9 MB jar is the complete Scala 3
      runtime; the maven2 `scala3-library_3` jar is an empty stub in Homebrew).
    - Each cell mirrors the Java cell's ARG/VALIDATION_N/STDIN_FILE/BINARY_OUTPUT.
      mandelbrot keeps `BINARY_OUTPUT = 1` and a binary `cmp` against the `.pbm`
      reference (no cases.txt). The 3 stdin problems validate via the committed
      small `cases.txt` fasta inputs, so the big gitignored `STDIN_FILE` is not
      needed for validation.
    - DEPLOY CAVEAT: the Scala `SCALA_LIBDIR` derivation assumes a Homebrew/maven2
      jar layout and `readlink -f`. On bicho (Linux, differently packaged Scala)
      adjust `SCALA_LIBDIR` to that host's scala-library location.

41. **Go — leetcode suite already filled; 3 wrong solutions fixed; clbg already
    integrated.** The `\{\s*\}` empty-body stub detector FALSE-POSITIVES on Go
    (Go uses `T{}` composite literals everywhere), so a full `run_validate.sh
    golang` sweep is the only reliable stub oracle for Go: it found 0 empty stubs
    and 96/99 PASS, with 3 genuinely-wrong committed solutions, now fixed:
    `count-vowel-substrings-of-a-string` (bad running-bitmask → per-start scan),
    `the-earliest-and-latest-rounds-where-players-compete` (bad closed form →
    memoized winner-set DP), and `number-of-ways-to-reorder-array-to-get-same-bst`
    (`>=` → `> root`, see item 38). Go leetcode is now 98 PASS + 1 DATA-BUG. Go
    clbg was already perfarena-integrated (10/10 Makefiles include perfarena.mk).
