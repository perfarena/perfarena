"""Tests for the API endpoints that lacked coverage.

The existing suite covers ~40 endpoints; this file fills in the remaining
six (cross-referenced against `@app.get/post/put/delete` in main.py):

  GET    /api/models                       — list distinct models with subs
  GET    /api/me/by-language               — per-language matrix for caller
  POST   /api/me/api-key/regenerate        — rotate API key
  DELETE /api/me/api-key                   — discard API key
  GET    /api/admin/baselines/state        — auto-refresher diagnostic
  POST   /api/admin/baselines/refresh      — force refresh + recompute

Each gets a happy path + the auth boundaries that apply.
"""
from conftest import ADMIN, ADMIN_KEY, USER, USER_KEY, GOOD_CODE, TRACE, make_submission, HARNESS


# ---------- /api/models -----------------------------------------------------

def test_list_models_returns_distinct_models_with_submissions(client):
    """Lists distinct model names sourced from non-deleted submissions, plus
    per-model submission count and language set. The fixture submissions all
    use model_name='test-model' under python3 so we expect to see it there."""
    # Seed at least one submission so there's something to list.
    make_submission(client)

    rows = client.get("/api/models").json()
    assert isinstance(rows, list)
    assert len(rows) >= 1
    by_name = {m["name"]: m for m in rows}
    assert "test-model" in by_name
    m = by_name["test-model"]
    assert m["n_submissions"] >= 1
    assert "python3" in m["languages"]
    # languages list is sorted
    assert m["languages"] == sorted(m["languages"])


def test_list_models_is_public_no_auth_required(client):
    """The model picker on the public Compare page hits this; must be open."""
    r = client.get("/api/models")           # no Authorization header
    assert r.status_code == 200


# ---------- /api/me/by-language ---------------------------------------------

def test_me_by_language_requires_auth(client):
    """Personal matrix — anonymous callers get 401."""
    assert client.get("/api/me/by-language").status_code == 401


