"""Cross-language correctness × performance analysis endpoint.

Verifies /api/analysis returns per-language aggregates, per-submission scatter
points, and Pearson correlations between correctness (pass@1/pass@k) and
performance (runtime/memory percentile)."""
from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS


def _seed_judged(client, lang, pass1, rt_pct, mem_pct):
    """Create a submission and force it judged with specific metrics so the
    analysis math is deterministic (the fake judge is random otherwise)."""
    body = {
        "harness_slug": HARNESS,
        "model_name": f"m-{lang}-{int(pass1*100)}", "model_version": "v1",
        "language": lang, "prompt_template": "stand-in 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]
    from sqlmodel import Session
    from app.db import engine
    from app.models import Submission, SubmissionStatus
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        sub.status = SubmissionStatus.judged
        sub.pass_at_1 = pass1
        sub.pass_at_k = pass1
        sub.avg_runtime_percentile = rt_pct
        sub.avg_memory_percentile = mem_pct
        s.add(sub); s.commit()
    return sid


def test_analysis_groups_by_language(client):
    # Use languages no other test file touches so the shared-DB means stay
    # isolated to what THIS test seeds (the endpoint aggregates every judged
    # submission in the DB).
    _seed_judged(client, "scala", 0.9, 60.0, 70.0)
    _seed_judged(client, "scala", 0.7, 40.0, 50.0)
    _seed_judged(client, "swift", 0.5, 80.0, 90.0)

    d = client.get("/api/analysis").json()
    by = {r["language"]: r for r in d["by_language"]}
    assert "scala" in by and "swift" in by
    # scala: mean pass@1 of 0.9 and 0.7 = 0.8
    assert abs(by["scala"]["mean_pass_at_1"] - 0.8) < 1e-9
    assert by["scala"]["n_submissions"] == 2
    assert by["scala"]["best_pass_at_1"] == 0.9
    # swift: single submission
    assert by["swift"]["n_submissions"] == 1
    assert abs(by["swift"]["mean_runtime_percentile"] - 80.0) < 1e-9


def test_analysis_points_present(client):
    _seed_judged(client, "cpp", 0.6, 55.0, 65.0)
    d = client.get("/api/analysis").json()
    pts = [p for p in d["points"] if p["language"] == "cpp"]
    assert pts, "expected at least one cpp scatter point"
    p = pts[0]
    assert p["pass_at_1"] == 0.6
    assert p["avg_runtime_percentile"] == 55.0
    assert "model_name" in p and "submission_id" in p


def test_analysis_correlation_perfect_positive(client):
    """3 submissions where pass@1 and runtime %ile move together perfectly →
    Pearson r should be ~1.0. Use a fresh language so other tests' rows don't
    pollute the pooled correlation (note: pooled corr is global, so this test
    asserts the WITHIN-language corr which is isolated to this language)."""
    _seed_judged(client, "go", 0.2, 20.0, 10.0)
    _seed_judged(client, "go", 0.5, 50.0, 40.0)
    _seed_judged(client, "go", 0.8, 80.0, 70.0)
    d = client.get("/api/analysis").json()
    go = next(r for r in d["by_language"] if r["language"] == "go")
    # pass@1 (0.2,0.5,0.8) vs rt%ile (20,50,80) are perfectly linear → r≈1
    assert go["pass1_runtime_corr"] is not None
    assert go["pass1_runtime_corr"] > 0.99


def test_analysis_correlation_none_when_too_few(client):
    """A language with < 3 paired points reports None for its correlation."""
    _seed_judged(client, "ruby", 0.4, 30.0, 20.0)
    d = client.get("/api/analysis").json()
    ruby = next(r for r in d["by_language"] if r["language"] == "ruby")
    assert ruby["pass1_runtime_corr"] is None


def test_analysis_public_no_auth(client):
    """The analysis endpoint is public (no Authorization header needed)."""
    assert client.get("/api/analysis").status_code == 200


# ---- discrimination endpoint ----------------------------------------------

def test_discrimination_endpoint_shape(client):
    """The discrimination endpoint returns per-language summaries; it reads a
    precomputed file, so on a clean test env it may be empty — assert the
    SHAPE either way."""
    r = client.get("/api/discrimination")
    assert r.status_code == 200
    d = r.json()
    assert "languages" in d and isinstance(d["languages"], list)
    assert "cross_language" in d and isinstance(d["cross_language"], list)
    assert "n_cross_language" in d
    for L in d["languages"]:
        assert {"language", "n_measured", "n_discriminative",
                "pct_discriminative", "mode_share_histogram"} <= set(L)
        assert len(L["mode_share_histogram"]) == 10
