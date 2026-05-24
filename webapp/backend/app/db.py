"""SQLite engine + session helpers."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine

DB_PATH = Path(os.environ.get("ARENA_DB", Path(__file__).parent.parent / "arena.db"))
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


# Tiny additive-only migrations applied on startup. Idempotent: each entry is
# `ALTER TABLE … ADD COLUMN …`, which SQLite errors on if the column already
# exists — we swallow that.
_ADDITIVE_COLUMNS = [
    ("submission", "avg_runtime_ms", "FLOAT"),
    ("submission", "avg_memory_mb", "FLOAT"),
    ("submission", "best_runtime_ms", "FLOAT"),
    ("submission", "best_memory_mb", "FLOAT"),
    ("submission", "time_human_avg", "FLOAT"),
    ("submission", "time_human_expert", "FLOAT"),
    ("submission", "mem_human_avg", "FLOAT"),
    ("submission", "mem_human_expert", "FLOAT"),
    ("run", "run_index", "INTEGER"),
    ("attempt", "n_runs", "INTEGER"),
    ("attempt", "n_accepted_runs", "INTEGER"),
    ("attempt", "final_status", "VARCHAR"),
    ("attempt", "avg_runtime_ms", "FLOAT"),
    ("attempt", "best_runtime_ms", "FLOAT"),
    ("attempt", "avg_memory_mb", "FLOAT"),
    ("attempt", "best_memory_mb", "FLOAT"),
    ("attempt", "avg_runtime_percentile", "FLOAT"),
    ("attempt", "best_runtime_percentile", "FLOAT"),
    ("attempt", "avg_memory_percentile", "FLOAT"),
    ("attempt", "best_memory_percentile", "FLOAT"),
    ("attempt", "trace", "JSON"),
    ("attempt", "trace_hash", "VARCHAR"),
    ("attempt", "trace_sig", "VARCHAR"),
    ("attempt", "trace_signer", "VARCHAR"),
    ("attempt", "sig_status", "VARCHAR"),
    ("attempt", "model_digest", "VARCHAR"),
    ("attempt", "model_digest_source", "VARCHAR"),
    ("attempt", "log_seq", "INTEGER"),
    ("attempt", "logged_at", "DATETIME"),
    ("user", "password_hash", "VARCHAR"),
    ("submission", "deleted_at", "DATETIME"),
    ("submission", "n_samples_per_problem", "INTEGER"),
    ("submission", "pass_at_k", "FLOAT"),
    ("submission", "pass_at_full", "FLOAT"),
    ("baseline", "fetched_at", "DATETIME"),
    ("submission", "prompt_id", "INTEGER"),
]


def _apply_additive_migrations() -> None:
    with engine.begin() as conn:
        cols_by_table: dict[str, set[str]] = {}
        for table, col, col_type in _ADDITIVE_COLUMNS:
            if table not in cols_by_table:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                cols_by_table[table] = {r[1] for r in rows}
            if col not in cols_by_table[table]:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                cols_by_table[table].add(col)
        # Backfill: existing submissions were always single-sample.
        try:
            conn.execute(text(
                "UPDATE submission SET n_samples_per_problem = 1 "
                "WHERE n_samples_per_problem IS NULL"
            ))
        except Exception:
            pass


def init_db() -> None:
    from . import models  # noqa: F401  ensure models are imported before create_all
    SQLModel.metadata.create_all(engine)
    _apply_additive_migrations()


def get_session():
    with Session(engine) as session:
        yield session
