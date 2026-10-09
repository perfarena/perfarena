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
    # LeetCode-provided starter-code snippets per language, keyed by langSlug
    # ("python3", "cpp", "java", …). Each value is the exact class + method
    # signature shape LeetCode expects — a model that returns code conforming
    # to it avoids "class/method not found" judge errors. Populated by
    # `python -m app.fetch_code_snippets`; nullable until that script runs.
    code_snippets: dict | None = Field(default=None, sa_column=Column(JSON))
    # Anti-contamination variants (see app/rewrite_competition.py). When
    # `original_problem_id` is set this Problem is a rephrased copy of
    # another — dispatch redirects to the original's title_slug AND
    # rewrites the LLM's code by substituting `function_name` (what the
    # variant's snippets expose) → `original_function_name` (what LC's
    # judge expects). All three columns are null on canonical problems.
    original_problem_id: int | None = Field(default=None, foreign_key="problem.id", index=True)
    function_name: str | None = None
    original_function_name: str | None = None


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
    # Energy-measurement provenance (new looped methodology). A single
    # submission MUST be measured with ONE tool on ONE machine — enforced at
    # dispatch time (see dispatch.py). energy_tool / machine_id are NULL on
    # LeetCode-oracle submissions (no local machine = "unknown"). run_uid is a
    # unique identifier for this submission's measurement run.
    energy_tool: str | None = Field(default=None, index=True)   # codecarbon | rapl | powermetrics | None
    run_uid: str | None = Field(default=None, index=True)
    machine_id: int | None = Field(default=None, foreign_key="machine.id", index=True)
    # Competition this submission is scoped to. total_problems uses THIS
    # competition's problem count (not "every Problem row in the system").
    # NULL on legacy rows; the leaderboard math defaults to the canonical
    # universe for those so pre-feature submissions stay comparable.
    competition_id: int | None = Field(
        default=None, foreign_key="competition.id", index=True)

    # Sample budget per problem (= "k" the submitter commits to upfront,
    # locked at creation, used for pass@k semantics).
    n_samples_per_problem: int = Field(default=1, ge=1)
    # The PromptTemplate this submission was produced with (when run via the
    # registry-driven script). Null for legacy / ad-hoc submissions that
    # passed `prompt_template` text directly.
    prompt_id: int | None = Field(default=None, foreign_key="prompttemplate.id", index=True)

    # Iterative-refinement experiment knobs (all default to single-shot,
    # no hints, no specific optimization target — the legacy behavior).
    # Set together at submission creation; the client driver in
    # `webapp/examples/iterative_refine.py` interprets these to shape the
    # prompt expansion for each round.
    n_rounds: int = Field(default=1, ge=1)
    include_hints: bool = Field(default=False)
    optimization_target: str | None = Field(default=None)   # 'latency'|'memory'|None
    # Retry-until-accepted workflow flag. When true, the client driver stops
    # spawning additional rounds for a problem the moment an attempt lands
    # `Accepted` — n_rounds is a MAXIMUM, not a fixed count. When false /
    # null (legacy default), the driver walks all n_rounds regardless so
    # every problem produces the same number of attempts. Server-side we
    # don't enforce anything; this is a durable record of intent so later
    # analysis can tell "retry to correctness" runs apart from "always run
    # N rounds" experiments.
    stop_on_accept: bool = Field(default=False)

    pass_at_1: float | None = None        # unbiased estimator over k samples
    pass_at_k: float | None = None        # k = n_samples_per_problem
    pass_at_full: float | None = None     # 1 if any of k samples accepted
    avg_runtime_ms: float | None = None
    avg_memory_mb: float | None = None
    best_runtime_ms: float | None = None
    best_memory_mb: float | None = None
    avg_runtime_percentile: float | None = None
    avg_memory_percentile: float | None = None
    # Solution-runtime energy (Joules) — aggregated across the submission's
    # attempts. See Attempt.avg_runtime_energy_j for what "runtime energy"
    # means vs Attempt.generation_energy_j (LLM side). Null on legacy /
    # LeetCode-only submissions since LC never measures execution energy;
    # the seeded in-house harness populates these on future sweeps.
    avg_runtime_energy_j: float | None = None
    best_runtime_energy_j: float | None = None
    avg_runtime_energy_percentile: float | None = None
    best_runtime_energy_percentile: float | None = None
    # Submission-level per function call rollup (B3): average solves/measurement across the
    # chosen attempts. The per function call TIME and ENERGY are already available as
    # avg_runtime_ms / avg_runtime_energy_j (those columns ARE the per function call
    # values under the looped methodology); avg_iterations adds the solve
    # count behind each measurement (confidence). NULL on LeetCode-scored subs.
    avg_iterations: float | None = None

    # Coefficients vs. the LeetCode-population baselines, averaged across the
    # submission. Convention: higher = better. Value = baseline / model.
    #   1.0 → matches the LeetCode baseline
    #   2.0 → twice as fast / half the memory of the LeetCode cohort
    #   0.5 → twice as slow / twice the memory
    # NB: legacy `_human_` field names — kept to avoid a DB migration. The
    # cohort isn't purely human (LeetCode mixes in AI-assisted submissions);
    # all user-visible UI strings have been renamed to "LeetCode avg/expert".
    time_human_avg: float | None = None
    time_human_expert: float | None = None
    mem_human_avg: float | None = None
    mem_human_expert: float | None = None

    total_solved: int = 0
    total_problems: int = 0

    # Cost + token accounting, summed across every attempt in the submission
    # (so k samples × N problems × refinement rounds all roll up here).
    # Recomputed whenever attempts are ingested. None until any attempt with
    # the respective field lands; 0.0 when all contributing attempts are free
    # (Ollama). See _recompute_submission_costs in main.py.
    total_cost_usd: float | None = None
    total_response_tokens: int | None = None
    # Total generation wall-clock across all attempts (ms) — the sum of every
    # attempt's `wall_clock_ms`. None until any attempt reports its generation
    # time. Recomputed alongside the cost/token totals.
    total_generation_ms: int | None = None
    # Total energy of the generation process across all attempts (joules).
    # Reported only when the submit client measures it (e.g., a GPU power
    # probe). For LeetCode-only data paths this stays None and the UI omits
    # the column / stat. Summed in _recompute_submission_costs.
    total_generation_energy_j: float | None = None

    # Soft delete: hides the submission from every public read surface but
    # keeps the row (and its attempts/runs/traces) so research exports, the
    # transparency log, and audit trails stay intact.
    deleted_at: datetime | None = Field(default=None, index=True)

    # PERFORMANCE oracle for this submission. NULL means "inherit from the
    # competition's default"; setting it overrides at the submission level
    # (e.g. re-scoring an old LC submission under a local perf harness). The
    # startup seed backfills legacy rows to the built-in "leetcode" perf
    # oracle.
    harness_id: int | None = Field(default=None, foreign_key="harness.id", index=True)
    # CORRECTNESS oracle for this submission. Same layering as harness_id
    # (submission override → competition default → NULL). Legacy rows get
    # backfilled to the seeded "leetcode" correctness oracle at startup.
    correctness_oracle_id: int | None = Field(
        default=None, foreign_key="correctnessoracle.id", index=True,
    )
    # PRODUCER of this submission's code — the agentic harness / workflow that
    # drove the model (e.g. Claude Code, Aider, OpenHands), as opposed to the
    # model itself (model_name/model_version). NULL means "direct" (the model
    # was prompted once with no agent loop), which is the legacy default: every
    # pre-feature submission is backfilled to the seeded "direct" agent, and a
    # new submission that names no agent resolves to it too. The competitor
    # identity on the leaderboard is the (agent, model) pair, so "Claude Code +
    # opus" ranks apart from "direct + opus". NOT part of the trace hash or the
    # model digest, so adding it leaves every existing provenance record valid.
    agent_id: int | None = Field(default=None, foreign_key="agent.id", index=True)


