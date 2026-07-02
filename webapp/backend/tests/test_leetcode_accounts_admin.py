"""Admin endpoints over the LeetCode credential pool.

The pool lives in data/leetcode_accounts.json and is normally managed via
`python -m app.leetcode_accounts`. These endpoints expose the same
add/remove/clear-exhaustion/list/probe operations to the SPA so a human
doesn't have to docker-exec into the container to refresh a cookie."""
from conftest import ADMIN, USER
from app import leetcode_accounts as _lc


COOKIE_OK = (
    "LEETCODE_SESSION=eyJfdGVzdF9wYXlsb2FkXyI=.fakesig; "
    "csrftoken=abc123xyz"
)


def _reset_pool(monkeypatch, tmp_path):
    """Redirect the pool file to a per-test scratch path AND block the
    .env-bootstrap fallback. Without the second step, load_accounts() on
    an empty file falls through to _bootstrap_from_env, which reads the
    process's LEETCODE_COOKIE_HEADER and silently materialises a
    'primary' account — breaking every "pool is empty" assertion."""
    p = tmp_path / "leetcode_accounts.json"
    monkeypatch.setattr(_lc, "ACCOUNTS_JSON", p)
    monkeypatch.setattr(_lc, "_bootstrap_from_env", lambda: [])
    return p


def test_admin_list_empty_pool(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    r = client.get("/api/admin/leetcode-accounts", headers=ADMIN)
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 0 and d["available"] == 0 and d["exhausted"] == 0
    assert d["accounts"] == []


def test_admin_endpoints_gated_by_admin(client, monkeypatch, tmp_path):
    """Regular users get 401/403; only admins can manage the pool."""
    _reset_pool(monkeypatch, tmp_path)
    # LIST is a GET; everything else is a POST/DELETE.
    r = client.get("/api/admin/leetcode-accounts", headers=USER)
    assert r.status_code in (401, 403)
    r = client.post("/api/admin/leetcode-accounts/probe", headers=USER)
    assert r.status_code in (401, 403)
    r = client.post("/api/admin/leetcode-accounts", headers=USER, json={
        "name": "x", "cookie_header": COOKIE_OK,
    })
    assert r.status_code in (401, 403)
    r = client.delete("/api/admin/leetcode-accounts/x", headers=USER)
    assert r.status_code in (401, 403)


def test_admin_add_account_round_trip(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "primary", "cookie_header": COOKIE_OK,
    })
    assert r.status_code == 201
    d = r.json()
    assert d["total"] == 1 and d["available"] == 1
    assert any(a["name"] == "primary" and not a["is_exhausted"]
               for a in d["accounts"])
    # And the LIST endpoint sees the same account.
    d2 = client.get("/api/admin/leetcode-accounts", headers=ADMIN).json()
    assert {a["name"] for a in d2["accounts"]} == {"primary"}


def test_admin_add_rejects_bad_cookie(client, monkeypatch, tmp_path):
    """Cookie header missing LEETCODE_SESSION or csrftoken is rejected
    server-side so we don't store an obviously-broken credential set."""
    _reset_pool(monkeypatch, tmp_path)
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "bad", "cookie_header": "just some other stuff",
    })
    assert r.status_code == 400


def test_admin_add_rejects_empty_name(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "  ", "cookie_header": COOKIE_OK,
    })
    assert r.status_code == 400


def test_admin_remove_account(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "primary", "cookie_header": COOKIE_OK,
    })
    r = client.delete("/api/admin/leetcode-accounts/primary", headers=ADMIN)
    assert r.status_code == 204
    d = client.get("/api/admin/leetcode-accounts", headers=ADMIN).json()
    assert d["total"] == 0


def test_admin_remove_unknown_account_is_404(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    r = client.delete("/api/admin/leetcode-accounts/nope", headers=ADMIN)
    assert r.status_code == 404


def test_admin_clear_exhaustion_named(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "primary", "cookie_header": COOKIE_OK,
    })
    _lc.mark_exhausted("primary", hours=24)
    # Sanity: exhausted now.
    d = client.get("/api/admin/leetcode-accounts", headers=ADMIN).json()
    assert d["exhausted"] == 1
    # Clear it via the admin endpoint.
    r = client.post(
        "/api/admin/leetcode-accounts/primary/clear-exhaustion",
        headers=ADMIN,
    )
    assert r.status_code == 200
    assert r.json()["exhausted"] == 0


