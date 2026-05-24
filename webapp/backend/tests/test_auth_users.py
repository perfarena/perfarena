"""Auth, registration, and admin user management."""
from conftest import ADMIN, USER, solve_captcha


def test_me_requires_auth(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer bogus"}).status_code == 401
    me = client.get("/api/me", headers=ADMIN)
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_admin_only_endpoints_reject_plain_user(client):
    assert client.get("/api/admin/pending").status_code == 401
    assert client.get("/api/admin/pending", headers=USER).status_code == 403
    assert client.get("/api/admin/pending", headers=ADMIN).status_code == 200


def test_register_flow_and_captcha(client):
    # missing captcha
    bad = client.post("/api/register", json={
        "email": "x@test.dev", "display_name": "X",
        "captcha_token": "", "captcha_answer": "",
    })
    assert bad.status_code == 400

    # wrong answer
    tok, _ = solve_captcha(client)
    wrong = client.post("/api/register", json={
        "email": "x@test.dev", "display_name": "X",
        "captcha_token": tok, "captcha_answer": "-9999",
    })
    assert wrong.status_code == 400

    # correct
    tok, ans = solve_captcha(client)
    ok = client.post("/api/register", json={
        "email": "newcomer@test.dev", "display_name": "Newcomer",
        "org": "Org", "motivation": "compete",
        "captcha_token": tok, "captcha_answer": ans,
    })
    assert ok.status_code == 201
    assert ok.json()["status"] == "pending"

    # duplicate email -> 409
    tok, ans = solve_captcha(client)
    dup = client.post("/api/register", json={
        "email": "newcomer@test.dev", "display_name": "Dup",
        "captcha_token": tok, "captcha_answer": ans,
    })
    assert dup.status_code == 409


def test_register_with_password_then_login(client):
    tok, ans = solve_captcha(client)
    r = client.post("/api/register", json={
        "email": "logmein@test.dev", "display_name": "Logger",
        "password": "correct-horse-battery",
        "captcha_token": tok, "captcha_answer": ans,
    })
    assert r.status_code == 201

    # not yet approved → 403
    pre = client.post("/api/login", json={
        "email": "logmein@test.dev", "password": "correct-horse-battery"})
    assert pre.status_code == 403

    # admin approves
    from sqlmodel import Session, select
    from app.db import engine
    from app.models import User
    with Session(engine) as s:
        u = s.exec(select(User).where(User.email == "logmein@test.dev")).first()
        uid = u.id
    appr = client.post(f"/api/admin/users/{uid}/approve", headers=ADMIN)
    assert appr.status_code == 200

    # wrong password
    bad = client.post("/api/login", json={
        "email": "logmein@test.dev", "password": "nope"})
    assert bad.status_code == 401

    # correct password returns the api key
    ok = client.post("/api/login", json={
        "email": "logmein@test.dev", "password": "correct-horse-battery"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["email"] == "logmein@test.dev"
    assert body["api_key"].startswith("arena_")
    # that key now authenticates
    me = client.get("/api/me", headers={"Authorization": f"Bearer {body['api_key']}"})
    assert me.status_code == 200

    # unknown email
    miss = client.post("/api/login", json={
        "email": "ghost@test.dev", "password": "whatever"})
    assert miss.status_code == 401


def test_admin_approve_and_reject(client, make_pending_user):
    pid = make_pending_user("approve-me@test.dev")
    pending = client.get("/api/admin/pending", headers=ADMIN).json()
    assert any(u["id"] == pid for u in pending)

    res = client.post(f"/api/admin/users/{pid}/approve", headers=ADMIN)
    assert res.status_code == 200
    new_key = res.json()["api_key"]
    assert new_key.startswith("arena_")
    # the freshly approved key now authenticates
    assert client.get("/api/me", headers={"Authorization": f"Bearer {new_key}"}).status_code == 200

    rid = make_pending_user("reject-me@test.dev")
    rej = client.post(f"/api/admin/users/{rid}/reject", headers=ADMIN)
    assert rej.status_code == 200 and rej.json()["status"] == "rejected"

    assert client.post("/api/admin/users/99999/approve", headers=ADMIN).status_code == 404
    assert client.post(f"/api/admin/users/{pid}/approve", headers=USER).status_code == 403
