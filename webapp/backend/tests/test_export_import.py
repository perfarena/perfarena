"""Admin export / import (replica merging).

Exports a snapshot of the platform's content, then imports it back into the
same DB to confirm:
  * natural-key tables (users, prompts, …) are idempotent — re-importing
    skips rows that already exist
  * surrogate-key tables (submissions, attempts, runs) re-key on import so
    the same bundle can be replayed without PK collisions
  * the import never blows away admin-curated content
"""
import json

from conftest import ADMIN, GOOD_CODE, TRACE, USER


def test_export_endpoint_lists_tables(client):
    r = client.get("/api/admin/export/tables", headers=ADMIN)
    assert r.status_code == 200
    tables = r.json()["tables"]
    # The dependency order matters for import — parents before children.
    assert "users" in tables
    assert tables.index("users") < tables.index("submissions")
    assert tables.index("submissions") < tables.index("attempts")
    assert tables.index("attempts") < tables.index("runs")


def test_export_requires_admin(client):
    assert client.get("/api/admin/export").status_code == 401
    assert client.get("/api/admin/export", headers=USER).status_code == 403
    assert client.post("/api/admin/import", json={}).status_code == 401


def test_export_json_round_trips(client):
    """End-to-end: submit, export the bundle, then import it back. The
    natural-key tables (users, prompts, problems) are no-ops; submissions/
    attempts/runs all re-key and DOUBLE the existing counts."""
    sid = client.post("/api/submissions", headers=ADMIN, json={
        "model_name": "export-rt-model", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }).json()["submission_id"]

    exp = client.get("/api/admin/export", headers=ADMIN,
                     params={"tables": "users,problems,prompt_templates,"
                                       "submissions,attempts,runs"}).json()
    n_subs_before = exp["counts"]["submissions"]
    assert n_subs_before >= 1
    n_attempts_before = exp["counts"]["attempts"]
    n_runs_before = exp["counts"]["runs"]
    # The submission we just made is in there.
    submission_ids = {s["id"] for s in exp["data"]["submissions"]}
    assert sid in submission_ids

    # Replay the bundle back. Re-keys submissions/attempts/runs, skips users.
    r = client.post("/api/admin/import", headers=ADMIN, json=exp)
    assert r.status_code == 200, r.text
    counts = r.json()["counts"]
    # Natural-key tables: every incoming row already exists locally → all skip.
    assert counts["users"]["inserted"] == 0
    assert counts["users"]["skipped"] >= 1
    assert counts["problems"]["inserted"] == 0
    # Surrogate-key tables: every row got inserted with a fresh id.
    assert counts["submissions"]["inserted"] == n_subs_before
    assert counts["attempts"]["inserted"] == n_attempts_before
    assert counts["runs"]["inserted"] == n_runs_before

    # And a fresh export sees the DOUBLED submission count.
    exp2 = client.get("/api/admin/export", headers=ADMIN,
                      params={"tables": "submissions"}).json()
    assert exp2["counts"]["submissions"] == n_subs_before * 2


def test_unknown_table_400(client):
    r = client.get("/api/admin/export", headers=ADMIN,
                   params={"tables": "submissions,not-a-table"})
    assert r.status_code == 400
    assert "not-a-table" in r.text


def test_csv_zip_export(client):
    """CSV format produces a real zip with one file per requested table
    plus a manifest. We don't round-trip CSV — JSON is the merge format."""
    import io
    import zipfile
    r = client.get("/api/admin/export", headers=ADMIN,
                   params={"tables": "users,problems", "format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert "users.csv" in names
    assert "problems.csv" in names
    assert "manifest.json" in names
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["version"] == 1
    assert set(manifest["tables"]) == {"users", "problems"}


def test_import_with_invalid_body_returns_400(client):
    """Bundle missing the `data` envelope is rejected. (Other malformed
    inputs are tolerated within their respective tables — the import never
    rolls everything back on one bad row.)"""
    r = client.post("/api/admin/import", headers=ADMIN, json={"oops": True})
    assert r.status_code == 400
    assert "data" in r.text.lower()
