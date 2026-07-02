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

    pass_at_1: float | None = None        # unbiased estimator over k samples
    pass_at_k: float | None = None        # k = n_samples_per_problem
    pass_at_full: float | None = None     # 1 if any of k samples accepted
    avg_runtime_ms: float | None = None
    avg_memory_mb: float | None = None
    best_runtime_ms: float | None = None
    best_memory_mb: float | None = None
    avg_runtime_percentile: float | None = None
    avg_memory_percentile: float | None = None

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
