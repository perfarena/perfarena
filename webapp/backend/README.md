# Arena backend

FastAPI + SQLModel + SQLite.

## Setup

```bash
cd webapp/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# bootstrap admin via env (writes admin API key to stdout)
ARENA_ADMIN_EMAIL=you@example.com ARENA_ADMIN_NAME='You' python -m app.seed

uvicorn app.main:app --reload --port 8000

# (optional) populate fake competitors & judged submissions so the leaderboard isn't empty:
python -m app.populate
```

Visit http://localhost:8000/docs for the auto-generated OpenAPI UI.

## Environment variables

| var                                 | default | purpose                                            |
|-------------------------------------|---------|----------------------------------------------------|
| `ARENA_DB`                          | `webapp/backend/arena.db` | sqlite path                  |
| `ARENA_ADMIN_EMAIL` / `_NAME`       |         | bootstrap admin (seed only)                        |
| `ARENA_DISPATCH_ENABLED`            | `0`     | `1` → call real `leetbot.py`; else fake judge      |
| `ARENA_DISPATCH_INTER_DELAY`        | `12`    | seconds between LeetCode submits within one batch (±20% jitter) |
| `ARENA_DISPATCH_MAX_RETRIES`        | `4`     | retries with exponential backoff on 429            |
| `ARENA_DISPATCH_SUBPROC_TIMEOUT`    | `180`   | per-attempt subprocess timeout (seconds)           |
| `ARENA_SUBMIT_COOLDOWN_S`           | `600`   | per-user min seconds between batches               |
| `ARENA_SUBMIT_DAILY_CAP`            | `5`     | per-user max batches per rolling 24h               |

Real LeetCode dispatch also requires `LEETCODE_COOKIE_HEADER` (or `LEETCODE_SESSION`+`LEETCODE_CSRF_TOKEN`) in a `.env` file at the repo root — see `../../.env.example`.

## Auth

Approved users get an API key from `POST /api/admin/users/{id}/approve`. They pass it via:

```
Authorization: Bearer arena_xxxxxxxx
```

## Public routes

- `GET /api/problems` — list problems (filter `level`, `tag`)
- `GET /api/rankings` — leaderboard (filter `language`, `level`, `sort`)
- `GET /api/submissions` — public list of all submissions
- `GET /api/submissions/{id}` — submission detail (model params, prompt, per-problem runs)
- `POST /api/register` — request access

## Gated routes (`Authorization: Bearer`)

- `GET /api/me` — whoami
- `GET /api/problems/{slug}` — full problem statement
- `POST /api/submissions` — submit a batch (all problems required)
- `GET /api/me/submissions` — your submissions

## Admin routes (`role=admin`)

- `GET /api/admin/pending` — pending registrations
- `POST /api/admin/users/{id}/approve` — grants API key
- `POST /api/admin/users/{id}/reject`
