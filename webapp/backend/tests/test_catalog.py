"""Curated catalog + small cross-cutting safety nets."""
from conftest import ADMIN, GOOD_CODE, TRACE, USER


def test_compare_winners_suppress_raw_runtime_across_languages(client):
    """When the compared models span multiple languages, raw-ms / raw-MB
    winners must NOT be crowned (those numbers aren't comparable across
    languages). Percentile + pass@* winners stay."""
    # Two single-attempt submissions in two languages, same model name.
    for lang in ("python3", "cpp"):
        body = {
            "model_name": "xlang-model", "model_version": "v1",
            "language": lang, "prompt_template": "solve the problem cleanly",
            "model_params": {},
            "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                          "inference_trace": TRACE}],
        }
        r = client.post("/api/submissions", headers=ADMIN, json=body)
        assert r.status_code == 202

    # Pick the model twice (a single model in scope; languages={python3, cpp}).
    # Compare needs >= 2 models, so add the existing test-model too.
    cmp = client.get("/api/compare",
                     params=[("models", "xlang-model"), ("models", "test-model")])
    assert cmp.status_code == 200
    w = cmp.json()["winners"]
    # Cross-language: raw-runtime/memory winners are suppressed.
    assert w["fastest_avg_runtime"] is None
    assert w["leanest_avg_memory"] is None
    # Percentile / pass@* / solved winners stay (language-fair).
    assert "best_pass_at_1" in w           # may be a name or None — just present


def test_compare_winners_kept_when_single_language_filter(client):
    """With ?language=python3, all rows are in one language → raw-ms / raw-MB
    winners are fair to crown again."""
    cmp = client.get("/api/compare",
                     params=[("models", "xlang-model"), ("models", "test-model"),
                             ("language", "python3")])
    assert cmp.status_code == 200
    w = cmp.json()["winners"]
    # When a model has at least one number, the field should be populated.
    # We only assert "not unconditionally suppressed".
    assert (w["fastest_avg_runtime"] is not None) or all(
        r.get("avg_runtime_ms") is None for r in cmp.json()["summary"])


def test_prompts_seeded_and_public(client):
    r = client.get("/api/prompts")
    assert r.status_code == 200
    rows = r.json()
    # _seed_default_prompts inserts at least two on first boot.
    assert len(rows) >= 2
    names = {p["name"] for p in rows}
    assert "standard-code-only" in names


def test_admin_can_add_edit_delete_prompt(client):
    payload = {
        "name": "explain-then-code-v2",
        "system_prompt": "Explain briefly then write {{language_display}} code in a ```{{language_fence}} block.",
        "user_prompt_template": "Slug `{{slug}}`:\n{{statement}}",
        "notes": "test variant",
    }
    # auth gates
    assert client.post("/api/admin/prompts", json=payload).status_code == 401
    assert client.post("/api/admin/prompts", headers=USER, json=payload).status_code == 403

    r = client.post("/api/admin/prompts", headers=ADMIN, json=payload)
    assert r.status_code == 201
    pid = r.json()["id"]
    assert pid > 0

    # appears in public list
    names = {p["name"] for p in client.get("/api/prompts").json()}
    assert "explain-then-code-v2" in names

    # edit
    upd = dict(payload, notes="updated")
    e = client.put(f"/api/admin/prompts/{pid}", headers=ADMIN, json=upd)
    assert e.status_code == 200 and e.json()["notes"] == "updated"

    # delete
    d = client.delete(f"/api/admin/prompts/{pid}", headers=ADMIN)
    assert d.status_code == 204
    assert client.get(f"/api/prompts/{pid}").status_code == 404


def test_submission_with_prompt_id_captures_and_filters(client):
    """A submission posted with prompt_id stamps + exposes the prompt link,
    and /api/rankings supports filtering by it. Bad prompt_id → 400."""
    p = client.get("/api/prompts").json()[0]
    pid = p["id"]
    # Submit all 5 sampled slugs so the eligibility gate (on in the test env)
    # admits the submission and we can assert it shows up in rankings.
    all_slugs = ("two-sum", "add-digits", "24-game", "valid-parentheses",
                 "reverse-integer")
    body = {
        "model_name": "prompt-test-m", "model_version": "v1",
        "language": "python3", "prompt_template": "stand-in text",
        "model_params": {"temperature": 0.0},
        "prompt_id": pid,
        "attempts": [
            {"title_slug": s, "code": GOOD_CODE, "inference_trace": TRACE}
            for s in all_slugs
        ],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]

    # Force pass-rate above the 25% threshold deterministically (the fake
    # judge is random) so the rankings filter assertion is stable.
    from sqlmodel import Session
    from app.db import engine
    from app.models import Submission
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        sub.total_solved = 4; sub.total_problems = 5; sub.pass_at_1 = 0.8
        s.add(sub); s.commit()

    detail = client.get(f"/api/submissions/{sid}").json()
    assert detail["submission"]["prompt_id"] == pid
    assert detail["submission"]["prompt_name"] == p["name"]

    # rankings filter pulls only submissions stamped with this prompt
    listed = client.get("/api/rankings", params={"prompt_id": pid}).json()
    assert all(r["prompt_id"] == pid for r in listed)
    assert sid in {r["submission_id"] for r in listed}

    # bogus prompt_id rejected before any side effects
    bad = client.post("/api/submissions", headers=ADMIN,
                      json=dict(body, prompt_id=999999))
    assert bad.status_code == 400


def test_registered_models_crud(client):
    payload = {
        "name": "qwen2.5-coder:7b",
        "version": "ollama",
        "provider": "ollama",
        "endpoint": "https://example.ollama/api",
        "params": {"temperature": 0.2},
        "notes": "Granted access via SanadLab.",
    }
    assert client.post("/api/admin/registered-models", json=payload).status_code == 401
    assert client.post("/api/admin/registered-models", headers=USER, json=payload).status_code == 403

    r = client.post("/api/admin/registered-models", headers=ADMIN, json=payload)
    assert r.status_code == 201
    body = r.json()
    rm_id = body["id"]
    assert body["status"] == "active"

    listed = {m["id"] for m in client.get("/api/registered-models").json()}
    assert rm_id in listed

    # retire it -> default list (active-only) hides it
    e = client.put(f"/api/admin/registered-models/{rm_id}", headers=ADMIN,
                   json=dict(payload, status="retired"))
    assert e.status_code == 200 and e.json()["status"] == "retired"
    assert rm_id not in {m["id"] for m in client.get("/api/registered-models").json()}
    # include_retired=true brings it back
    assert rm_id in {m["id"] for m in
                     client.get("/api/registered-models",
                                params={"include_retired": "true"}).json()}

    d = client.delete(f"/api/admin/registered-models/{rm_id}", headers=ADMIN)
    assert d.status_code == 204
    assert client.get(f"/api/registered-models/{rm_id}").status_code == 404
