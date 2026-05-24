"""Data model.

Five tables drive the site:
  User       — competitors and admins
  Problem    — supported LeetCode problems (seeded from data/problems.parquet)
  Submission — one batch from a user (a model run across all problems in one language)
  Attempt    — one solution within a submission (problem × language × code + trace)
  Run        — a dispatched LeetCode judge result for one attempt

Joins are done by FK lookup in handlers; we don't define ORM relationships.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlmodel import Field, SQLModel, JSON, Column


class UserRole(str, Enum):
    user = "user"
    admin = "admin"


class UserStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    display_name: str
    org: str | None = None
    role: UserRole = UserRole.user
    status: UserStatus = UserStatus.pending
    api_key: str | None = Field(default=None, index=True, unique=True)
    password_hash: str | None = None   # bcrypt; nullable for legacy users
    motivation: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: datetime | None = None


class Problem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    external_id: int = Field(index=True, description="LeetCode question id from the source parquet")
    title_slug: str = Field(index=True, unique=True)
    title: str
    level: str = Field(index=True)
    tags: str
    content: str
    hints: str | None = None


class SubmissionStatus(str, Enum):
    received = "received"
    dispatching = "dispatching"
    judged = "judged"
    failed = "failed"


class Submission(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    model_name: str = Field(index=True)
    model_version: str
    language: str = Field(index=True)
    prompt_template: str
    model_params: dict = Field(sa_column=Column(JSON))
    status: SubmissionStatus = SubmissionStatus.received
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Sample budget per problem (= "k" the submitter commits to upfront,
    # locked at creation, used for pass@k semantics).
    n_samples_per_problem: int = Field(default=1, ge=1)
    # The PromptTemplate this submission was produced with (when run via the
    # registry-driven script). Null for legacy / ad-hoc submissions that
    # passed `prompt_template` text directly.
    prompt_id: int | None = Field(default=None, foreign_key="prompttemplate.id", index=True)

    pass_at_1: float | None = None        # unbiased estimator over k samples
    pass_at_k: float | None = None        # k = n_samples_per_problem
    pass_at_full: float | None = None     # 1 if any of k samples accepted
    avg_runtime_ms: float | None = None
    avg_memory_mb: float | None = None
    best_runtime_ms: float | None = None
    best_memory_mb: float | None = None
    avg_runtime_percentile: float | None = None
    avg_memory_percentile: float | None = None

    # Coefficients vs. human baselines, averaged across the submission.
    # Convention: higher = better. Value = baseline / model.
    #   1.0 → matches the human baseline
    #   2.0 → twice as fast / half the memory of the baseline cohort
    #   0.5 → twice as slow / twice the memory
    time_human_avg: float | None = None
    time_human_expert: float | None = None
    mem_human_avg: float | None = None
    mem_human_expert: float | None = None

    total_solved: int = 0
    total_problems: int = 0

    # Soft delete: hides the submission from every public read surface but
    # keeps the row (and its attempts/runs/traces) so research exports, the
    # transparency log, and audit trails stay intact.
    deleted_at: datetime | None = Field(default=None, index=True)


class Attempt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", index=True)
    problem_id: int = Field(foreign_key="problem.id", index=True)
    code: str
    inference_trace: str
    # Verifiable provenance: {prompt, system_prompt, raw_response, reasoning,
    # model, provider, endpoint, params, created_at, provider_meta}. Lets a
    # reviewer confirm the model actually produced this solution.
    trace: dict | None = Field(default=None, sa_column=Column(JSON))
    wall_clock_ms: int | None = None
    cost_usd: float | None = None

    # Provenance (see provenance.py). trace_hash binds code+summary+trace and
    # is anchored in the append-only TraceLogEntry chain at ingest time.
    trace_hash: str | None = Field(default=None, index=True)
    trace_sig: str | None = None           # base64 Ed25519 signature (optional)
    trace_signer: str | None = None        # base64 Ed25519 public key (optional)
    sig_status: str | None = None          # verified|invalid|unsigned|unsupported
    model_digest: str | None = Field(default=None, index=True)
    model_digest_source: str | None = None  # submitted|derived
    log_seq: int | None = None             # position in the transparency log
    logged_at: datetime | None = None      # server-authoritative timestamp

    # Aggregates over the N dispatch runs of this attempt (best = min ms / min MB
    # / max percentile; avg = arithmetic mean over accepted runs).
    n_runs: int = 0
    n_accepted_runs: int = 0
    final_status: str | None = None
    avg_runtime_ms: float | None = None
    best_runtime_ms: float | None = None
    avg_memory_mb: float | None = None
    best_memory_mb: float | None = None
    avg_runtime_percentile: float | None = None
    best_runtime_percentile: float | None = None
    avg_memory_percentile: float | None = None
    best_memory_percentile: float | None = None


class Baseline(SQLModel, table=True):
    """Human reference performance for one (problem, language) pair.

    `*_avg` is the population mean; `*_expert` approximates the top decile
    (or whatever cohort we choose to call "expert"). Populated from public
    LeetCode submission data; seeded synthetically until that pipeline lands.
    """
    id: int | None = Field(default=None, primary_key=True)
    problem_id: int = Field(foreign_key="problem.id", index=True)
    language: str = Field(index=True)
    runtime_ms_avg: float
    runtime_ms_expert: float
    memory_mb_avg: float
    memory_mb_expert: float
    source: str = Field(default="synthetic", description="Where this baseline came from")
    fetched_at: datetime | None = None   # last refresh from LeetCode (if real)


class BaselineRefreshState(SQLModel, table=True):
    """Singleton (id=1) holding the trigger counters for the auto-refresher.
    Updated after each real-mode submission; the refresher fires when either
    the per-submission count or the wall-clock window crosses its threshold."""
    id: int = Field(default=1, primary_key=True)
    last_refresh_at: datetime | None = None
    submissions_since_last: int = 0


class Snapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    label: str = Field(index=True, description="e.g. '2026-W19' or ISO date")
    taken_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    payload: dict = Field(sa_column=Column(JSON))


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    attempt_id: int = Field(foreign_key="attempt.id", index=True)
    run_index: int = Field(default=1, description="Which of the N repeated dispatches (1-based)")
    submission_id_external: str | None = None
    status_msg: str
    runtime_ms: float | None = None
    memory_mb: float | None = None
    runtime_percentile: float | None = None
    memory_percentile: float | None = None
    total_correct: int | None = None
    total_testcases: int | None = None
    raw_response: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RegisteredModel(SQLModel, table=True):
    """A model officially supported on the platform. The team adds models
    here once access is granted by the model's authors; the platform then
    runs experiments against them and publishes the results. Replaces the
    older open self-submission flow."""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)              # canonical name used in submissions
    version: str
    provider: str                              # ollama|openai|anthropic|hf|custom
    endpoint: str | None = None                # base URL (Ollama) / "openai" / "anthropic"
    params: dict | None = Field(default=None, sa_column=Column(JSON))   # decoding defaults
    notes: str | None = None                   # markdown blurb
    status: str = Field(default="active", index=True)   # active|retired
    added_at: datetime = Field(default_factory=datetime.utcnow)
    added_by_user_id: int | None = Field(default=None, foreign_key="user.id")


class PromptTemplate(SQLModel, table=True):
    """Reusable prompt the team can pick when running experiments. The
    `user_prompt_template` may reference {{slug}} / {{statement}} /
    {{language}} / {{language_display}} placeholders, expanded at run time."""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    system_prompt: str
    user_prompt_template: str
    language_hint: str | None = None
    notes: str | None = None
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_user_id: int | None = Field(default=None, foreign_key="user.id")


class TraceLogEntry(SQLModel, table=True):
    """Append-only, hash-chained transparency log of submitted traces.

    Each entry commits to the previous one (`prev_hash`) and to the attempt's
    content-binding `leaf_hash`, with a server-authoritative `logged_at`. The
    chain is publicly auditable at GET /api/provenance/log: tampering,
    reordering, or back-dating any entry breaks every later `entry_hash`.
    """
    id: int | None = Field(default=None, primary_key=True)
    seq: int = Field(index=True, unique=True, description="Monotonic 1-based position")
    attempt_id: int = Field(foreign_key="attempt.id", index=True)
    leaf_hash: str = Field(index=True, description="sha256 binding code+summary+trace")
    prev_hash: str = Field(description="entry_hash of seq-1 (genesis = 64 zeros)")
    entry_hash: str = Field(description="sha256 over {seq,prev_hash,leaf_hash,attempt_id,logged_at}")
    logged_at: datetime = Field(default_factory=datetime.utcnow, index=True)