class Machine(SQLModel, table=True):
    """A physical/virtual machine on which submissions are measured. Each
    Submission references the machine it ran on (NULL = unknown, e.g. LeetCode-
    oracle submissions). Energy numbers are only comparable within one machine +
    one energy tool, so the machine is part of a measurement's provenance."""
    id: int | None = Field(default=None, primary_key=True)
    name: str
    hostname: str | None = None
    cpu_model: str | None = None
    cores: int | None = None
    energy_source: str | None = None      # rapl | codecarbon | powermetrics | none
    os: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MeasurementProfile(SQLModel, table=True):
    """A specific (machine x energy tool x config) under which measurements
    were taken. Splitting this from Machine lets ONE physical box host several
    setups — RAPL at budget 1.5s, codecarbon at 1.0s, a different governor —
    without duplicating the hardware row. A Run points at the profile that
    ACTUALLY produced its numbers (mirrors Run.harness_id). Energy is only
    comparable within one profile."""
    id: int | None = Field(default=None, primary_key=True)
    machine_id: int | None = Field(default=None, foreign_key="machine.id", index=True)
    energy_tool: str | None = None        # rapl | codecarbon | powermetrics | none
    # Free-form measurement config: budget_s, mem_budget_s, warmup_frac,
    # repeats, governor, turbo_disabled, toolchain_versions, noise_floor_cv, ...
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # sha256 over canonical_json({energy_tool, config}); the (machine_id,
    # config_hash) pair is the dedup key so the same setup resolves to one row.
    config_hash: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


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
    # Wall-clock runtime of the generation process for THIS solution (ms) —
    # how long the model took to produce this attempt's code. Reported by the
    # submit client; summed into Submission.total_generation_ms.
    wall_clock_ms: int | None = None
    # Energy used by the generation process for THIS solution (joules).
    # Reported only when the submit client has a power probe (NVML, RAPL,
    # provider telemetry, …). LeetCode itself never reports this — left None
    # and the UI omits the column when no rows in scope carry a value.
    generation_energy_j: float | None = None
    # The client already detected this response isn't a valid solution
    # (wrong language extracted, no code in response, malformed output, …) —
    # we record it as a failed attempt and stash the raw response in `trace`
    # so the model's behavior is preserved for analysis. Set ⇒ dispatch skips
    # LeetCode entirely and synthesizes one failure Run with this string.
    prefailed_reason: str | None = None
    # System-level dispatch failure that's NOT a LeetCode verdict. Set when
    # the credential pool is dead, leetbot can't reach LC after retries, etc.
    # When set we write ZERO Run rows for this attempt (avoiding pass@k
    # pollution); the next dispatch call re-picks the attempt up (the
    # `pending` filter is "no Run rows yet"), so this also doubles as a
    # retry signal. Polling endpoints surface it so the sweep client can
    # tell the user WHY rather than hanging on a useless poll loop.
    dispatch_warning: str | None = None
    # $ cost of the single LLM call that produced THIS sample. 0 for local /
    # Ollama models; for paid APIs the client computes it from token pricing.
    # Snapshot value — captured at submit time using the rates configured on
    # the client at that moment. The SPA prefers to recompute this on-the-fly
    # from RegisteredModel.input_price_per_1m_usd × prompt_tokens +
    # output_price_per_1m_usd × response_tokens when those are available.
    cost_usd: float | None = None
    # Token count of the CODE-only response (the extracted solution, excluding
    # any reasoning/thinking channel). Best-effort — the client estimates it.
    response_tokens: int | None = None
    # Token count of the PROMPT side of the same LLM call. Added so the SPA
    # can recompute cost dynamically when the operator edits per-model
    # pricing on /models. Null on attempts ingested before this column
    # existed — those rows fall back to the snapshot cost_usd above.
    prompt_tokens: int | None = None

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

    # Refinement-chain link. round_index is 0-based within the submission
    # (round 0 = cold-start, no feedback). previous_attempt_id points back to
    # the round-(N-1) attempt for the same problem, so a single chain can be
    # walked forward or backward. Both nullable for back-compat with legacy
    # single-shot attempts.
    round_index: int = Field(default=0, ge=0)
    previous_attempt_id: int | None = Field(default=None, foreign_key="attempt.id", index=True)

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
    # Runtime energy of the solution (Joules) — energy consumed WHILE
    # EXECUTING the code, distinct from the LLM-side `generation_energy_j`
    # above. Populated only by harnesses that instrument execution (RAPL
    # / NVML / an in-house probe). Best = min across the attempt's runs;
    # avg = mean over accepted runs. LeetCode's online judge never reports
    # this, so every legacy row stays null and the UI hides the column
    # when nothing in scope has a value. Same shape as runtime_ms /
    # memory_mb so ranking / sorting code can treat energy as one more
    # perf axis without special-casing.
    avg_runtime_energy_j: float | None = None
    best_runtime_energy_j: float | None = None
    avg_runtime_energy_percentile: float | None = None
    best_runtime_energy_percentile: float | None = None
    # Mean iterations (solves) per accepted run — the per function call measurement's
    # confidence (B3). NULL on LeetCode-scored attempts.
    avg_iterations: float | None = None


