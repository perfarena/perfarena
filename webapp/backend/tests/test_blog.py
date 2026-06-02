"""Blog endpoints — admin CRUD + public list/detail.

Public `/api/blog` returns only published posts; drafts are admin-only via
`/api/admin/blog`. `published_at` is stamped the first time a post flips to
`published` and then stays put across later edits."""
from conftest import ADMIN, USER


def test_public_list_excludes_drafts(client):
    # Draft → not visible publicly
    client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "blog-draft", "title": "Draft post",
        "summary": "still cooking", "body": "draft body",
        "status": "draft",
    })
    listed = client.get("/api/blog").json()
    slugs = {p["slug"] for p in listed}
    assert "blog-draft" not in slugs


def test_admin_can_create_publish_and_public_can_read(client):
    r = client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "first-post", "title": "First post",
        "summary": "Hello world.",
        "body": "# Hello\nFull markdown body.",
        "cover_image_url": "/img/cover-first.png",
        "author_name": "PerfArena team",
        "status": "published",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "published"
    assert body["published_at"] is not None     # stamped on first publish

    # Public list shows the card
    listed = client.get("/api/blog").json()
    card = next(p for p in listed if p["slug"] == "first-post")
    assert card["title"] == "First post"
    assert card["cover_image_url"] == "/img/cover-first.png"
    # Card schema omits the full body
    assert "body" not in card

    # Public detail returns the full body
    detail = client.get("/api/blog/first-post").json()
    assert detail["body"].startswith("# Hello")


def test_unknown_or_draft_slug_returns_404_publicly(client):
    assert client.get("/api/blog/no-such").status_code == 404
    client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "draft-only", "title": "x", "body": "x", "status": "draft",
    })
    assert client.get("/api/blog/draft-only").status_code == 404


def test_admin_list_shows_all_statuses(client):
    """Admin endpoint reveals drafts the public list hides."""
    client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "admin-only-draft", "title": "Internal",
        "body": "x", "status": "draft",
    })
    r = client.get("/api/admin/blog", headers=ADMIN)
    assert r.status_code == 200
    slugs = {p["slug"] for p in r.json()}
    assert "admin-only-draft" in slugs


def test_edit_first_publish_stamps_published_at(client):
    r = client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "later-publish", "title": "Later",
        "body": "x", "status": "draft",
    })
    pid = r.json()["id"]
    assert r.json()["published_at"] is None

    e = client.put(f"/api/admin/blog/{pid}", headers=ADMIN, json={
        "slug": "later-publish", "title": "Later",
        "body": "x", "status": "published",
    })
    assert e.status_code == 200
    first_ts = e.json()["published_at"]
    assert first_ts is not None

    # A later edit doesn't move the published_at timestamp.
    e2 = client.put(f"/api/admin/blog/{pid}", headers=ADMIN, json={
        "slug": "later-publish", "title": "Later (revised)",
        "body": "y", "status": "published",
    })
    assert e2.json()["published_at"] == first_ts
    assert e2.json()["title"] == "Later (revised)"


def test_auth_required_for_admin_routes(client):
    payload = {"slug": "auth-test", "title": "x", "body": "x"}
    assert client.post("/api/admin/blog", json=payload).status_code == 401
    assert client.post("/api/admin/blog", headers=USER, json=payload).status_code == 403
    assert client.get("/api/admin/blog").status_code == 401
    assert client.get("/api/admin/blog", headers=USER).status_code == 403


def test_duplicate_slug_rejected(client):
    client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "dup", "title": "first", "body": "x",
    })
    r = client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "dup", "title": "second", "body": "x",
    })
    assert r.status_code == 409


def test_admin_can_delete_post(client):
    r = client.post("/api/admin/blog", headers=ADMIN, json={
        "slug": "to-delete", "title": "x", "body": "x", "status": "published",
    })
    pid = r.json()["id"]
    d = client.delete(f"/api/admin/blog/{pid}", headers=ADMIN)
    assert d.status_code == 204
    assert client.get("/api/blog/to-delete").status_code == 404
