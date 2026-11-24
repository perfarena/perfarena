"""Stored efficiency rankings: a page gets the last computed ranking at once,
and one background thread recomputes it.

A ranking takes seconds to compute (the bootstrap behind the tied leaders), so
a page waits only for a set of filters that was never computed, or after a
change of the ranking settings (the tie margins, the bootstrap samples). The
thread recomputes a stored ranking when
  * the ranking settings changed, or
  * the data changed (ranking_api.data_version) and the ranking is older than
    ranking.refresh_interval_s (one hour by default).
It keeps the default views (each active competition, over all languages and in
each language) computed. It refreshes another set of filters only while a page
asked for it in the last week. POST /api/admin/rankings/refresh recomputes
every stored ranking at once.

The thread is off when ARENA_RANKING_REFRESH=0 (the test suite).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta

from sqlmodel import Session, select

from . import app_settings
from .db import engine
from .models import Competition, RankingResult

PASS_INTERVAL_S = 60                     # how often the thread looks for stale rankings
TOUCH_AFTER = timedelta(hours=1)         # requested_at is updated at most this often
KEEP_FRESH_FOR = timedelta(days=7)       # a non-default ranking nobody requested stops refreshing

FILTERS = ("harness", "agent", "agent_kind", "rounds", "prompt_id", "language")

_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()
_pass_lock = threading.Lock()


def params_of(competition: str, **filters) -> tuple[str, dict]:
    params = {"competition": competition, **{f: filters.get(f) for f in FILTERS}}
    return json.dumps(params, sort_keys=True), params


def _lock(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _data_tag(session: Session) -> str:
    from .ranking_api import data_version
    return json.dumps(data_version(session), default=str)


def _settings_tag(session: Session, competition: str) -> str:
    from .ranking_api import ranking_settings
    st = ranking_settings(session, competition)
    return json.dumps([st.bootstrap, sorted(st.tie_pct.items()), st.min_cells,
                       st.min_problem_clusters], sort_keys=True)


def _compute_and_store(session: Session, key: str, params: dict) -> dict:
    from .ranking_api import compute_efficiency_rankings
    # Read the version before computing, so data that arrives meanwhile makes
    # the next pass see a change.
    data, settings = _data_tag(session), _settings_tag(session, params["competition"])
    payload = compute_efficiency_rankings(session, **params)
    row = session.get(RankingResult, key)
    if row is None:
        row = RankingResult(key=key, competition=params["competition"], params=params)
    row.data_version, row.settings, row.payload = data, settings, payload
    row.computed_at = datetime.utcnow()
    session.add(row)
    session.commit()
    return payload


def get(session: Session, competition: str, *, fresh: bool = False, **filters) -> dict:
    """The stored ranking, or a new one when none is stored, when the stored
    one has other ranking settings, or when `fresh` is set."""
    key, params = params_of(competition, **filters)
    settings = _settings_tag(session, competition)
    if not fresh:
        row = session.get(RankingResult, key)
        if row is not None and row.settings == settings:
            if datetime.utcnow() - row.requested_at > TOUCH_AFTER:
                row.requested_at = datetime.utcnow()
                session.add(row)
                session.commit()
            return row.payload
    with _lock(key):
        if not fresh:
            session.expire_all()                      # see what another request stored meanwhile
            row = session.get(RankingResult, key)
            if row is not None and row.settings == settings:
                return row.payload
        return _compute_and_store(session, key, params)


def _stale(row: RankingResult, data: str, settings: str, interval_s: float,
           force: bool) -> bool:
    if force or row.settings != settings:
        return True
    return (row.data_version != data
            and datetime.utcnow() - row.computed_at >= timedelta(seconds=interval_s))


def refresh_pass(force: bool = False) -> int:
    """Compute every missing default view and recompute every stale stored
    ranking. Returns the number of rankings computed."""
    n = 0
    with _pass_lock, Session(engine) as s:
        interval = app_settings.get_float("ranking.refresh_interval_s", 3600.0)
        data = _data_tag(s)
        settings: dict[str, str] = {}

        def stale(row: RankingResult | None) -> bool:
            if row is None:
                return True
            if row.competition not in settings:
                settings[row.competition] = _settings_tag(s, row.competition)
            return _stale(row, data, settings[row.competition], interval, force)

        def compute(key: str, params: dict) -> None:
            nonlocal n
            try:
                with _lock(key):
                    _compute_and_store(s, key, params)
                n += 1
            except Exception as e:                       # noqa: BLE001
                s.rollback()
                print(f"[ranking-store] {key}: {e!r}", file=sys.stderr)

        # The default views: each active competition over all languages, then
        # in each of its languages.
        defaults = set()
        for comp in s.exec(select(Competition).where(Competition.status == "active")).all():
            key, params = params_of(comp.slug)
            defaults.add(key)
            if stale(s.get(RankingResult, key)):
                compute(key, params)
            row = s.get(RankingResult, key)
            for lang in (row.payload.get("languages", []) if row else []):
                k, p = params_of(comp.slug, language=lang)
                defaults.add(k)
                if stale(s.get(RankingResult, k)):
                    compute(k, p)
        # Every other stored ranking that a page asked for in the last week.
        now = datetime.utcnow()
        for row in s.exec(select(RankingResult)).all():
            if row.key in defaults or now - row.requested_at > KEEP_FRESH_FOR:
                continue
            if stale(row):
                compute(row.key, row.params)
    return n


def refresh_now() -> bool:
    """Start a forced pass in the background. False when a pass is running."""
    if _pass_lock.locked():
        return False
    threading.Thread(target=_safe_pass, kwargs={"force": True},
                     name="ranking-refresh-now", daemon=True).start()
    return True


def _safe_pass(force: bool = False) -> None:
    try:
        n = refresh_pass(force=force)
        if n:
            print(f"[ranking-store] computed {n} ranking(s)", file=sys.stderr)
    except Exception as e:                               # noqa: BLE001
        print(f"[ranking-store] pass failed: {e!r}", file=sys.stderr)


def start_refresh_thread() -> None:
    if os.environ.get("ARENA_RANKING_REFRESH", "1") == "0":
        return

    def _loop() -> None:
        while True:
            _safe_pass()
            time.sleep(PASS_INTERVAL_S)

    threading.Thread(target=_loop, name="ranking-refresh", daemon=True).start()
