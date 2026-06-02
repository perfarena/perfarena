"""/models = every model that ever submitted answers.

A submission for a brand-new (model_name, model_version) auto-creates a
RegisteredModel stub (status='unverified') so /models can list it. The list
endpoint returns rich activity stats per row so the page surfaces submission
counts, languages, and freshness without N+1 round-trips."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE
from app.db import engine
from app.models import RegisteredModel


def _submit(client, name, version, language="python3"):
    body = {
        "model_name": name, "model_version": version,
        "language": language, "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def _registered(name, version):
    with Session(engine) as s:
        return s.exec(
            select(RegisteredModel)
            .where(RegisteredModel.name == name,
                   RegisteredModel.version == version)
        ).first()


def test_first_submission_autostubs_registered_model(client):
    name, ver = "auto-stub-model", "v1"
    assert _registered(name, ver) is None, "precondition: not yet registered"
    _submit(client, name, ver)
    rm = _registered(name, ver)
    assert rm is not None, "stub should be auto-created on first submission"
    assert rm.status == "unverified"
    # Rich-metadata fields start blank — admin fills them in via the form.
    assert rm.description is None and rm.model_type is None
    assert rm.family is None and rm.provenance is None


def test_second_submission_does_not_duplicate_stub(client):
    name, ver = "dedupe-model", "v1"
    _submit(client, name, ver)
    _submit(client, name, ver)
    with Session(engine) as s:
        rows = s.exec(
            select(RegisteredModel)
            .where(RegisteredModel.name == name,
                   RegisteredModel.version == version_eq(ver))
        ).all()
    assert len(rows) == 1


def version_eq(v):
    """Tiny helper: SQLModel needs the literal here for clarity; this is just a
    pass-through so the where() reads naturally."""
    return v


def test_list_endpoint_includes_unverified_by_default(client):
    """Auto-stubbed rows must appear in the default list (the page's whole
    purpose is to show every model that submitted)."""
    _submit(client, "list-shown-model", "v1")
    rows = client.get("/api/registered-models").json()
    names = {(r["name"], r["version"]) for r in rows}
    assert ("list-shown-model", "v1") in names
    row = next(r for r in rows if r["name"] == "list-shown-model")
    assert row["status"] == "unverified"


def test_list_endpoint_attaches_activity_stats(client):
    _submit(client, "stats-model", "v1", language="python3")
    _submit(client, "stats-model", "v1", language="cpp")
    rows = client.get("/api/registered-models").json()
    row = next(r for r in rows if r["name"] == "stats-model")
    assert row["n_submissions"] == 2
    # Both submissions go through the fake dispatcher synchronously, so both
    # land judged before this list call returns.
    assert row["n_judged"] == 2
    assert sorted(row["languages"]) == ["cpp", "python3"]
    assert row["last_submitted_at"] is not None
    # The rich-metadata fields surface in the overview too (initially null).
    for k in ("description", "model_type", "family", "provenance"):
        assert k in row, f"overview row missing {k!r}"


def test_backfill_creates_stubs_for_preexisting_submissions(client):
    """Pre-feature submissions don't have stubs. The startup backfill walks
    the Submission table once and creates a row for every distinct
    (model_name, model_version) that lacks one — so existing data (e.g.
    historical qwen runs) surfaces on /models without re-submitting."""
    from app.main import _backfill_registered_model_stubs_from_submissions
    from app.db import engine as _engine
    from app.models import Submission as _S

    name, ver = "preexisting-qwen", "v-historic"
    # Simulate a "pre-feature" submission: stuff a Submission row directly
    # into the DB without going through POST /api/submissions, so the
    # auto-stub on-ingest path never fires.
    with Session(_engine) as s:
        s.add(_S(user_id=1, model_name=name, model_version=ver,
                 language="python3", prompt_template="x" * 30,
                 model_params={"temperature": 0.0}, total_problems=1))
        s.commit()
    assert _registered(name, ver) is None, "precondition: no stub yet"

    _backfill_registered_model_stubs_from_submissions()
    rm = _registered(name, ver)
    assert rm is not None
    assert rm.status == "unverified"

    # Re-running is a no-op (no duplicate rows).
    _backfill_registered_model_stubs_from_submissions()
    with Session(_engine) as s:
        rows = s.exec(select(RegisteredModel).where(
            RegisteredModel.name == name, RegisteredModel.version == ver,
        )).all()
    assert len(rows) == 1


def test_admin_can_edit_metadata_on_stub(client):
    """The whole point of the auto-stub: admin clicks edit on /models, fills
    in description / type / family / provenance, saves → status flips to
    active and the rich metadata sticks."""
    _submit(client, "edit-stub-model", "v1")
    rm = _registered("edit-stub-model", "v1")
    update = {
        "name": "edit-stub-model", "version": "v1", "provider": "ollama",
        "description": "Qwen 2.5 Coder fine-tune.",
        "model_type": "coder", "family": "qwen",
        "provenance": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B",
        "status": "active",
    }
    r = client.put(f"/api/admin/registered-models/{rm.id}",
                   headers=ADMIN, json=update)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["description"] == "Qwen 2.5 Coder fine-tune."
    assert body["family"] == "qwen" and body["model_type"] == "coder"
    assert "huggingface" in body["provenance"]
