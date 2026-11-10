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

## Static snapshot on GitHub Pages

The live arena runs in the container. GitHub Pages can also serve a read-only copy of its public pages at https://perfarena.github.io/perfarena/. The copy reads every ranking, submission, problem, and comparison from JSON files instead of the API. It has no accounts, no submissions, and no admin pages. The banner shows the date of the snapshot.

### Before the first snapshot

Do these steps once, on the computer that has this repository:

```bash
cd webapp/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../frontend && npm install
git remote add perfarena git@github.com:perfarena/perfarena.git    # the public repository
```

### Make and publish a snapshot

1. **Copy the database of the live arena.** Use `.backup`, not `cp`, because `.backup` gives a complete copy while the server runs:

   ```bash
   sqlite3 /path/to/arena.db ".backup arena-copy.db"
   ```

   If the arena runs in Docker, make the copy in the container and then take it out:

   ```bash
   docker exec perfarena-backend python -c "import sqlite3; d = sqlite3.connect('/data/arena-copy.db'); sqlite3.connect('file:/data/arena.db?mode=ro', uri=True).backup(d); d.close()"
   docker cp perfarena-backend:/data/arena-copy.db arena-copy.db
   docker exec perfarena-backend rm /data/arena-copy.db
   ```

   Bring `arena-copy.db` to the computer that has this repository.

2. **Build the snapshot.** From the root of the repository:

   ```bash
   webapp/publish_pages.sh ~/arena-copy.db
   ```

   If the live server sets `ARENA_LEADERBOARD_GATE`, `ARENA_LEADERBOARD_MIN_PASS_RATE`, or an `ARENA_RANKING_*` variable, put the same values in front of the command (for example `ARENA_LEADERBOARD_GATE=on webapp/publish_pages.sh ...`). These settings change what the public sees. A setting that an admin changed on the Variables page is in the database, so the copy has it already.

   The script never changes the database file. For a 60 MB database it takes about 6 minutes and makes about 220 MB of files. At the end, it prints the size, the commit, and a preview command.

3. **Look at the preview.** Run the preview command that the script printed, then open http://localhost:8080/perfarena/. Open the other pages by clicking. A reload on a deeper page shows "not found" only in this preview. GitHub Pages loads the app for these pages.

4. **Publish.**

   ```bash
   git push --force perfarena gh-pages
   ```

   The push sends only the snapshot. The `gh-pages` commit has no parent, so it holds no source code and no history of the repository. Each snapshot replaces the previous one.

5. **The first time only, turn on GitHub Pages.** On github.com/perfarena/perfarena, open **Settings → Pages**. Set **Source** to **Deploy from a branch**, the branch to `gh-pages`, and the folder to `/ (root)`. Then click **Save**.

After each push, the site changes in a few minutes. The **Actions** tab of the repository shows the progress as "pages build and deployment".

To update the snapshot, do steps 1 to 4 again.

### Problems

- **`no such column: ...` or `no such table: ...`:** the database is older than the code of the app, for example the old development database `webapp/backend/arena.db`. Use a copy of the live database.
- **The site is larger than 1 GB:** GitHub Pages does not accept it. The script prints the size in step 2.
- **"This view is not in the snapshot":** the snapshot cannot store every combination of filters and comparisons. It stores:
  - **Rankings:** every competition × harness × language (× level for the submission view). It stores each of the other filters alone.
  - **Compare:** every pair of models, and the pairs of the newest submissions of different models in one competition and language.

### What the script does

1. `backend/app/static_export.py` sends every public GET request that the snapshot pages can send to the app, as a visitor who is not signed in, on a second copy of the database. It writes each answer to `data/`.
2. The script builds the frontend with `VITE_STATIC=1`. In this mode, `frontend/src/lib/staticMode.ts` reads `data/` instead of `/api`.
3. It commits the site to the local `gh-pages` branch. It does not push.

The export can also run in the container, if its image has `app/static_export.py`: `python -m app.static_export --db /data/arena.db --out /data/pages-export`. Then copy the `pages-export` folder out and give it to `publish_pages.sh` in place of the database. The second argument of the script sets the address path, which is `/perfarena/` by default. Use `/` for a custom domain.