def test_admin_clear_exhaustion_all(client, monkeypatch, tmp_path):
    """name='*' clears every account's exhaustion timer at once."""
    _reset_pool(monkeypatch, tmp_path)
    for n in ("a", "b", "c"):
        client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
            "name": n, "cookie_header": COOKIE_OK,
        })
        _lc.mark_exhausted(n, hours=24)
    d = client.get("/api/admin/leetcode-accounts", headers=ADMIN).json()
    assert d["exhausted"] == 3
    r = client.post(
        "/api/admin/leetcode-accounts/*/clear-exhaustion",
        headers=ADMIN,
    )
    assert r.status_code == 200
    assert r.json()["exhausted"] == 0


def test_admin_clear_exhaustion_unknown_is_404(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    r = client.post(
        "/api/admin/leetcode-accounts/nope/clear-exhaustion",
        headers=ADMIN,
    )
    assert r.status_code == 404


def test_admin_add_overwrites_existing(client, monkeypatch, tmp_path):
    """Calling add with an existing name updates the cookie + clears the
    exhaustion flag — same semantics as the CLI's `--add NAME --cookie …`."""
    _reset_pool(monkeypatch, tmp_path)
    client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "primary", "cookie_header": COOKIE_OK,
    })
    _lc.mark_exhausted("primary", hours=24)
    # Re-add → should clear exhaustion AND replace cookie.
    new_cookie = ("LEETCODE_SESSION=eyJfbmV3X3Bhc3MK.s; csrftoken=newcsrf")
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "primary", "cookie_header": new_cookie,
    })
    assert r.status_code == 201
    d = r.json()
    assert d["total"] == 1 and d["exhausted"] == 0


def test_admin_probe_endpoint_shape(client, monkeypatch, tmp_path):
    """The probe endpoint returns the same shape as LIST, with the probe
    fields populated. We don't actually hit LC in tests, so we stub the
    whoami function to a deterministic result."""
    _reset_pool(monkeypatch, tmp_path)
    client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "primary", "cookie_header": COOKIE_OK,
    })
    # Monkeypatch `whoami` so the test doesn't touch the network. The
    # endpoint imports it via the module reference, so patch the module-
    # level attribute (lookups happen at call time, not at import time).
    monkeypatch.setattr(
        _lc, "whoami",
        lambda acc, **kw: (True, "signed in as fake_user"),
    )
    r = client.post("/api/admin/leetcode-accounts/probe", headers=ADMIN)
    assert r.status_code == 200
    row = r.json()["accounts"][0]
    assert row["is_signed_in"] is True
    assert row["username"] == "fake_user"
    assert "signed in as" in row["probe_message"]


# ---------- split session/csrf field shape ----------------------------------

def test_admin_add_split_session_csrf(client, monkeypatch, tmp_path):
    """The SPA form posts session_token + csrf_token separately. Backend
    stitches them back into the canonical 'LEETCODE_SESSION=…; csrftoken=…'
    shape so downstream readers (leetbot.py) see one format."""
    p = _reset_pool(monkeypatch, tmp_path)
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "split",
        "session_token": "eyJfdGVzdF8K.fakesig",
        "csrf_token": "abc123xyz",
    })
    assert r.status_code == 201
    # Read the stored file directly and confirm the stitched shape.
    import json
    rows = json.loads(p.read_text())
    assert len(rows) == 1
    cookie = rows[0]["cookie_header"]
    assert cookie == "LEETCODE_SESSION=eyJfdGVzdF8K.fakesig; csrftoken=abc123xyz"


def test_admin_add_split_rejects_missing_csrf(client, monkeypatch, tmp_path):
    """Half-filled split form (session but no csrf) is rejected — partial
    credentials would silently never sign in."""
    _reset_pool(monkeypatch, tmp_path)
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "half", "session_token": "eyJ.fakesig",
    })
    assert r.status_code == 400


def test_admin_add_split_rejects_missing_session(client, monkeypatch, tmp_path):
    _reset_pool(monkeypatch, tmp_path)
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "half", "csrf_token": "abc",
    })
    assert r.status_code == 400


def test_admin_add_legacy_cookie_header_still_works(client, monkeypatch, tmp_path):
    """Pre-existing CLI callers (and tests) that POST cookie_header keep
    working unchanged."""
    _reset_pool(monkeypatch, tmp_path)
    r = client.post("/api/admin/leetcode-accounts", headers=ADMIN, json={
        "name": "legacy",
        "cookie_header": COOKIE_OK,
    })
    assert r.status_code == 201
    d = r.json()
    assert d["total"] == 1
    assert any(a["name"] == "legacy" for a in d["accounts"])
