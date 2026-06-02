# PerfArena

Open competition site where AI labs submit batches of solutions for a fixed set of LeetCode problems. Submissions are dispatched to LeetCode for verification; verified results are ranked by language, problem, and difficulty using pass@k and avg runtime/memory percentiles.

> **Note on percentile semantics.** LeetCode's per-run runtime/memory percentile is observably noisy in our data (~4 pp within-attempt spread, ~50 % ranking agreement with raw ms across attempts). The platform leaderboard's ~500-sample mean smooths most of it out, but the per-problem view shows best-of-5 with mean alongside instead of a single noisy run. The full empirical analysis and what LC actually documents (vs. what's just community speculation) lives in [`docs/leetcode-percentile-semantics.md`](../docs/leetcode-percentile-semantics.md).

Built at NYU Abu Dhabi by Rui Rua, Karim Ali (SanadLab), and João Paulo Fernandes. Licensed under Apache 2.0 — see `LICENSE`.

```
webapp/
├── backend/        FastAPI + SQLModel + SQLite. Imports the parent repo's leetbot.py for LeetCode dispatch.
└── frontend/       Vite + React + TypeScript PWA. Public rankings; gated submission/problem browser.
```

## What's public vs. gated

| Surface                                  | Public | Auth |
|------------------------------------------|--------|------|
| List of supported problems (names only)  |   ✓    |      |
| Problem details (content, hints)         |        |  ✓   |
| Rankings (model × language × problem)    |   ✓    |      |
| Submission summaries (model, params, submitter, scores) | ✓ |    |
| Register request                         |   ✓    |      |
| Submit a batch of solutions              |        |  ✓   |
| Inspect own submission runs              |        |  ✓   |
| Approve / reject registrations           |        |  admin |

## Submission rules

A submission is one **batch**: a candidate solution for every supported problem in one language, plus per-problem metadata:

- `inference_trace` (required) — the chain-of-thought / tool calls / reasoning steps emitted by the model
- `model_params` (required) — name, version, temperature, top_p, max_tokens, system prompt hash
- `prompt_template` (required) — exact prompt template used
- `wall_clock_ms`, `cost_usd` (optional but ranked)

After submission the backend queues each solution for LeetCode dispatch. Only solutions accepted by LeetCode count toward pass@k.

## Running locally

```bash
# Backend (in webapp/backend)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.seed                  # seeds problems from ../../data/problems.parquet
uvicorn app.main:app --reload       # http://localhost:8000

# Frontend (in webapp/frontend)
npm install
npm run dev                         # http://localhost:5173
```

See `backend/README.md` and `frontend/README.md` for details.
