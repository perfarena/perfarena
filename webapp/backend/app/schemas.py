"""Request/response schemas (Pydantic v2)."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str
    org: str | None = None
    motivation: str | None = None
    captcha_token: str
    captcha_answer: str
    # Optional for backward compat; new sign-ups should set one so they can
    # later log in with email + password instead of pasting the API key.
    password: str | None = Field(default=None, min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    email: str
    display_name: str
    org: str | None
    role: str
    api_key: str


class ProblemPublic(BaseModel):
    id: int
    title_slug: str
    title: str
    level: str
    tags: list[str]


class ProblemTopSolution(BaseModel):
    submission_id: int
    model_name: str
    model_version: str
    language: str
    submitter: str
    submitter_slug: str
    status_msg: str
    runtime_ms: float | None
    memory_mb: float | None
    runtime_percentile: float | None
    memory_percentile: float | None


class ProblemDetail(ProblemPublic):
    content: str
    hints: str | None
    testcases: int | None
    n_attempts: int
    n_accepted: int
    top_solutions: list[ProblemTopSolution]
    # One row per language that has at least one judged attempt for this
    # problem — the *best* solution in that language. Lets a viewer compare
    # how the same problem fares across languages at a glance.
    best_per_language: list[ProblemTopSolution] = []
    # Attempts that produced a Run, broken down by language.
    n_attempts_by_language: dict[str, int] = {}
    n_accepted_by_language: dict[str, int] = {}


class AttemptIn(BaseModel):
    title_slug: str
    code: str
    inference_trace: str = Field(min_length=20, description="Human-readable reasoning summary; min 20 chars")
    trace: dict | None = Field(
        default=None,
        description=(
            "Verifiable provenance. Recommended keys: prompt, system_prompt, "
            "raw_response, reasoning, model, provider, endpoint, params, "
            "created_at, provider_meta."
        ),
    )
    wall_clock_ms: int | None = None
    cost_usd: float | None = None
    model_digest: str | None = Field(
        default=None,
        description=(
            "Explicit model identity digest (Ollama sha256, HF revision SHA, "
            "provider system_fingerprint, …). If omitted the platform derives "
            "a deterministic one from model name/version/params/provider meta."
        ),
    )
    trace_sig: str | None = Field(
        default=None,
        description="Optional base64 Ed25519 signature over the leaf trace hash.",
    )
    trace_signer: str | None = Field(
        default=None,
        description="Optional base64 Ed25519 public key matching trace_sig.",
    )


class SubmissionIn(BaseModel):
    model_name: str
    model_version: str
    language: str
    prompt_template: str = Field(min_length=10)
    model_params: dict
    # The "k" of pass@k — declared upfront, locked at creation. With k > 1
    # the submitter must provide exactly k attempts per problem to be
    # considered complete, and pass@k is computed from those samples.
    n_samples_per_problem: int = Field(default=1, ge=1, le=200)
    # Optional reference to the registered PromptTemplate that produced these
    # samples. When set, `prompt_template` should match the resolved text but
    # the prompt_id is the canonical link the UI uses to filter / group.
    prompt_id: int | None = None
    attempts: list[AttemptIn] = Field(default_factory=list)


class AppendAttemptsIn(BaseModel):
    attempts: list[AttemptIn] = Field(min_length=1)


class ProgressEntry(BaseModel):
    submission_id: int
    language: str
    model_name: str
    model_version: str
    attempted: int
    accepted: int
    queued: int
    total_supported: int
    missing_count: int
    fully_accepted: bool
    created_at: datetime


class SubmissionProgress(BaseModel):
    submission_id: int
    language: str
    attempted: int
    accepted: int
    queued: int
    total_supported: int
    fully_accepted: bool
    missing: list[str]
    rejected: list[str]


class SubmissionPublic(BaseModel):
    id: int
    user_display_name: str
    user_org: str | None
    submitter_slug: str
    model_name: str
    model_version: str
    language: str
    status: str
    created_at: datetime
    pass_at_1: float | None
    avg_runtime_ms: float | None
    avg_memory_mb: float | None
    best_runtime_ms: float | None
    best_memory_mb: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None
    time_human_avg: float | None
    time_human_expert: float | None
    mem_human_avg: float | None
    mem_human_expert: float | None
    total_solved: int
    total_problems: int
    n_attempts: int = 0
    n_samples_per_problem: int = 1
    pass_at_k: float | None = None
    pass_at_full: float | None = None
    fully_accepted: bool = False
    leaderboard_eligible: bool = False
    leaderboard_reason: str | None = None   # eligible | incomplete | pass-rate-too-low | not-judged
    prompt_id: int | None = None
    prompt_name: str | None = None


class RankingEntry(BaseModel):
    rank: int
    model_name: str
    model_version: str
    language: str
    pass_at_1: float
    avg_runtime_ms: float | None
    avg_memory_mb: float | None
    best_runtime_ms: float | None
    best_memory_mb: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None
    time_human_avg: float | None
    time_human_expert: float | None
    mem_human_avg: float | None
    mem_human_expert: float | None
    total_solved: int
    total_problems: int
    n_attempts: int = 0
    n_samples_per_problem: int = 1
    pass_at_k: float | None = None
    pass_at_full: float | None = None
    submission_id: int
    submitter: str
    submitter_slug: str
    prompt_id: int | None = None
    prompt_name: str | None = None


class RunPublic(BaseModel):
    problem_slug: str
    status_msg: str
    runtime_ms: float | None
    memory_mb: float | None
    runtime_percentile: float | None
    memory_percentile: float | None
    total_correct: int | None
    total_testcases: int | None
    error_log: "RunErrorLog | None" = None


class ModelDetailLanguageStat(BaseModel):
    language: str
    n_submissions: int
    best_pass_at_1: float | None
    best_avg_runtime_ms: float | None
    best_avg_memory_mb: float | None
    best_avg_runtime_percentile: float | None
    best_avg_memory_percentile: float | None
    fully_accepted_any: bool
    leaderboard_eligible_any: bool


class ModelDetail(BaseModel):
    name: str
    versions: list[str]
    languages: list[str]
    n_submissions: int
    best_pass_at_1: float | None
    best_avg_runtime_percentile: float | None
    best_avg_memory_percentile: float | None
    submitters: list[str]
    submissions: list[SubmissionPublic]
    by_language: list[ModelDetailLanguageStat] = []


class SubmitterDetail(BaseModel):
    slug: str
    display_name: str
    org: str | None
    n_submissions: int
    n_models: int
    languages: list[str]
    submissions: list[SubmissionPublic]


class ModelListEntry(BaseModel):
    name: str
    n_submissions: int
    languages: list[str]


class CompareModelOnProblem(BaseModel):
    accepted: bool
    submission_id: int | None
    attempt_id: int | None
    language: str | None
    status: str | None
    runtime_ms: float | None
    memory_mb: float | None
    runtime_percentile: float | None
    memory_percentile: float | None


class CompareProblemRow(BaseModel):
    problem_slug: str
    problem_title: str
    level: str
    per_model: dict[str, CompareModelOnProblem]   # model_name → best attempt


class CompareSummaryRow(BaseModel):
    model_name: str
    n_submissions: int
    n_problems_accepted: int
    n_problems_attempted: int
    languages: list[str]
    pass_at_1_max: float | None       # best across the submissions in scope
    pass_at_k_max: float | None
    pass_at_full_max: float | None
    avg_runtime_ms: float | None      # mean over each problem's best accepted attempt
    best_runtime_ms: float | None     # best per-problem minimum, averaged
    avg_memory_mb: float | None
    best_memory_mb: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None
    time_human_avg: float | None
    time_human_expert: float | None
    mem_human_avg: float | None
    mem_human_expert: float | None
    leading_submission_id: int | None   # the submission contributing the headline pass@1


class CompareWinners(BaseModel):
    """Per-metric winner across the compared models, plus a composite overall
    winner = model with the most per-metric wins (pass@1 tie-break)."""
    best_pass_at_1: str | None
    best_pass_at_k: str | None
    best_pass_at_full: str | None
    most_problems_solved: str | None
    fastest_avg_runtime: str | None
    leanest_avg_memory: str | None
    best_runtime_percentile: str | None
    best_memory_percentile: str | None
    overall: str | None
    overall_wins_by_model: dict[str, int]   # {model_name: number of metrics won}


class CompareResponse(BaseModel):
    models: list[str]
    language: str | None
    problem_slugs: list[str] | None
    summary: list[CompareSummaryRow]
    problems: list[CompareProblemRow]
    winners: CompareWinners


class RegisteredModelPublic(BaseModel):
    id: int
    name: str
    version: str
    provider: str
    endpoint: str | None
    params: dict | None
    notes: str | None
    status: str
    added_at: datetime


class RegisteredModelIn(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    endpoint: str | None = None
    params: dict | None = None
    notes: str | None = None
    status: str = "active"


class PromptTemplatePublic(BaseModel):
    id: int
    name: str
    system_prompt: str
    user_prompt_template: str
    language_hint: str | None
    notes: str | None
    status: str
    created_at: datetime


class PromptTemplateIn(BaseModel):
    name: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt_template: str = Field(min_length=1)
    language_hint: str | None = None
    notes: str | None = None
    status: str = "active"


class BaselineRow(BaseModel):
    title_slug: str
    language: str
    runtime_ms_avg: float
    runtime_ms_expert: float
    memory_mb_avg: float
    memory_mb_expert: float
    source: str = "leetcode-distribution"


class BaselineBulkIn(BaseModel):
    baselines: list[BaselineRow] = Field(min_length=1)


class RunErrorLog(BaseModel):
    """Diagnostic detail from LeetCode (Runtime/Compile/Wrong Answer) or from
    our dispatch layer (Auth failed, Dispatch parse error, …)."""
    runtime_error: str | None = None
    full_runtime_error: str | None = None
    compile_error: str | None = None
    full_compile_error: str | None = None
    last_testcase: str | None = None
    expected_output: str | None = None
    code_output: str | None = None
    std_output: str | None = None
    note: str | None = None              # our classifier note (auth failed, etc.)
    raw_stdout: str | None = None        # leetbot subprocess stdout tail
    raw_stderr: str | None = None        # leetbot subprocess stderr tail


class AttemptRunDetail(BaseModel):
    run_index: int
    status_msg: str
    runtime_ms: float | None
    memory_mb: float | None
    runtime_percentile: float | None
    memory_percentile: float | None
    total_correct: int | None
    total_testcases: int | None
    error_log: RunErrorLog | None = None


class AttemptResult(BaseModel):
    attempt_id: int
    problem_slug: str
    problem_title: str
    level: str
    status: str            # "Accepted" | a rejection reason | "queued"
    accepted: bool
    n_runs: int
    n_accepted_runs: int
    avg_runtime_ms: float | None
    best_runtime_ms: float | None
    avg_memory_mb: float | None
    best_memory_mb: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None
    submitted_at: datetime | None
    runs: list[AttemptRunDetail]
    # Same shape submission_detail returns on its traces[] — so the
    # AttemptResults page can render the same "Provenance" and "Log entry"
    # expanders SubmissionDetail does.
    inference_trace: str | None = None
    trace: dict | None = None
    provenance: dict | None = None
