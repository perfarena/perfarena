"""Cost estimator endpoint — returns observed token-total ranges from
already-completed full sweeps, scoped by prompt / language / model /
competition. The frontend calculator multiplies these by user-supplied
$-per-1M rates to produce a cost envelope.

These tests live separate from test_competitions.py because the data
they manufacture (trace dicts with provider_meta.usage) doesn't fit any
of the existing fixtures."""
from conftest import ADMIN, GOOD_CODE, TRACE
from app.db import engine
from app.models import Attempt, Submission
from sqlmodel import Session, select


def _submit_minimal(client, *, model="cost-test", language="python3",
                    trace_extras=None):
    """Build a single-problem submission so we can poke at the resulting
    attempt's trace from inside the test."""
    body = {
        "model_name": model, "model_version": "v1",
        "language": language,
        "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{
            "title_slug": "two-sum", "code": GOOD_CODE,
            "inference_trace": TRACE,
            "trace": {
                "model": model, "provider": "openai",
                **(trace_extras or {}),
            },
        }],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def test_cost_estimator_empty_returns_metadata_only(client):
    """Even with zero submissions the endpoint must return the
    available-filter universes so the calculator page can render."""
    r = client.get("/api/cost-estimator-stats")
    assert r.status_code == 200
    d = r.json()
    for k in ("available_prompts", "available_languages",
              "available_models", "available_competitions",
              "submissions", "input_tokens", "output_tokens"):
        assert k in d, f"missing {k!r}"
    assert isinstance(d["available_competitions"], list)
    # leetcode-99-perf is seeded by conftest, so it's always there.
    assert any(c["slug"] == "leetcode-99-perf" for c in d["available_competitions"])


def test_cost_estimator_extracts_openai_tokens(client):
    """A submission whose attempt carries trace.provider_meta.usage must
    show up with the parsed prompt/completion totals."""
    sid = _submit_minimal(client, trace_extras={
        "provider_meta": {"usage": {"prompt_tokens": 1234, "completion_tokens": 567}},
    })
    d = client.get("/api/cost-estimator-stats").json()
    row = next(s for s in d["submissions"] if s["submission_id"] == sid)
    assert row["total_input_tokens"] == 1234
    assert row["total_output_tokens"] == 567
    assert row["n_attempts_with_token_data"] == 1


def test_cost_estimator_extracts_ollama_tokens(client):
    """Ollama uses prompt_eval_count + eval_count on provider_meta itself,
    NOT a nested usage object. Both shapes must round-trip."""
    sid = _submit_minimal(client, model="ollama-test", trace_extras={
        "provider": "ollama",
        "provider_meta": {"prompt_eval_count": 999, "eval_count": 222},
    })
    d = client.get(
        "/api/cost-estimator-stats?model_name=ollama-test"
    ).json()
    row = next(s for s in d["submissions"] if s["submission_id"] == sid)
    assert row["total_input_tokens"] == 999
    assert row["total_output_tokens"] == 222


def test_cost_estimator_excludes_partial_sweeps_from_range(client):
    """A 1-problem submission is NOT a full sweep against the default
    99-problem competition, so it must NOT widen the min/max envelope
    (would lie to the user about expected cost). It still appears in
    the submissions list, just flagged is_full_sweep=false."""
    sid = _submit_minimal(client, trace_extras={
        "provider_meta": {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    })
    d = client.get("/api/cost-estimator-stats").json()
    row = next(s for s in d["submissions"] if s["submission_id"] == sid)
    assert row["is_full_sweep"] is False
    # The submission's tokens are 1+1; if they leaked into the envelope,
    # the input_tokens.n would be at least 1.
    assert d["input_tokens"]["n"] == 0
    assert d["input_tokens"]["min"] is None


def test_cost_estimator_filters_compose(client):
    """language + model + competition filters intersect; passing all three
    narrows to the intersection without crashing."""
    _submit_minimal(client, model="filter-a", language="python3",
                    trace_extras={"provider_meta": {
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}})
    _submit_minimal(client, model="filter-b", language="cpp",
                    trace_extras={"provider_meta": {
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10}}})
    d = client.get(
        "/api/cost-estimator-stats"
        "?model_name=filter-a&language=python3&competition=leetcode-99-perf"
    ).json()
    assert all(s["model_name"] == "filter-a" for s in d["submissions"])
    assert all(s["language"] == "python3" for s in d["submissions"])


def test_cost_estimator_unknown_competition_is_404(client):
    """Typo'd competition slug fails loud, not silently returns everything."""
    r = client.get("/api/cost-estimator-stats?competition=does-not-exist")
    assert r.status_code == 404


def test_cost_estimator_envelope_min_max_median(client):
    """When MULTIPLE full sweeps exist, the envelope reports their actual
    spread. We fake "full sweep" by linking every existing problem to a
    submission's attempts and stuffing usage into each trace."""
    # Build a 99-problem submission, then hand-poke a second attempt set
    # with a different total to widen the envelope.
    # Easier: build two single-attempt submissions but link them to a tiny
    # 1-problem competition so each is "full" against THAT competition.
    from app.models import Competition, CompetitionProblem, Problem
    with Session(engine) as s:
        p = s.exec(select(Problem)).first()
        comp = Competition(slug="envelope-test", name="Envelope Test",
                           kind="other", status="active")
        s.add(comp); s.commit(); s.refresh(comp)
        s.add(CompetitionProblem(competition_id=comp.id, problem_id=p.id))
        s.commit()

    # Two submissions, each "full" against the 1-problem comp. Use a
    # unique language (java) so we can use the language filter to isolate
    # this pair from other tests' submissions sharing the same slug —
    # conftest doesn't reset the DB between tests in this file, so without
    # this isolation step the envelope ends up polluted.
    _submit_minimal(client, model="env-low", language="java", trace_extras={
        "provider_meta": {"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
    })
    _submit_minimal(client, model="env-high", language="java", trace_extras={
        "provider_meta": {"usage": {"prompt_tokens": 800, "completion_tokens": 400}},
    })

    d = client.get(
        "/api/cost-estimator-stats?competition=envelope-test&language=java"
    ).json()
    assert d["input_tokens"]["n"] == 2
    assert d["input_tokens"]["min"] == 100
    assert d["input_tokens"]["max"] == 800
    assert d["output_tokens"]["min"] == 50
    assert d["output_tokens"]["max"] == 400
