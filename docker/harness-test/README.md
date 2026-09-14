# PerfArena harness test container

A local Docker replica of the **bicho** runner host: all 14 language toolchains
plus the codecarbon measurement venv, so you can run and debug the arena-local
harness (compile, validate, measure) on your laptop **without deploying to
bicho**. Mirrors `setup-harness-host.sh` toolchain-for-toolchain.

The repo is **mounted** at `/work` at run time, so your local edits to the
harness scripts take effect immediately with no rebuild.

## What is and is not faithful

| Aspect | In the container |
|---|---|
| Compile / run / correctness (`make validate`) | **Faithful** — same toolchains, same versions |
| Runtime, peak memory, iteration counts | **Faithful** |
| Harness logic (codegen, marshalling, drivers) | **Faithful** |
| **Absolute energy (Joules)** | **Estimate only** — a container cannot read RAPL package counters, so codecarbon falls back to a TDP estimate |

Use it to reproduce and fix harness bugs (compile errors, missing files, routing,
the TS `??` transpile, CLBG measurement, etc.). Do **not** quote its energy
numbers in the paper — those still come from bare-metal bicho.

## Quick start

```bash
# 1. Build once (slow: it downloads every toolchain — 15-30 min, a few GB)
./docker/harness-test/run.sh build

# 2. Drop into a shell in /work (the repo root)
./docker/harness-test/run.sh

# inside the container:
cat /image-toolchains.txt          # confirm every language installed
```

## Troubleshoot one cell (the common workflow)

```bash
# inside the container — LeetCode C++ correctness + one measurement case:
cd Energy-Languages/C++/leetcode/cousins-in-binary-tree
printf 'class Solution { public: bool isCousins(TreeNode* r,int x,int y){ return false; } };\n' > solution.cpp
make -s clean compile validate                       # correctness (full-suite oracle)
bash ../../../selection/run_harness.sh cpp 1.0 0     # ONE measured case -> CASE=/ITERS=/BEACON= line

# CLBG (whole-program) — validate + measure via make:
cd /work/Energy-Languages/Java/clbg/binary-trees
make -s clean compile validate                       # golden-output diff
make -s measure                                      # energy+wall (codecarbon), appends ../Java.jsonl
```

Any language works the same way — swap `cpp` for `java`, `typescript`, `rust`,
`python3`, `csharp`, `go`, … and the cell path for its `<Lang>/…` directory.

## Run the full :8787 runner (end-to-end, like bicho)

```bash
./docker/harness-test/run.sh server
# now POST measure/validate requests to http://localhost:8787 exactly as the
# backend does. Point a local backend / matrix_sweep --harness at it.
```

## Notes

- **Energy on a Linux host:** if you run this on a Linux box that exposes
  `/sys/class/powercap/intel-rapl:*`, `PERFARENA_RAPL=1 ./run.sh` adds
  `--privileged` + a powercap mount to try real RAPL. No effect on macOS/Windows
  (the Docker VM has no RAPL). Even then, container energy is less reliable than
  bare metal — see `setup-harness-host.sh`.
- **Guaranteed vs best-effort languages:** C, C++, C#, Go, Java, JavaScript,
  PHP, Python, Ruby, Rust, TypeScript always build into the image. Kotlin,
  Scala, and Swift are best-effort (a flaky download logs a WARNING at build but
  never fails the image); check `/image-toolchains.txt` to see which landed.
- **Env preset:** `PERFARENA_PROFILER=codecarbon`, `PYTHONPATH=/work/Energy-Languages`,
  and small `PERFARENA_MEASURE/WARMUP/IDLE_S` values for fast iteration. Override
  any of them with `-e` via the passthrough form:
  `./docker/harness-test/run.sh env PERFARENA_MEASURE=20 bash`.
- **Redeploy parity:** because the repo is mounted, whatever you fix locally and
  verify here is exactly what you then `git pull` on bicho.
