# leetcode_crawler

Tools for turning a HuggingFace-style LeetCode dataset into a workable problem/solution corpus, sampling solutions for evaluation, and submitting them to LeetCode to capture judge feedback.

## Repo layout

```
leetcode_crawler/
├── train-00000-of-00001.parquet   Raw HF dataset (34,903 rows; 1,853 problem ids)
├── prepare_dataset.py             Split raw parquet → data/problems.parquet + data/solutions.parquet
├── sample_solutions.py            Pick top-N problems per difficulty by solution count, dump to disk
├── leetbot.py                     Submit a single solution to LeetCode and capture judge metadata
├── data/
│   ├── problems.parquet           1,973 deduped problem rows (id, slug, tag, level, content, hints)
│   ├── solutions.parquet          4,391 code blocks extracted from `content` (slug, lang, code, …)
│   ├── runs.jsonl                 Append-only log of LeetCode submission runs
│   └── test_submissions/          Hand-written Python solutions used to smoke-test leetbot.py
├── sampled_solutions/             Output of sample_solutions.py — see below
├── requirements.txt
├── .env.example                   Auth template for leetbot.py
└── env/                           Local virtualenv (gitignored in practice)
```

## Datasets

### `train-00000-of-00001.parquet` (38 MB, HF-shaped)
- 34,903 rows × 7 cols: `id, question_content, title_slug, tag, level, question_hints, content`
- 1,853 unique problem ids — ~18 long-form `content` variants per problem
- `level` distribution: Medium 19,025 / Easy 8,355 / Hard 6,973 / NaN 550
- Top tags: Array, String, Dynamic Programming, Hash Table, Math, DFS, Sorting…

### `data/problems.parquet` (1.2 MB)
- 1,973 rows × 6 cols, one row per `title_slug`
- `level` populated on every row (Medium 1,046 / Easy 494 / Hard 433)
- `question_hints` is null on 863 rows; everything else is complete

### `data/solutions.parquet` (917 KB)
- 4,391 rows × 7 cols (`source_row_id, title_slug, id, level, lang, code, leetcode_shaped`)
- Covers 1,030 of the 1,973 problem variants
- Languages: python3 (3,058), java (673), cpp (331), javascript (280), then csharp/c/golang/rust/ruby/swift/typescript/php/scala
- `leetcode_shaped` flags whether the snippet looks like a LeetCode submission (e.g. `class Solution`) — 110 True / 4,281 False
- Keys cleanly back to `problems.parquet` by both `id` and `title_slug`

Note: 169 ids in `problems.parquet` appear under multiple slugs with differing levels — join on `title_slug` if you need a stable difficulty label.

### `data/runs.jsonl`
Append-only log produced by `leetbot.py`. Each line records one submission: `submission_id, status_msg, runtime_ms, memory_mb, runtime_percentile, memory_percentile, total_correct, total_testcases`.

## Scripts

### `prepare_dataset.py`
Reads the raw HF parquet, dedupes problems on `title_slug`, and extracts fenced code blocks from the `content` field into `solutions.parquet`. The fence-language → canonical-lang mapping lives at the top of the file (`LANG_MAP`).

```bash
python prepare_dataset.py [--src train-00000-of-00001.parquet] [--out data]
```

### `sample_solutions.py`
Picks the top-N problems per difficulty (by row count in `solutions.parquet`) and writes every solution for those problems to disk:

```
sampled_solutions/<problem-slug>/<language>/solution-<nr>.<ext>
```

Solution files are numbered deterministically by `source_row_id`. Ties at the cutoff are broken alphabetically by slug. A `_selected_problems.csv` is written next to the output for inspection.

```bash
python sample_solutions.py [--top 30] [--out sampled_solutions] [--solutions data/solutions.parquet]
```

Default run (`--top 30`) selects 90 problems and writes ~1,239 solution files.

### `leetbot.py`
Submits a single solution file to LeetCode and returns the judge's metadata (status, runtime, memory, percentiles, testcase counts). Requires authenticated cookies — see Setup below.

```bash
python leetbot.py <problem-slug> <lang> <path-to-solution-file>
# e.g.
python leetbot.py two-sum python3 data/test_submissions/maximum-depth-of-binary-tree.py
```

## Setup

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt        # httpx, python-dotenv, pandas, pyarrow
```

### Auth for `leetbot.py`
Copy `.env.example` to `.env` and provide LeetCode credentials. Two options:

- **`LEETCODE_COOKIE_HEADER`** — paste the full `Cookie:` header from a logged-in DevTools request. Simplest, most reliable.
- **`LEETCODE_SESSION` + `LEETCODE_CSRF_TOKEN`** — pull the individual cookies from Application → Cookies → leetcode.com.

`LEETCODE_HOST` defaults to `https://leetcode.com`; set to `https://leetcode.cn` for the China site.

## Typical workflow

1. `python prepare_dataset.py` — derive `data/problems.parquet` and `data/solutions.parquet` from the raw HF parquet.
2. `python sample_solutions.py` — pull a balanced sample of problems and lay their solutions out on disk.
3. `python leetbot.py <slug> <lang> <file>` — submit one of the sampled solutions to LeetCode; results land in `data/runs.jsonl`.
