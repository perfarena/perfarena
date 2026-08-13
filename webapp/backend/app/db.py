"""SQLite engine + session helpers."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import SQLModel, Session, create_engine

DB_PATH = Path(os.environ.get("ARENA_DB", Path(__file__).parent.parent / "arena.db"))
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


# Concurrency hardening for the dispatch worker + HTTP handler combo.
# Without these, every concurrent writer hits "database is locked" the
# moment the worker is mid-transaction and the API tries to commit a
# submission append.
#
#   * WAL  — Write-Ahead Logging lets writers append to a side journal
#            while readers see the pre-write state from the main file.
#            One writer at a time still applies, but concurrent readers
#            no longer block it. Persistent (set on the file itself), so
#            once enabled it survives container restarts.
#   * synchronous=NORMAL — safe with WAL (fsync only at checkpoint, not
#            every write). Still durable; just trades a microsecond
#            sub-checkpoint window for substantially less write latency.
#   * busy_timeout=30000 — when a writer DOES collide, wait 30 s instead
#            of erroring after the default 5 s. Most lock waits clear in
#            milliseconds; a longer ceiling absorbs the dispatcher's
#            occasional multi-row commit bursts without leaking a 500.
@event.listens_for(engine, "connect")
def _sqlite_concurrency_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


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
    # Iterative-refinement / experiment-knob columns.
    ("submission", "n_rounds", "INTEGER"),
    ("submission", "include_hints", "INTEGER"),       # 0/1 — SQLite has no bool
    ("submission", "optimization_target", "VARCHAR"), # 'latency'|'memory'|NULL
    ("attempt", "round_index", "INTEGER"),
    ("attempt", "previous_attempt_id", "INTEGER"),    # nullable FK back to attempt.id
    # LeetCode's starter-code snippets keyed by langSlug — populated by
    # app.fetch_code_snippets. Stored as JSON: {"python3": "class Solution:\n…", …}
    ("problem", "code_snippets", "JSON"),
    # Cost + token accounting.
    #   attempt.cost_usd        — $ cost of the one LLM call that produced this
    #                             sample (0 for local/Ollama). Defined on the
    #                             model already, but it was never in this list,
    #                             so older DBs lack the column — add it here.
    #   attempt.response_tokens — token count of the CODE-only response.
    #   submission.total_cost_usd / total_response_tokens — sums across all the
    #                             submission's attempts (covers k samples × N
    #                             problems × refinement rounds).
    ("attempt", "cost_usd", "FLOAT"),
    ("attempt", "response_tokens", "INTEGER"),
    ("submission", "total_cost_usd", "FLOAT"),
    ("submission", "total_response_tokens", "INTEGER"),
    # Generation wall-clock accounting.
    #   attempt.wall_clock_ms        — generation runtime of THIS solution (ms).
    #                                  On the model since early on, but absent
    #                                  from this list, so older DBs may lack it.
    #   submission.total_generation_ms — sum of wall_clock_ms across attempts.
    ("attempt", "wall_clock_ms", "INTEGER"),
    ("submission", "total_generation_ms", "INTEGER"),
    # Verbatim LeetCode /check/ payload per run — additive so older rows
    # keep the field null until the next dispatch fills it in.
    ("run", "leetcode_full_response", "JSON"),
    # Client-detected malformed-response signal. When set, the attempt is
    # recorded but dispatch synthesizes a failure Run (no LeetCode call) so
    # the raw model response is preserved without burning a submission quota.
    ("attempt", "prefailed_reason", "VARCHAR"),
    # System-level dispatch failure that's NOT a verdict from LeetCode:
    # expired credentials, LC frontend 5xx after retries, etc. When set we
    # write ZERO Run rows for the attempt — those would be pure noise in
    # pass@k and on the leaderboard — and the next dispatch call retries
    # the attempt automatically (pending = no Run rows). The sweep client
    # surfaces the warning so the user knows whether to refresh cookies
    # or just wait the transient out.
    ("attempt", "dispatch_warning", "VARCHAR"),
    # Submission is now SCOPED to a single competition — pass@k and
    # "complete" math use the competition's problem count as the
    # denominator (not "every Problem row in the system", which would
    # double-count canonical+variant problems and report 45/198 instead
    # of 45/99). NULL on legacy submissions; the leaderboard math falls
    # back to the canonical-99 universe for those.
    ("submission", "competition_id", "INTEGER"),
    # Anti-contamination problem variants — see app/rewrite_competition.py.
    # A "variant" Problem is the same task as `original_problem_id`, but
    # the title / statement / function name have been rephrased so a model
    # that memorised the original training set can't pattern-match its way
    # to the answer. Dispatch redirects variant submissions to the
    # original's title_slug AND substitutes `function_name` → `original_function_name`
    # in the code before sending to LeetCode (since LC's judge only knows
    # the canonical signature).
    #
    # Columns on originals stay null. On variants all three are set.
    ("problem", "original_problem_id", "INTEGER"),
    ("problem", "function_name", "VARCHAR"),
    ("problem", "original_function_name", "VARCHAR"),
    # Rich admin-curated metadata on registered models. Auto-stubbed rows
    # start with these all null and admins fill them in via /models edit.
    ("registeredmodel", "description", "TEXT"),
    ("registeredmodel", "model_type", "VARCHAR"),
    ("registeredmodel", "family", "VARCHAR"),
    ("registeredmodel", "provenance", "TEXT"),
    # Energy accounting (Joules). Reported by the submit client when a power
    # probe is available; LeetCode never reports it, so the UI hides the
    # column / stat when every row in scope is null.
    ("attempt", "generation_energy_j", "FLOAT"),
    ("submission", "total_generation_energy_j", "FLOAT"),
    # Per-model USD/1M token pricing — admin-configurable on /models. When
    # set, the SPA recomputes attempt.cost_usd dynamically from these
    # rates × stored prompt+response tokens, so an operator who edits a
    # rate after a sweep instantly sees the new $-totals everywhere
    # (per-attempt, per-submission, leaderboards). When null we fall
    # back to the snapshot cost_usd stamped on the attempt at submit.
    ("registeredmodel", "input_price_per_1m_usd", "FLOAT"),
    ("registeredmodel", "output_price_per_1m_usd", "FLOAT"),
    # Prompt-token count per attempt — required for the live-recomputed
    # cost above. New submissions populate it; older attempts where it's
    # null fall back to the snapshot cost (no recomputation possible).
    ("attempt", "prompt_tokens", "INTEGER"),
    # Benchmark harness / performance oracle abstraction. Historically the
    # platform was hard-wired to LeetCode's online judge; introducing the
    # Harness table lets us plug in additional oracles (in-house harness,
    # HumanEval unittest runner, etc.) without conflating their outputs.
    # Binding is layered — Competition → Submission override → Run captures
    # the oracle that actually ran. Legacy rows are backfilled to the seeded
    # "leetcode" harness so their implied provenance is preserved.
    ("competition", "harness_id", "INTEGER"),
    ("submission", "harness_id", "INTEGER"),
    ("run", "harness_id", "INTEGER"),
    # Runtime energy (Joules) consumed while EXECUTING the solution — the
    # third perf axis alongside runtime_ms and memory_mb. Only harnesses
    # that instrument execution (RAPL / NVML / an in-house probe) populate
    # these; LeetCode never does, so every legacy row stays NULL and the
    # SPA hides the column when nothing in scope has data.
    ("run", "runtime_energy_j", "FLOAT"),
    ("run", "runtime_energy_percentile", "FLOAT"),
    ("attempt", "avg_runtime_energy_j", "FLOAT"),
    ("attempt", "best_runtime_energy_j", "FLOAT"),
    ("attempt", "avg_runtime_energy_percentile", "FLOAT"),
    ("attempt", "best_runtime_energy_percentile", "FLOAT"),
    ("submission", "avg_runtime_energy_j", "FLOAT"),
    ("submission", "best_runtime_energy_j", "FLOAT"),
    ("submission", "avg_runtime_energy_percentile", "FLOAT"),
    ("submission", "best_runtime_energy_percentile", "FLOAT"),
    # Retry-until-accepted workflow flag. See models.Submission.stop_on_accept
    # for semantics. SQLite has no bool → 0/1 INTEGER; legacy rows stay NULL
    # and the ingest treats NULL as False (all-rounds behavior).
    ("submission", "stop_on_accept", "INTEGER"),
    # Correctness oracle FKs — the decoupled twin of the harness (perf
    # oracle) FKs above. Same layered binding (Competition → Submission
    # override → Run captures what actually judged). Legacy rows get
    # backfilled to the seeded "leetcode" correctness oracle by
    # _seed_default_correctness_oracle so pre-abstraction data keeps its
    # implied provenance intact.
    ("competition", "correctness_oracle_id", "INTEGER"),
    ("submission", "correctness_oracle_id", "INTEGER"),
    ("run", "correctness_oracle_id", "INTEGER"),
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
        # Backfill the experiment-knob columns: legacy submissions were all
        # single-round, no hints, no specific optimization target. Legacy
        # attempts were all round 0 with no predecessor in the chain.
        for stmt in (
            "UPDATE submission SET n_rounds = 1 WHERE n_rounds IS NULL",
            "UPDATE submission SET include_hints = 0 WHERE include_hints IS NULL",
            "UPDATE attempt SET round_index = 0 WHERE round_index IS NULL",
        ):
            try:
                conn.execute(text(stmt))
            except Exception:
                pass


def _seed_default_harness() -> None:
    """Seed the built-in "leetcode" harness (id=1) and backfill every
    Competition / Submission / Run that lacks a harness_id to it. Idempotent:
    the insert is guarded by a slug lookup, and the UPDATEs are no-ops once
    the columns are populated. Preserves all existing data — rows are only
    filled where they were previously NULL."""
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT id FROM harness WHERE slug = 'leetcode' LIMIT 1"
        )).fetchone()
        if row is None:
            # Raw SQL bypasses SQLModel's Python-side default_factory, so we
            # stamp created_at explicitly to satisfy the NOT NULL constraint.
            from datetime import datetime as _dt
            conn.execute(text(
                "INSERT INTO harness (slug, name, kind, description, status, created_at) "
                "VALUES (:slug, :name, :kind, :desc, 'active', :created_at)"
            ), {
                "slug": "leetcode",
                "name": "LeetCode Online Judge",
                "kind": "leetcode",
                "desc": (
                    "LeetCode's public online judge. Runtime, memory, and "
                    "percentile columns on Run reflect what LeetCode reported "
                    "for the submission. This is the legacy default — every "
                    "competition, submission, and run that predates the "
                    "harness abstraction is backfilled to it."
                ),
                "created_at": _dt.utcnow(),
            })
            row = conn.execute(text(
                "SELECT id FROM harness WHERE slug = 'leetcode' LIMIT 1"
            )).fetchone()
        leetcode_harness_id = int(row[0])
        for stmt in (
            "UPDATE competition SET harness_id = :hid WHERE harness_id IS NULL",
            "UPDATE submission SET harness_id = :hid WHERE harness_id IS NULL",
            "UPDATE run SET harness_id = :hid WHERE harness_id IS NULL",
        ):
            try:
                conn.execute(text(stmt), {"hid": leetcode_harness_id})
            except Exception:
                # Column may not exist on the very first startup pass if the
                # additive migration failed silently — swallow and let the
                # next boot retry. All errors here are non-fatal.
                pass


def _seed_default_correctness_oracle() -> None:
    """Seed the built-in "leetcode" correctness oracle (id=1) and backfill
    every Competition / Submission / Run that lacks a correctness_oracle_id
    to it. Mirror of _seed_default_harness for the correctness half of the
    two-oracle model. Idempotent: repeat calls are no-ops."""
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT id FROM correctnessoracle WHERE slug = 'leetcode' LIMIT 1"
        )).fetchone()
        if row is None:
            from datetime import datetime as _dt
            conn.execute(text(
                "INSERT INTO correctnessoracle (slug, name, kind, description, status, created_at) "
                "VALUES (:slug, :name, :kind, :desc, 'active', :created_at)"
            ), {
                "slug": "leetcode",
                "name": "LeetCode Online Judge",
                "kind": "leetcode",
                "desc": (
                    "LeetCode's public online judge — the correctness half "
                    "of the two-oracle model. Every Accepted / Wrong Answer "
                    "/ Runtime Error / Time Limit Exceeded / Compile Error "
                    "verdict on runs from this oracle comes straight from "
                    "LC's /check/ endpoint. The legacy default — every "
                    "competition, submission, and run that predates the "
                    "correctness-oracle abstraction is backfilled to it."
                ),
                "created_at": _dt.utcnow(),
            })
            row = conn.execute(text(
                "SELECT id FROM correctnessoracle WHERE slug = 'leetcode' LIMIT 1"
            )).fetchone()
        leetcode_oracle_id = int(row[0])
        for stmt in (
            "UPDATE competition SET correctness_oracle_id = :oid WHERE correctness_oracle_id IS NULL",
            "UPDATE submission SET correctness_oracle_id = :oid WHERE correctness_oracle_id IS NULL",
            "UPDATE run SET correctness_oracle_id = :oid WHERE correctness_oracle_id IS NULL",
        ):
            try:
                conn.execute(text(stmt), {"oid": leetcode_oracle_id})
            except Exception:
                pass


def init_db() -> None:
    from . import models  # noqa: F401  ensure models are imported before create_all
    SQLModel.metadata.create_all(engine)
    _apply_additive_migrations()
    _seed_default_harness()
    _seed_default_correctness_oracle()


def get_session():
    with Session(engine) as session:
        yield session