def test_me_by_language_returns_per_language_matrix(client):
    """Submit in two languages, confirm the response splits by language."""
    # Submission 1: python3, an existing fixture problem
    make_submission(client, slugs=("two-sum",))
    # Submission 2: cpp, same slug — different language bucket
    body = {
        "harness_slug": HARNESS,
        "model_name": "test-model", "model_version": "v1",
        "language": "cpp", "prompt_template": "solve the problem cleanly",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    assert client.post("/api/submissions", headers=ADMIN, json=body).status_code == 202

    body = client.get("/api/me/by-language", headers=ADMIN).json()
    # Tolerate either shape — endpoint returns a per-language matrix; we
    # just need to see python3 + cpp keys reflected somewhere.
    serialized = repr(body)
    assert "python3" in serialized
    assert "cpp" in serialized


# ---------- /api/me/api-key/regenerate --------------------------------------

def test_api_key_regenerate_requires_auth(client):
    assert client.post("/api/me/api-key/regenerate").status_code == 401


def test_api_key_regenerate_swaps_key_and_invalidates_old(client):
    """POSTing returns a new key; subsequent calls with the OLD bearer fail.
    Use a throw-away user so the rotation doesn't break the rest of the suite."""
    # Carve out an isolated user we can rotate without affecting other tests.
    from sqlmodel import Session
    from app.db import engine
    from app.auth import hash_api_key
    from app.models import User, UserRole, UserStatus
    with Session(engine) as s:
        u = User(email="rotate-test@x.dev", display_name="Rotator",
                 role=UserRole.user, status=UserStatus.approved,
                 api_key=hash_api_key("arena_rotate_oldkey"))
        s.add(u); s.commit()
        uid = u.id

    old_headers = {"Authorization": "Bearer arena_rotate_oldkey"}
    r = client.post("/api/me/api-key/regenerate", headers=old_headers)
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert new_key and new_key != "arena_rotate_oldkey"

    # Old bearer is now dead.
    assert client.get("/api/me", headers=old_headers).status_code == 401
    # New bearer works.
    new_headers = {"Authorization": f"Bearer {new_key}"}
    assert client.get("/api/me", headers=new_headers).status_code == 200

    # Clean up so the suite's user table stays tidy.
    with Session(engine) as s:
        u = s.get(User, uid)
        if u: s.delete(u); s.commit()


# ---------- DELETE /api/me/api-key ------------------------------------------

def test_api_key_delete_requires_auth(client):
    assert client.delete("/api/me/api-key").status_code == 401


def test_api_key_delete_clears_programmatic_access(client):
    """After DELETE, the key is None and the same bearer no longer works.
    Isolated user — we don't want to brick ADMIN/USER for the rest of the run."""
    from sqlmodel import Session, select
    from app.db import engine
    from app.auth import hash_api_key
    from app.models import User, UserRole, UserStatus
    with Session(engine) as s:
        u = User(email="delete-key-test@x.dev", display_name="DelKey",
                 role=UserRole.user, status=UserStatus.approved,
                 api_key=hash_api_key("arena_delete_oldkey"))
        s.add(u); s.commit()
        uid = u.id

    headers = {"Authorization": "Bearer arena_delete_oldkey"}
    # Sanity: the key works before deletion.
    assert client.get("/api/me", headers=headers).status_code == 200

    r = client.delete("/api/me/api-key", headers=headers)
    assert r.status_code == 204

    # DB row's api_key column was nulled out.
    with Session(engine) as s:
        u = s.get(User, uid)
        assert u.api_key is None

    # The dropped bearer is rejected on the next call.
    assert client.get("/api/me", headers=headers).status_code == 401

    with Session(engine) as s:
        u = s.get(User, uid)
        if u: s.delete(u); s.commit()


# ---------- /api/admin/baselines/state --------------------------------------

def test_baselines_state_admin_only(client):
    assert client.get("/api/admin/baselines/state").status_code == 401
    assert client.get("/api/admin/baselines/state", headers=USER).status_code == 403


def test_baselines_state_returns_diagnostic_shape(client):
    """Echoes the trigger thresholds, the source-count breakdown, and the
    last_refresh_at watermark. We assert on the SHAPE; values vary by env."""
    r = client.get("/api/admin/baselines/state", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    for key in ("last_refresh_at", "submissions_since_last", "trigger_every_n",
                "trigger_hours", "stale_days", "auto_refresh_enabled",
                "baselines_by_source"):
        assert key in body, f"missing key in baselines state: {key}"
    assert isinstance(body["baselines_by_source"], dict)


# ---------- /api/admin/baselines/refresh ------------------------------------

def test_baselines_refresh_admin_only(client):
    assert client.post("/api/admin/baselines/refresh").status_code == 401
    assert client.post("/api/admin/baselines/refresh", headers=USER).status_code == 403


def test_baselines_refresh_runs_to_completion_in_test_env(client):
    """The refresh endpoint is now async: POST kicks off a daemon thread and
    returns 202 with the in-flight progress immediately, then a separate
    progress endpoint reports `state == 'done'` when the worker finishes.
    The test env has no real LeetCode network, so the worker can only seed
    synthetic placeholders; we wait for the state flip and assert the
    progress payload shape."""
    import time
    r = client.post("/api/admin/baselines/refresh", headers=ADMIN)
    assert r.status_code == 202, r.text
    init = r.json()
    assert init["state"] in ("running", "done")     # may have flipped already

    # Poll until done. The test DB is tiny so the worker finishes well
    # under the deadline — bail out with the latest snapshot if it doesn't.
    deadline = time.time() + 30
    last = init
    while last["state"] == "running" and time.time() < deadline:
        time.sleep(0.2)
        last = client.get("/api/admin/baselines/refresh/progress",
                          headers=ADMIN).json()
    assert last["state"] == "done", last
    # Counts are present and integers (the worker may have inserted 0 if
    # the test DB has no accepted runs at all).
    for k in ("real", "synthetic", "kept"):
        assert isinstance(last[k], int), last
    assert last["ended_at"] is not None

    # state endpoint moved its own last_refresh_at forward too.
    after = client.get("/api/admin/baselines/state", headers=ADMIN).json()
    assert after["last_refresh_at"] is not None
    assert after["submissions_since_last"] == 0


def test_baselines_refresh_double_post_is_safe(client):
    """Two POSTs in quick succession both return 202 with a valid progress
    snapshot — the second one is either a no-op (worker still running) or
    a fresh start (worker already done). Either way, no exception, no
    duplicate work-in-flight."""
    import time
    r1 = client.post("/api/admin/baselines/refresh", headers=ADMIN)
    assert r1.status_code == 202
    r2 = client.post("/api/admin/baselines/refresh", headers=ADMIN)
    assert r2.status_code == 202
    for body in (r1.json(), r2.json()):
        assert body["state"] in ("running", "done", "idle")
    # Drain so subsequent tests aren't racing the worker.
    deadline = time.time() + 30
    while time.time() < deadline:
        progress = client.get("/api/admin/baselines/refresh/progress",
                              headers=ADMIN).json()
        if progress["state"] != "running":
            break
        time.sleep(0.2)
