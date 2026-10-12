"""Competition abstraction — the platform can host multiple contests, with the
99 LeetCode problems seeded as the default. Membership is many-to-many (a
problem can be in several competitions), and Submissions are NOT FK'd to
competitions, so existing data is preserved when the table is introduced."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE, USER, HARNESS
from app.db import engine
from app.models import Competition, CompetitionProblem, Problem


def test_default_leetcode_competition_seeded(client):
    """The conftest seed creates a 'leetcode-99-perf' competition and links
    every existing Problem row to it. Without that, the platform would
    launch with no competitions and the /competitions page would be empty."""
    r = client.get("/api/competitions")
    assert r.status_code == 200
    by_slug = {c["slug"]: c for c in r.json()}
    assert "leetcode-99-perf" in by_slug
    seed = by_slug["leetcode-99-perf"]
    assert seed["kind"] == "leetcode"
    assert seed["status"] == "active"
    # All seed problems are linked.
    with Session(engine) as s:
        n_problems = len(s.exec(select(Problem)).all())
    assert seed["n_problems"] == n_problems


def test_get_competition_detail_lists_problems_and_languages(client):
    """Detail view returns the full problem set and any languages that have
    competed under it. Submitting any attempt for a member problem makes the
    language show up in the detail."""
    body = {
        "harness_slug": HARNESS,
        "model_name": "comp-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    assert client.post("/api/submissions", headers=ADMIN, json=body).status_code == 202

    d = client.get("/api/competitions/leetcode-99-perf").json()
    assert d["slug"] == "leetcode-99-perf"
    assert d["n_problems"] > 0
    assert any(p["title_slug"] == "two-sum" for p in d["problems"])
    assert "python3" in d["languages"]


def test_get_competition_returns_404_for_unknown_slug(client):
    assert client.get("/api/competitions/no-such-slug").status_code == 404


def test_admin_can_create_edit_delete_competition(client):
    payload = {
        "slug": "humaneval-v1", "name": "HumanEval (v1)",
        "description": "Reference Python coding eval set.", "kind": "humaneval",
    }
    assert client.post("/api/admin/competitions", json=payload).status_code == 401
    assert client.post("/api/admin/competitions", headers=USER, json=payload).status_code == 403

    r = client.post("/api/admin/competitions", headers=ADMIN, json=payload)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["slug"] == "humaneval-v1"

    # appears in the list
    slugs = {c["slug"] for c in client.get("/api/competitions").json()}
    assert "humaneval-v1" in slugs

    # edit
    upd = dict(payload, name="HumanEval reference suite", status="retired")
    e = client.put(f"/api/admin/competitions/{cid}", headers=ADMIN, json=upd)
    assert e.status_code == 200 and e.json()["status"] == "retired"
    # retired hidden by default
    slugs_default = {c["slug"] for c in client.get("/api/competitions").json()}
    assert "humaneval-v1" not in slugs_default
    # but visible with include_retired=true
    slugs_all = {c["slug"] for c in
                 client.get("/api/competitions",
                            params={"include_retired": "true"}).json()}
    assert "humaneval-v1" in slugs_all

    # delete
    d = client.delete(f"/api/admin/competitions/{cid}", headers=ADMIN)
    assert d.status_code == 204
    assert client.get("/api/competitions/humaneval-v1").status_code == 404


def test_admin_can_set_competition_problems(client):
    # Add a fresh competition and assign a known slug to it.
    r = client.post("/api/admin/competitions", headers=ADMIN, json={
        "slug": "easy-only", "name": "Easy-only", "kind": "custom",
    })
    cid = r.json()["id"]

    # Set membership to [two-sum]
    s = client.put(f"/api/admin/competitions/{cid}/problems", headers=ADMIN,
                   json={"problem_slugs": ["two-sum"]})
    assert s.status_code == 200 and s.json()["n_problems"] == 1

    detail = client.get("/api/competitions/easy-only").json()
    assert [p["title_slug"] for p in detail["problems"]] == ["two-sum"]

    # Unknown slug → 400, original membership untouched
    err = client.put(f"/api/admin/competitions/{cid}/problems", headers=ADMIN,
                     json={"problem_slugs": ["does-not-exist"]})
    assert err.status_code == 400
    detail2 = client.get("/api/competitions/easy-only").json()
    assert [p["title_slug"] for p in detail2["problems"]] == ["two-sum"]


def test_duplicate_slug_rejected(client):
    """Slug is the URL-stable identifier — must be unique."""
    payload = {"slug": "leetcode-99-perf", "name": "x", "kind": "leetcode"}
    r = client.post("/api/admin/competitions", headers=ADMIN, json=payload)
    assert r.status_code == 409


def test_rankings_competition_filter_returns_404_for_unknown_slug(client):
    r = client.get("/api/rankings", params={"competition": "no-such"})
    assert r.status_code == 404


def test_rankings_competition_filter_scopes_to_membership(client):
    """Filter by competition: only submissions touching that competition's
    problems make the leaderboard. The seed LC-99 competition contains every
    problem so it's a no-op; an empty competition leaves the leaderboard
    empty regardless of how many submissions exist platform-wide."""
    # Submit something so the global leaderboard has at least one row.
    body = {
        "harness_slug": HARNESS,
        "model_name": "rank-filter-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1111111111",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE},
                     {"title_slug": "add-digits", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    assert client.post("/api/submissions", headers=ADMIN, json=body).status_code == 202

    # Default LC-99 includes every problem → identical to no filter.
    no_filter = client.get("/api/rankings").json()
    seed_only = client.get("/api/rankings",
                           params={"competition": "leetcode-99-perf"}).json()
    # Same models in both lists (set comparison guards against re-ordering).
    assert {(r["model_name"], r["model_version"]) for r in no_filter} \
        == {(r["model_name"], r["model_version"]) for r in seed_only}

    # An empty competition leaves the leaderboard empty.
    cr = client.post("/api/admin/competitions", headers=ADMIN, json={
        "slug": "empty-comp", "name": "Empty", "kind": "custom",
    })
    cid = cr.json()["id"]
    # No problems linked.
    empty = client.get("/api/rankings",
                       params={"competition": "empty-comp"}).json()
    assert empty == []
    # cleanup
    client.delete(f"/api/admin/competitions/{cid}", headers=ADMIN)


def test_existing_submissions_preserved_on_seed(client):
    """Smoke test for 'additive — preserve existing data'. Submit, then
    confirm the data is still there after a hypothetical 're-seed' run
    (the seed function is idempotent)."""
    raw = client.get("/api/submissions").json()
    sub_count_before = len(raw) if isinstance(raw, list) else len(raw.get("items", raw))
    body = {
        "harness_slug": HARNESS,
        "model_name": "preserve-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 9999999999",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    client.post("/api/submissions", headers=ADMIN, json=body)
    # re-run the seed; should be a no-op.
    from app.main import _seed_default_competitions
    _seed_default_competitions()
    raw2 = client.get("/api/submissions").json()
    sub_count_after = len(raw2) if isinstance(raw2, list) else len(raw2.get("items", raw2))
    assert sub_count_after == sub_count_before + 1


# ---------- /api/problems competition awareness ----------------------------

def test_problems_listing_includes_competitions(client):
    """Every problem row carries the competitions it belongs to. The seed
    creates `leetcode-99-perf` and links every problem to it, so the
    field is non-empty by default."""
    rows = client.get("/api/problems").json()
    assert rows, "seed should produce problems"
    for r in rows:
        assert "competitions" in r, f"missing 'competitions' on {r['title_slug']!r}"
        assert isinstance(r["competitions"], list)
        # Each ref has just slug + name — light pointer, not the full
        # CompetitionPublic shape with stats.
        for c in r["competitions"]:
            assert set(c) == {"slug", "name"}
    # Default seed must link every problem to leetcode-99-perf.
    assert all(
        any(c["slug"] == "leetcode-99-perf" for c in r["competitions"])
        for r in rows
    )


def test_problems_listing_filters_by_competition_slug(client):
    """`?competition=<slug>` filters to that competition's membership. We
    create a second 5-problem competition and verify the filter cuts the
    listing down accordingly."""
    # Build a small 5-problem competition off the existing seed.
    with Session(engine) as s:
        seed_problems = s.exec(select(Problem)).all()[:5]
        comp = Competition(slug="filter-test", name="Filter Test",
                           kind="other", status="active")
        s.add(comp); s.commit(); s.refresh(comp)
        for p in seed_problems:
            s.add(CompetitionProblem(competition_id=comp.id, problem_id=p.id))
        s.commit()
        seed_slugs = {p.title_slug for p in seed_problems}

    rows = client.get("/api/problems?competition=filter-test").json()
    assert {r["title_slug"] for r in rows} == seed_slugs
    # And every returned row claims the new competition in its memberships.
    for r in rows:
        assert any(c["slug"] == "filter-test" for c in r["competitions"])


def test_problems_listing_unknown_competition_returns_404(client):
    """A typo'd `?competition=` is a hard fail — silently returning the
    full listing would be worse than the URL bar making it obvious."""
    r = client.get("/api/problems?competition=does-not-exist")
    assert r.status_code == 404


def test_problems_listing_filter_intersects_with_level(client):
    """`?level=` and `?competition=` compose; result is the intersection."""
    rows = client.get("/api/problems?competition=leetcode-99-perf&level=Easy").json()
    assert rows, "default seed should have Easy problems in the 99-perf comp"
    assert all(r["level"] == "Easy" for r in rows)
