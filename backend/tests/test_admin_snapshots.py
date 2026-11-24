"""Weekly snapshot capture (admin), baseline bulk upsert, + public read-back."""
from conftest import ADMIN, USER, make_submission


def test_admin_baselines_upsert(client):
    payload = {"baselines": [
        {"title_slug": "two-sum", "language": "python3",
         "runtime_ms_avg": 80.0, "runtime_ms_expert": 32.0,
         "memory_mb_avg": 17.1, "memory_mb_expert": 15.9,
         "source": "leetcode-distribution"},
        {"title_slug": "ghost-problem", "language": "python3",
         "runtime_ms_avg": 1, "runtime_ms_expert": 1,
         "memory_mb_avg": 1, "memory_mb_expert": 1},
    ]}
    assert client.post("/api/admin/baselines", json=payload).status_code == 401
    assert client.post("/api/admin/baselines", headers=USER, json=payload).status_code == 403

    r = client.post("/api/admin/baselines", headers=ADMIN, json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["upserted"] == 1
    assert body["skipped"] == ["ghost-problem/python3"]

    # idempotent: a second call updates in place, doesn't duplicate
    r2 = client.post("/api/admin/baselines", headers=ADMIN, json={
        "baselines": [payload["baselines"][0] | {"runtime_ms_avg": 75.0}]
    })
    assert r2.status_code == 200 and r2.json()["upserted"] == 1


def test_snapshot_capture_and_read(client):
    make_submission(client, slugs=("two-sum", "add-digits"))

    assert client.post("/api/admin/snapshots").status_code == 401
    assert client.post("/api/admin/snapshots", headers=USER).status_code == 403

    created = client.post("/api/admin/snapshots", headers=ADMIN,
                          params={"label": "test-week"})
    assert created.status_code == 201
    snap = created.json()
    assert snap["label"] == "test-week" and "id" in snap

    listed = client.get("/api/snapshots").json()
    assert any(s["id"] == snap["id"] for s in listed)

    detail = client.get(f"/api/snapshots/{snap['id']}")
    assert detail.status_code == 200
    rep = detail.json()
    assert rep["id"] == snap["id"]
    assert "report" in rep and "totals" in rep["report"]
