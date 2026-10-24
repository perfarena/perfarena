"""Per-competition tie margins of the efficiency rankings
(Competition.ranking_tie_pct over the global ranking.tie_*_pct settings)."""
from conftest import ADMIN

COMP = "margins-test"


def _tie_pct(client):
    r = client.get("/api/efficiency-rankings", params={"competition": COMP, "bootstrap": False})
    assert r.status_code == 200, r.text
    return {m: v["tie_pct"] for m, v in r.json()["metrics"].items()}


def test_competition_margins_override_the_global_ones(client):
    r = client.post("/api/admin/competitions", headers=ADMIN,
                    json={"slug": COMP, "name": "Margins test",
                          "ranking_tie_pct": {"latency": 3.68, "energy": 3.68}})
    assert r.status_code in (200, 201), r.text
    cid = r.json()["id"]
    assert _tie_pct(client) == {"latency": 3.68, "energy": 3.68, "memory": 9.83}

    # An update that leaves the margins out keeps them.
    r = client.put(f"/api/admin/competitions/{cid}", headers=ADMIN,
                   json={"slug": COMP, "name": "Margins test, renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["ranking_tie_pct"] == {"latency": 3.68, "energy": 3.68}
    assert _tie_pct(client)["latency"] == 3.68

    # An explicit null returns the competition to the global margins.
    r = client.put(f"/api/admin/competitions/{cid}", headers=ADMIN,
                   json={"slug": COMP, "name": "Margins test", "ranking_tie_pct": None})
    assert r.status_code == 200, r.text
    assert _tie_pct(client) == {"latency": 1.84, "energy": 1.84, "memory": 9.83}


def test_unknown_metric_or_out_of_range_margin_is_rejected(client):
    for bad in ({"speed": 2.0}, {"latency": 80.0}):
        r = client.post("/api/admin/competitions", headers=ADMIN,
                        json={"slug": f"{COMP}-bad", "name": "Bad", "ranking_tie_pct": bad})
        assert r.status_code == 422, r.text
