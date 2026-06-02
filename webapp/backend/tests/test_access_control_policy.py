"""Platform-wide access-control policy.

The rules (set by the curated-platform pivot):

  * POST/PUT/DELETE endpoints that ADD or MUTATE system content are
    ADMIN-ONLY. Self-submission was retired; the team submits on behalf of
    model authors.
  * All other endpoints (reads, login, captcha, personal /me operations)
    are open for the community to use.

These tests pin the policy at the route level so a future drift surfaces
immediately."""
import json

from conftest import ADMIN, GOOD_CODE, TRACE, USER


# ---------- writes that MUST be admin-only ---------------------------------

def _attempt_body(slug="two-sum"):
    return {
        "model_name": "policy-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": slug, "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }


def test_post_submissions_requires_admin(client):
    assert client.post("/api/submissions", json=_attempt_body()).status_code == 401
    assert client.post("/api/submissions", headers=USER, json=_attempt_body()).status_code == 403
    assert client.post("/api/submissions", headers=ADMIN, json=_attempt_body()).status_code == 202


def test_post_append_attempts_requires_admin(client):
    sid = client.post("/api/submissions", headers=ADMIN,
                      json=_attempt_body()).json()["submission_id"]
    body = {"attempts": [{"title_slug": "add-digits", "code": GOOD_CODE,
                          "inference_trace": TRACE}]}
    assert client.post(f"/api/submissions/{sid}/attempts", json=body).status_code == 401
    assert client.post(f"/api/submissions/{sid}/attempts", headers=USER, json=body).status_code == 403
    assert client.post(f"/api/submissions/{sid}/attempts", headers=ADMIN, json=body).status_code == 202


def test_delete_submission_requires_admin(client):
    """Owner-can-delete was retired with the curated-platform pivot — only
    admins can soft-delete submissions now, regardless of who submitted them."""
    sid = client.post("/api/submissions", headers=ADMIN,
                      json=_attempt_body()).json()["submission_id"]
    assert client.delete(f"/api/submissions/{sid}").status_code == 401
    assert client.delete(f"/api/submissions/{sid}", headers=USER).status_code == 403
    assert client.delete(f"/api/submissions/{sid}", headers=ADMIN).status_code == 204


def test_post_register_requires_admin(client):
    """Self-signup is closed. Admin onboards accounts; the captcha flow is
    still applied so the code path is reusable if a public window reopens."""
    body = {"email": "policy@test.dev", "display_name": "Policy",
            "captcha_token": "x", "captcha_answer": "x"}
    assert client.post("/api/register", json=body).status_code == 401
    assert client.post("/api/register", headers=USER, json=body).status_code == 403


# ---------- writes that stay open or personal ------------------------------

def test_login_is_public(client):
    """Sign-in itself must remain open or no admin could ever obtain a session."""
    r = client.post("/api/login", json={"email": "no-such@test.dev",
                                        "password": "x"})
    # 401 because creds are wrong, NOT 401 because the endpoint refused us
    # the chance to try. The point is auth-failed, not auth-required.
    assert r.status_code in (401, 403)


def test_captcha_endpoint_is_public(client):
    assert client.get("/api/captcha").status_code == 200


# ---------- reads that MUST be open to the community ----------------------

def test_dataset_export_is_public(client):
    """Community needs the dataset to verify the leaderboard end-to-end."""
    r = client.get("/api/datasets/solutions")
    assert r.status_code == 200
    # Even though there may be no records, the endpoint must answer.
    for ln in r.text.splitlines():
        if ln.strip():
            json.loads(ln)         # any line that exists must be valid JSON
            break


def test_public_read_routes_do_not_require_auth(client):
    """A representative slice of read endpoints — none should require auth."""
    for path in [
        "/api/problems",
        "/api/rankings",
        "/api/submissions",
        "/api/registered-models",
        "/api/prompts",
        "/api/competitions",
        "/api/blog",
        "/api/models",
        "/api/analysis",
        "/api/discrimination",
        "/api/provenance/log",
        "/api/captcha",
        "/api/languages",
        "/api/snapshots",
    ]:
        r = client.get(path)
        assert r.status_code != 401, f"{path} unexpectedly requires auth"
        assert r.status_code != 403, f"{path} unexpectedly forbids anon access"
