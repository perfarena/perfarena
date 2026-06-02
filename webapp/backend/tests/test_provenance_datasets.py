"""Transparency log + gated dataset export."""
import json

from conftest import ADMIN, USER, make_submission


def test_provenance_log_chain(client):
    make_submission(client, slugs=("two-sum", "add-digits"))
    r = client.get("/api/provenance/log")
    assert r.status_code == 200
    d = r.json()
    assert d["verified"] is True
    assert d["count"] >= 2
    assert d["genesis"] == "0" * 64
    assert d["entries"], "expected at least one log entry"
    for e in d["entries"]:
        assert {"seq", "leaf_hash", "prev_hash", "entry_hash",
                "logged_at", "model_digest", "sig_status"} <= e.keys()
    # entries come back seq-desc; each links to its predecessor
    entries = d["entries"]
    for newer, older in zip(entries, entries[1:]):
        assert newer["prev_hash"] == older["entry_hash"]

    page = client.get("/api/provenance/log", params={"limit": 1}).json()
    assert len(page["entries"]) == 1


def test_dataset_export_public_and_streams(client):
    """Dataset download is open to the community — anyone can verify the
    leaderboard end-to-end without first asking for an API key."""
    make_submission(client, slugs=("two-sum", "add-digits"))

    # anonymous works
    r = client.get("/api/datasets/solutions")
    assert r.status_code == 200
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert lines, "expected JSONL records"
    rec = json.loads(lines[0])
    assert {"submission_id", "model_name", "language", "code",
            "trace_hash", "model_digest", "problem_slug"} <= rec.keys()

    # filter narrows the stream and stays valid JSONL (still anon)
    filtered = client.get("/api/datasets/solutions",
                           params={"language": "python3", "only_accepted": True})
    assert filtered.status_code == 200
    for ln in filtered.text.splitlines():
        if ln.strip():
            row = json.loads(ln)
            assert row["language"] == "python3"
            assert row["status_msg"] == "Accepted"
