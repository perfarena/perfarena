"""Stored efficiency rankings (ranking_store.py) and the batched bootstrap fit."""
from __future__ import annotations

import threading
from datetime import timedelta

import numpy as np
import pytest
from sqlmodel import Session

from conftest import ADMIN, USER
from test_ranking import COMP, _seed_competition
from app import app_settings, ranking as rk, ranking_store
from app.db import engine
from app.models import AppSetting, RankingResult


def test_fit_batch_matches_fit_one_by_one():
    rng = np.random.default_rng(1)
    Ws = rng.integers(0, 4, size=(200, 6, 6)).astype(float) / 2
    Ws[:, np.arange(6), np.arange(6)] = 0
    for start in (None, rk.fit(Ws.sum(0))):
        one_by_one = np.array([rk.fit(W, start=start) for W in Ws])
        assert np.allclose(rk.fit_batch(Ws, start=start), one_by_one, rtol=0, atol=1e-9)


@pytest.fixture(scope="module", autouse=True)
def _data():
    _seed_competition()


def _row(key: str) -> RankingResult | None:
    with Session(engine) as s:
        return s.get(RankingResult, key)


def _get(client, **params):
    r = client.get("/api/efficiency-rankings", params={"competition": COMP, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_first_request_stores_the_ranking_and_later_requests_read_it(client):
    key, _ = ranking_store.params_of(COMP, agent="direct")
    first = _get(client, agent="direct")
    assert _row(key).payload == first
    with Session(engine) as s:
        row = s.get(RankingResult, key)
        row.payload = {**row.payload, "marker": 1}
        s.add(row)
        s.commit()
    assert _get(client, agent="direct")["marker"] == 1


def test_new_data_waits_for_the_refresh_interval(monkeypatch):
    key, _ = ranking_store.params_of(COMP, rounds="single")
    with Session(engine) as s:
        ranking_store.get(s, COMP, rounds="single")
    computed = _row(key).computed_at
    monkeypatch.setattr(ranking_store, "_data_tag", lambda s: "new data")
    ranking_store.refresh_pass()
    assert _row(key).computed_at == computed            # changed data, but within the hour
    with Session(engine) as s:
        row = s.get(RankingResult, key)
        row.computed_at -= timedelta(hours=2)
        s.add(row)
        s.commit()
    ranking_store.refresh_pass()
    assert _row(key).data_version == "new data"


def test_a_settings_change_recomputes_at_the_next_pass():
    key, _ = ranking_store.params_of(COMP)
    with Session(engine) as s:
        ranking_store.get(s, COMP)
        s.add(AppSetting(key="ranking.tie_latency_pct", value="5.0"))
        s.commit()
    app_settings._invalidate()
    try:
        ranking_store.refresh_pass()
        assert _row(key).payload["metrics"]["latency"]["tie_pct"] == 5.0
    finally:
        with Session(engine) as s:
            s.delete(s.get(AppSetting, "ranking.tie_latency_pct"))
            s.commit()
        app_settings._invalidate()
        ranking_store.refresh_pass()
    assert _row(key).payload["metrics"]["latency"]["tie_pct"] != 5.0


def test_the_default_views_are_computed():
    ranking_store.refresh_pass()
    for language in (None, "python3", "cpp"):
        key, _ = ranking_store.params_of(COMP, language=language)
        assert _row(key) is not None, language


def test_a_snapshot_computes_a_fresh_ranking():
    key, _ = ranking_store.params_of(COMP)
    with Session(engine) as s:
        row = s.get(RankingResult, key) or RankingResult(key=key, competition=COMP, params={},
                                                         data_version="", settings="")
        row.payload = {"marker": 1}
        s.add(row)
        s.commit()
        fresh = ranking_store.get(s, COMP, fresh=True)
    assert "marker" not in fresh and _row(key).payload == fresh


def test_only_an_admin_can_start_a_recompute(client, monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(ranking_store, "_safe_pass", lambda force=False: ran.set())
    assert client.post("/api/admin/rankings/refresh").status_code == 401
    assert client.post("/api/admin/rankings/refresh", headers=USER).status_code == 403
    r = client.post("/api/admin/rankings/refresh", headers=ADMIN)
    assert r.status_code == 202 and r.json() == {"started": True}
    assert ran.wait(5)


def test_large_responses_are_gzipped(client):
    r = client.get("/api/efficiency-rankings", params={"competition": COMP},
                   headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and len(r.content) > 1024
    assert r.headers["content-encoding"] == "gzip"
    small = client.get("/api/version", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in small.headers