class Baseline(SQLModel, table=True):
    """LeetCode-population reference performance for one (problem, language) pair.

    `*_avg` is the population mean; `*_expert` approximates the top decile
    (or whatever cohort we choose to call "expert"). Populated from public
    LeetCode submission data; seeded synthetically until that pipeline lands.
    The cohort isn't purely human — LeetCode mixes human and AI-assisted
    submissions in its distributions — so "LeetCode population" is more
    accurate than "human" for what these numbers actually represent.
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
    # Runtime energy (Joules) consumed while executing the solution on
    # this specific run. Optional — populated only by harnesses that can
    # measure execution energy (e.g. an in-house runner reading RAPL /
    # NVML). LeetCode never reports this so every legacy Run stays null.
    # Rolled up into Attempt.avg_runtime_energy_j / best_runtime_energy_j
    # the same way runtime_ms rolls into avg_runtime_ms.
    runtime_energy_j: float | None = None
    runtime_energy_percentile: float | None = None
    # Per function call metrics (looped shared-input methodology): iterations completed in
    # the measured window, and energy / time normalized per single solve.
    iterations: int | None = None
    energy_j_per_op: float | None = None
    time_us_per_op: float | None = None
    total_correct: int | None = None
    total_testcases: int | None = None
    raw_response: dict | None = Field(default=None, sa_column=Column(JSON))
    # Verbatim LeetCode /check/ response for THIS run (or the dispatcher's
    # synthetic failure payload in fake-mode / pre-flight errors). Captured
    # for every run regardless of verdict so any LeetCode-side error shape
    # — including ones the parser doesn't enumerate (`status_code`,
    # `pretty_lang`, `state`, `task_finish_time`, …) — is preserved for
    # later inspection.
    leetcode_full_response: dict | None = Field(
        default=None, sa_column=Column(JSON),
    )
    # PERFORMANCE oracle that ACTUALLY produced this run's runtime / memory
    # / percentile numbers. NULL means "inherit from the submission" (which
    # itself falls back to the competition). The startup seed backfills
    # legacy rows to the built-in "leetcode" perf oracle (id=1).
    harness_id: int | None = Field(default=None, foreign_key="harness.id", index=True)
    # CORRECTNESS oracle that ACTUALLY produced this run's verdict +
    # error_log. Often the same registry entry as harness_id when the
    # source is a bundled judge (LeetCode does both), but decoupling lets
    # a single Run record when e.g. correctness came from a local unit-test
    # runner and perf came from an in-house energy-probing harness. Startup
    # backfill points every legacy row at the seeded "leetcode" correctness
    # oracle.
    correctness_oracle_id: int | None = Field(
        default=None, foreign_key="correctnessoracle.id", index=True,
    )
    # The (machine x tool x config) that ACTUALLY produced this run's numbers.
    # Mirrors the harness_id / correctness_oracle_id layering. NULL for external
    # (LeetCode) runs that carry no local measurement environment.
    measurement_profile_id: int | None = Field(
        default=None, foreign_key="measurementprofile.id", index=True,
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RegisteredModel(SQLModel, table=True):
    """A model the platform knows about — either admin-curated or auto-stubbed
    from a first submission. Every (model_name, model_version) that lands in
    a Submission gets a row here, so /models can list every model that ever
    submitted answers; admins then fill in the rich metadata (description,
    type, family, provenance) on previously-unverified stubs."""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)              # canonical name used in submissions
    version: str
    provider: str                              # ollama|openai|anthropic|hf|custom
    endpoint: str | None = None                # base URL (Ollama) / "openai" / "anthropic"
    params: dict | None = Field(default=None, sa_column=Column(JSON))   # decoding defaults
    notes: str | None = None                   # markdown blurb
    # Rich metadata, filled in by admins after a model has been seen. All
    # nullable: auto-stubbed rows start with everything blank and the UI
    # surfaces an "unverified — needs admin details" banner until populated.
    description: str | None = None             # longer-form markdown blurb
    model_type: str | None = None              # chat | instruct | reasoning | coder | embedding | …
    family: str | None = None                  # qwen | llama | gpt | claude | mistral | …
    provenance: str | None = None              # HF/model-card URL, citation, source links (markdown)
    # active   — admin verified, in active use
    # retired  — admin has marked it out of rotation
    # unverified — auto-created on first submission; awaits admin curation
    status: str = Field(default="active", index=True)
    added_at: datetime = Field(default_factory=datetime.utcnow)
    added_by_user_id: int | None = Field(default=None, foreign_key="user.id")
    # Per-model USD pricing per 1M tokens. Admin-editable on /models. When
    # both are set, the SPA recomputes attempt.cost_usd dynamically from
    # these × stored prompt/response tokens — so editing a rate after a
    # sweep instantly updates every $-stat that touches this model. Null
    # = falls back to the snapshot cost_usd captured at submit time.
    input_price_per_1m_usd: float | None = None
    output_price_per_1m_usd: float | None = None


class Competition(SQLModel, table=True):
    """A named contest the platform tracks — one set of Problems, evaluated
    under one prompt/dispatch regime. The 99 LeetCode problems are the seed
    competition (slug='leetcode-99-perf'); future ones (HumanEval, MBPP, a
    custom curated set) drop in additively without touching existing data.

    Submissions reference Problems by slug — there's intentionally no FK
    from Submission to Competition. Competition-scoped leaderboards filter
    by membership in the join table, so a problem in two competitions is
    counted in both."""
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)     # stable id used in URLs
    name: str
    description: str | None = None                 # markdown blurb
    kind: str = Field(default="custom", index=True)  # leetcode | humaneval | mbpp | custom | …
    status: str = Field(default="active", index=True)  # active | retired
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_user_id: int | None = Field(default=None, foreign_key="user.id")
    # Default PERFORMANCE oracle (see Harness below) — produces the
    # runtime_ms / memory_mb / percentile / runtime_energy_j numbers on
    # runs from this competition. NULL on legacy rows; the startup seed
    # backfills to the built-in "leetcode" performance oracle so pre-
    # abstraction data keeps its implied provenance.
    harness_id: int | None = Field(default=None, foreign_key="harness.id", index=True)
    # Default CORRECTNESS oracle — produces the Accepted / Wrong Answer /
    # Runtime Error / TLE verdict + error_log. Historically bundled with
    # the perf oracle (LeetCode does both), but decoupling lets us pair a
    # local unit-test runner (correctness) with an in-house RAPL-instrumented
    # harness (performance) — or vice versa. NULL on legacy rows; startup
    # seed backfills to the built-in "leetcode" correctness oracle.
    correctness_oracle_id: int | None = Field(
        default=None, foreign_key="correctnessoracle.id", index=True,
    )
    # Admin allow-list of harness slugs blessed for this competition.
    # POST /api/submissions returns 400 if payload.harness_slug isn't in
    # this list — see _validate_compat_slugs + the submit handler in
    # main.py. Stored as JSON (list[str]) so a rename remains stable
    # and DB rows stay human-readable. Default empty = "no submissions
    # accepted yet"; the admin populates via the /competitions edit
    # form on the SPA.
    compatible_harness_slugs: list[str] | None = Field(
        default_factory=list, sa_column=Column(JSON),
    )


class Harness(SQLModel, table=True):
    """A PERFORMANCE oracle — the component that measures HOW WELL a
    solution runs (runtime, memory, percentiles, runtime energy).

    Kept named `Harness` for schema stability; conceptually it's the
    performance-oracle half of the two-oracle model. Correctness verdicts
    (Accepted / Wrong Answer / RE / TLE / CE) live on `CorrectnessOracle`
    below. LeetCode's online judge conveniently does BOTH — the seeded
    "leetcode" rows in `harness` and `correctnessoracle` cover legacy data.

    Binding is layered: Competition.harness_id sets the default performance
    oracle for every submission in that contest; Submission.harness_id can
    override it (re-judge an existing set under a new perf harness);
    Run.harness_id records the oracle that actually produced this run's
    numbers. Same layering applies to `correctness_oracle_id` on those
    three tables.
    """
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)     # stable id used in URLs / configs
    name: str                                       # display name
    # leetcode      — LeetCode's online judge (runtime_ms / memory_mb / percentile)
    # arena-local   — first-party harness that runs code locally
    # custom        — anything else (HumanEval unittest runner, MBPP, …)
    kind: str = Field(default="custom", index=True)
    description: str | None = None                 # markdown blurb
    # Free-form JSON — endpoint URLs, timeouts, docker image tag, credential
    # references, whatever the specific harness needs to be reproducible.
    config: dict | None = Field(default=None, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)  # active | retired
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_user_id: int | None = Field(default=None, foreign_key="user.id")


class CorrectnessOracle(SQLModel, table=True):
    """A CORRECTNESS oracle — the component that answers "did this solution
    solve the problem?". Produces the verdict (Accepted / Wrong Answer /
    Runtime Error / Time Limit Exceeded / Compile Error / …) and the
    error_log the client can feed back into a next-round prompt.

    Decoupled from `Harness` (the performance oracle) so the two can be
    mixed and matched: e.g. LeetCode's judge for correctness paired with
    an in-house RAPL-instrumented harness for performance, or a local
    unit-test runner for correctness paired with LeetCode's perf numbers.
    Same layered binding as Harness (Competition default → Submission
    override → Run captures what actually judged the row).

    Shape mirrors `Harness` intentionally so the same operator UX applies
    — one registry, two tabs, no cognitive overhead.
    """
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)     # stable id used in URLs / configs
    name: str
    # leetcode         — LeetCode's Accepted / WA / RE / TLE / CE verdicts
    # unittest         — a local pytest / gtest / cargo-test runner
    # humaneval        — the HumanEval canonical test suite
    # custom           — anything else
    kind: str = Field(default="custom", index=True)
    description: str | None = None                 # markdown blurb
    # Free-form JSON — test-suite path, docker image tag, endpoint URL,
    # timeouts, whatever this specific oracle needs to reproduce a verdict.
    config: dict | None = Field(default=None, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)  # active | retired
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_user_id: int | None = Field(default=None, foreign_key="user.id")


class Agent(SQLModel, table=True):
    """A PRODUCER of solutions — the agentic harness / workflow that drove the
    model to write the code (e.g. Claude Code, Aider, OpenHands), as distinct
    from the model itself (model_name/model_version on Submission).

    Shape mirrors `Harness` / `CorrectnessOracle` intentionally so the same
    operator UX and resolution logic apply — one registry, one slug on the
    wire, a layered default. The seeded "direct" agent (no agent loop, the
    model prompted once) is the legacy default: every submission that predates
    this abstraction, and every new submission that names no agent, resolves to
    it, so pre-feature data stays valid and comparable.

    The leaderboard competitor identity is the (agent, model) pair, so a run
    made with an agentic harness ranks apart from a direct run of the same
    model. `version` records the agent tool's own version (independent of the
    model version) so two Claude Code releases driving the same model are
    distinguishable.
    """
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)     # stable id used in URLs / configs
    name: str                                       # display name
    # direct   — no agent loop; the model was prompted once (the legacy default)
    # agentic  — a tool-using / multi-step coding agent (Claude Code, Aider, …)
    # custom   — anything else
    kind: str = Field(default="agentic", index=True)
    # Version of the agent TOOL itself (not the model). NULL for "direct" and
    # for agents whose version the operator did not record.
    version: str | None = Field(default=None)
    description: str | None = None                 # markdown blurb
    # Free-form JSON — the harness repo/commit, config file, tool settings,
    # whatever makes the agent run reproducible.
    config: dict | None = Field(default=None, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)  # active | retired
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_user_id: int | None = Field(default=None, foreign_key="user.id")


class CompetitionProblem(SQLModel, table=True):
    """Many-to-many membership join. Composite primary key so the same
    problem can appear in many competitions without dup rows."""
    competition_id: int = Field(foreign_key="competition.id", primary_key=True)
    problem_id: int = Field(foreign_key="problem.id", primary_key=True)


class BlogPost(SQLModel, table=True):
    """A blog post displayed on the /blog page as a card and at /blog/{slug}
    in full. `body` is markdown; `cover_image_url` is rendered as the card's
    header image when present. Draft posts are admin-only previewable; only
    `status=='published'` rows appear in public listings."""
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)          # stable URL id
    title: str
    summary: str | None = None                          # one-paragraph teaser
    body: str = ""                                      # markdown
    cover_image_url: str | None = None
    author_name: str | None = None                      # display author (free text)
    status: str = Field(default="draft", index=True)    # draft | published | retired
    published_at: datetime | None = None                # filled when status flips to published
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_user_id: int | None = Field(default=None, foreign_key="user.id")


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


class AppSetting(SQLModel, table=True):
    """Runtime-editable configuration overrides, keyed by a dotted setting name
    (e.g. `dispatch.repeats`). An override here wins over the environment
    variable / code default at USE time, so an admin can tune dispatch behaviour
    from the Variables tab without an env change or a restart. A missing row
    means "use the env/default" — so an empty table reproduces legacy behaviour
    exactly. See app.app_settings for the registry and typed accessors."""
    key: str = Field(primary_key=True, description="dotted setting name")
    value: str = Field(description="stringified value; cast per the registry")
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by_user_id: int | None = Field(default=None, foreign_key="user.id")
