"""Public, unauthenticated endpoints."""


def test_list_problems(client):
    r = client.get("/api/problems")
    assert r.status_code == 200
    data = r.json()
    slugs = {p["title_slug"] for p in data}
    assert {"two-sum", "24-game"} <= slugs
    for p in data:
        assert {"id", "title_slug", "title", "level", "tags"} <= p.keys()
        assert isinstance(p["tags"], list)


def test_list_problems_filtered(client):
    easy = client.get("/api/problems", params={"level": "Easy"}).json()
    assert easy and all(p["level"] == "Easy" for p in easy)
    tagged = client.get("/api/problems", params={"tag": "Math"}).json()
    assert all("Math" in p["tags"] for p in tagged)


def test_problem_detail_ok_and_404(client):
    r = client.get("/api/problems/two-sum")
    assert r.status_code == 200
    d = r.json()
    assert d["title_slug"] == "two-sum"
    assert d["content"]                       # statement is public
    assert {"testcases", "n_attempts", "n_accepted", "top_solutions"} <= d.keys()
    assert client.get("/api/problems/nope-not-real").status_code == 404


def test_rankings(client):
    r = client.get("/api/rankings")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert client.get("/api/rankings", params={"language": "python3",
                                               "sort": "pass_at_1"}).status_code == 200


def test_languages(client):
    r = client.get("/api/languages")
    assert r.status_code == 200
    langs = r.json()
    assert langs and all("name" in x and "category" in x for x in langs)
    name = langs[0]["name"]
    assert client.get(f"/api/languages/{name}").status_code == 200
    assert client.get("/api/languages/not-a-language").status_code == 404


def test_captcha_shape(client):
    c = client.get("/api/captcha").json()
    assert c["token"] and c["prompt"].startswith("What is ")


def test_snapshots_list_public(client):
    r = client.get("/api/snapshots")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert client.get("/api/snapshots/999999").status_code == 404


def test_submissions_list_public(client):
    r = client.get("/api/submissions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
