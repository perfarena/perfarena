"""HTTP surface for replica pools, machine classes, calibration, and the
queue view; plus export/import of the new columns."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import dispatch
from app.db import engine
from app.export_import import import_bundle
from app.main import app
from app.models import (
    Attempt, Harness, Machine, MachineClass, OracleReplica, Run,
)
from replica_fakes import (  # noqa: F401 — fixtures
    add_replica, admin_headers, make_oracles, make_submission, primary_replica,
    real_mode, runners, uid,
)

client = TestClient(app)


def _slug(hid: int) -> str:
    with Session(engine) as s:
        return s.get(Harness, hid).slug


def test_replica_crud_and_public_listing(real_mode, runners, admin_headers):
    a, b = runners("a"), runners("b")
    hid, _ = make_oracles(a)
    slug = _slug(hid)
    r = client.post("/api/admin/replicas", headers=admin_headers, json={
        "harness_slug": slug, "name": "b", "endpoint": b.url + "/",
        "auth_token": "s3cret", "capacity": 2})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["endpoint"] == b.url and r.json()["has_auth_token"] is True
    assert "auth_token" not in r.json()

    pool = client.get(f"/api/harnesses/{slug}/replicas").json()
    assert [x["is_primary"] for x in pool] == [True, False]
    assert "s3cret" not in str(pool)

    r = client.put(f"/api/admin/replicas/{rid}", headers=admin_headers,
                   json={"weight": 3, "status": "disabled"})
    assert r.status_code == 200 and r.json()["weight"] == 3
    assert r.json()["status"] == "disabled" and r.json()["capacity"] == 2
    assert client.put(f"/api/admin/replicas/{rid}", headers=admin_headers,
                      json={"status": "bogus"}).status_code == 400

    prim = pool[0]["id"]
    assert client.delete(f"/api/admin/replicas/{prim}",
                         headers=admin_headers).status_code == 400
    assert client.put(f"/api/admin/replicas/{prim}", headers=admin_headers,
                      json={"endpoint": "http://x"}).status_code == 400

    with Session(engine) as s:                    # a run names the replica
        sid, (aid,) = make_submission(hid)
        s.add(Run(attempt_id=aid, status_msg="Accepted", replica_id=rid))
        s.commit()
    assert client.delete(f"/api/admin/replicas/{rid}",
                         headers=admin_headers).status_code == 409


def test_replica_needs_exactly_one_runner_oracle(admin_headers, runners):
    a = runners("a")
    hid, _ = make_oracles(a)
    body = {"name": "x", "endpoint": "http://127.0.0.1:1"}
    assert client.post("/api/admin/replicas", headers=admin_headers,
                       json=body).status_code == 400
    assert client.post("/api/admin/replicas", headers=admin_headers, json={
        **body, "harness_slug": "leetcode"}).status_code == 400   # no runner
    assert client.post("/api/admin/replicas", json={
        **body, "harness_slug": _slug(hid)}).status_code == 401   # admin only


def test_probe_records_health(real_mode, runners, admin_headers):
    a, b = runners("a"), runners("b")
    hid, _ = make_oracles(a)
    rid = add_replica(b, harness_id=hid)
    b.fail = True
    r = client.post(f"/api/admin/replicas/{rid}/probe", headers=admin_headers)
    assert r.status_code == 200 and r.json()["ok"] is False
    with Session(engine) as s:
        row = s.get(OracleReplica, rid)
        assert row.last_health_ok is False and row.last_health_at is not None


def test_harness_config_policy_is_validated(admin_headers, runners):
    a = runners("a")
    body = {"slug": f"h-{uid()}", "name": "h", "kind": "arena-local",
            "config": {"endpoint": a.url, "replicas": {"policy": "quorum"}}}
    assert client.post("/api/admin/harnesses", headers=admin_headers,
                       json=body).status_code == 400
    body["config"]["replicas"]["policy"] = "split_repeats"
    r = client.post("/api/admin/harnesses", headers=admin_headers, json=body)
    assert r.status_code == 201, r.text
    pool = client.get(f"/api/harnesses/{body['slug']}/replicas").json()
    assert len(pool) == 1 and pool[0]["endpoint"] == a.url   # primary seeded
    oracle = {"slug": f"o-{uid()}", "name": "o", "kind": "arena-local",
              "config": {"endpoint": a.url, "replicas": {"policy": "split_repeats"}}}
    assert client.post("/api/admin/correctness-oracles", headers=admin_headers,
                       json=oracle).status_code == 400


def test_machine_class_api_and_assignment(admin_headers):
    slug = f"cls-{uid()}"
    r = client.post("/api/admin/machine-classes", headers=admin_headers, json={
        "slug": slug, "name": "c", "spec": {"cpu": "x"},
        "calibration": {"attempt_id": 1, "repeats": 3, "tolerance_pct": 5}})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.post("/api/admin/machine-classes", headers=admin_headers, json={
        "slug": slug, "name": "dup"}).status_code == 409
    with Session(engine) as s:
        m = Machine(name=f"m-{uid()}")
        s.add(m)
        s.commit()
        mid = m.id
        rep = OracleReplica(name="r", endpoint="http://127.0.0.1:9", machine_id=mid,
                            admitted_class_id=cid)
        s.add(rep)
        s.commit()
        rep_id = rep.id
    r = client.put(f"/api/admin/machines/{mid}/class", headers=admin_headers,
                   json={"machine_class_slug": slug})
    assert r.status_code == 200 and r.json()["machine_class_id"] == cid
    listed = {c["slug"]: c for c in client.get("/api/machine-classes").json()}
    assert mid in listed[slug]["machine_ids"]
    machines = {m["id"]: m for m in client.get("/api/machines").json()}
    assert machines[mid]["machine_class_id"] == cid
    # Clearing the class revokes the admission of replicas on that machine.
    client.put(f"/api/admin/machines/{mid}/class", headers=admin_headers,
               json={"machine_class_slug": None})
    with Session(engine) as s:
        assert s.get(OracleReplica, rep_id).admitted_class_id is None


def test_calibrate_endpoint_runs_in_background(real_mode, runners, admin_headers):
    a = runners("a")
    hid, _ = make_oracles(a)
    _, (ref,) = make_submission(hid)
    slug = f"cls-{uid()}"
    client.post("/api/admin/machine-classes", headers=admin_headers, json={
        "slug": slug, "name": "c",
        "calibration": {"attempt_id": ref, "repeats": 3, "tolerance_pct": 5}})
    rid = primary_replica(harness_id=hid)
    r = client.post(f"/api/admin/replicas/{rid}/calibrate", headers=admin_headers,
                    json={"machine_class_slug": slug})
    assert r.status_code == 202
    end = time.monotonic() + 10
    rows = []
    while time.monotonic() < end and not rows:
        rows = client.get(f"/api/admin/replicas/{rid}/calibrations",
                          headers=admin_headers).json()
        time.sleep(0.1)
    assert rows and rows[0]["passed"] and rows[0]["became_baseline"]


def test_inconsistent_listing_and_queue_view(real_mode, runners, admin_headers):
    perf, a = runners("perf"), runners("a")
    b = runners("b", verdict="Time Limit Exceeded")
    hid, oid = make_oracles(perf, correctness=a,
                            oracle_policy={"policy": "broadcast_merge"})
    add_replica(b, oracle_id=oid)
    sid, (aid,) = make_submission(hid, oid)
    dispatch.dispatch_submission(sid)
    out = client.get("/api/admin/replicas/inconsistent", headers=admin_headers).json()
    mine = [x for x in out["attempts"] if x["attempt_id"] == aid]
    assert mine and sorted(v["status_msg"] for v in mine[0]["votes"]) == [
        "Accepted", "Time Limit Exceeded"]
    q = client.get("/api/admin/queue", headers=admin_headers).json()
    assert q["enabled"] is False and "states" in q


def test_import_remaps_classes_and_clears_live_state():
    cls_slug = f"cls-{uid()}"
    bundle = {"data": {
        "machine_classes": [{"id": 900, "slug": cls_slug, "name": "c"}],
        "machines": [{"id": 901, "name": f"m-{uid()}", "machine_class_id": 900}],
        "users": [{"id": 902, "email": f"{uid()}@x.dev", "display_name": "u",
                   "role": "user", "status": "approved"}],
        "submissions": [{"id": 903, "user_id": 902, "model_name": "m",
                         "model_version": "v", "language": "python3",
                         "prompt_template": "p", "model_params": {},
                         "machine_class_id": 900}],
        "attempts": [{"id": 904, "submission_id": 903, "problem_id": 1,
                      "code": "x", "inference_trace": "t",
                      "dispatch_state": "claimed", "claimed_by": "w"}],
        "runs": [{"id": 905, "attempt_id": 904, "status_msg": "Accepted",
                  "replica_id": 77, "machine_id": 901}],
    }}
    with Session(engine) as s:
        report = import_bundle(s, bundle)
        assert report["counts"]["machine_classes"]["inserted"] == 1
        assert report["counts"]["runs"]["inserted"] == 1
        mc = s.exec(select(MachineClass).where(MachineClass.slug == cls_slug)).one()
        run = s.exec(select(Run).order_by(Run.id.desc())).first()
        m = s.get(Machine, run.machine_id)
        att = s.get(Attempt, run.attempt_id)
        assert m.machine_class_id == mc.id and run.replica_id is None
        assert att.dispatch_state is None and att.claimed_by is None
