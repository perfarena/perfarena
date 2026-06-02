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
    # Headline (best of the 5 dispatch repeats for this attempt). LeetCode's
    # measured-ms is noisy on a per-run basis (server load, JIT state), so
    # the BEST of N is the closest thing to "what this code can do" — using
    # it as the headline ranking criterion drops a lot of the within-attempt
    # noise documented in docs/leetcode-percentile-semantics.md.
    runtime_ms: float | None              # = best_runtime_ms (min across runs)
    memory_mb: float | None               # = best_memory_mb (min across runs)
    runtime_percentile: float | None      # = best_runtime_percentile (max across runs)
    memory_percentile: float | None       # = best_memory_percentile (max across runs)
    # Mean across the 5 dispatch repeats — shipped alongside the best so the
    # UI can show "best (avg)" pairs and the user can see the noise band
    # rather than a single number. Pre-existing rows that lack the per-run
    # detail will leave these as None.
    avg_runtime_ms: float | None = None
    avg_memory_mb: float | None = None
    avg_runtime_percentile: float | None = None
    avg_memory_percentile: float | None = None
    n_runs: int | None = None
    n_accepted_runs: int | None = None


class ProblemDetail(ProblemPublic):
    content: str
    hints: str | None
    # Starter-code shape per language (e.g. "python3" → "class Solution:\n    def …").
    # Tells the client driver and prompt expander exactly which class/method
    # signature LeetCode expects for this problem. Populated by
    # `app.fetch_code_snippets`; empty dict when not yet fetched.
    code_snippets: dict[str, str] = {}
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
    generation_energy_j: float | None = Field(
        default=None,
        description=(
            "Energy used by the generation process for this solution, in "
            "Joules. Optional — reported when the submit client has a power "
            "probe (NVML, RAPL, provider telemetry). Summed into "
            "Submission.total_generation_energy_j."
        ),
    )
    prefailed_reason: str | None = Field(
        default=None,
        description=(
            "Set when the client already knows the model's response isn't a "
            "valid solution (e.g. wrong language, no code, malformed). The "
            "attempt is recorded with `code` + `trace` (raw response) and "
            "dispatch synthesizes a failure Run instead of calling LeetCode."
        ),
    )
    cost_usd: float | None = Field(
        default=None,
        description="$ cost of the LLM call that produced this sample (0 for "
                    "Ollama / local models).",
    )
    response_tokens: int | None = Field(
        default=None,
        description="Token count of the code-only response (excludes any "
                    "reasoning channel). Best-effort estimate from the client.",
    )
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
    # Iterative-refinement chain link. Both optional — legacy / single-shot
    # attempts post round_index=0 (default) and previous_attempt_id=None.
    round_index: int = Field(default=0, ge=0)
    previous_attempt_id: int | None = Field(
        default=None,
        description=(
            "id of the round-(N-1) attempt for the same problem, in this same "
            "submission. The server validates the link belongs to the same "
            "submission_id and problem before accepting."
        ),
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
    # Experiment knobs (default to single-shot, no hints, no specific target —
    # i.e. legacy behavior). The client driver in iterative_refine.py sets
    # these to drive the prompt expansion + matches them in feedback.
    n_rounds: int = Field(default=1, ge=1, le=10,
                          description="Total rounds in this refinement chain")
    include_hints: bool = Field(default=False,
                                description="Include LeetCode's problem hints in the prompt")
    optimization_target: str | None = Field(
        default=None,
        pattern="^(latency|memory)$",
        description="Per-attempt optimization steer; one of latency|memory or null",
    )
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
    n_rounds: int = 1
    include_hints: bool = False
    optimization_target: str | None = None
    # Cost + token totals across all attempts (k samples included).
    total_cost_usd: float | None = None
    total_response_tokens: int | None = None
    # Total generation wall-clock (ms) summed across all attempts.
    total_generation_ms: int | None = None
    # Total generation energy (Joules) summed across all attempts. None when
    # no attempt reported it — the UI omits the stat in that case.
    total_generation_energy_j: float | None = None


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
    # Generation energy of the best attempt (Joules). None for the LC-only
    # data path; included so the comparison histogram can render an energy
    # bar when at least one model in scope reports it.
    generation_energy_j: float | None = None


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


# ---- prompt-strategy comparison (one model, many prompts) -------------------

class ComparePromptRow(BaseModel):
    """One row of the /api/compare/prompts response — aggregated metrics for
    one PromptTemplate within the (model, version, language, problems) scope.
    Same metric vocabulary as `CompareSummaryRow`, but the axis is the prompt
    strategy rather than the model."""
    prompt_id: int
    prompt_name: str
    status: str                            # active | retired
    n_submissions: int
    n_problems_accepted: int
    n_problems_attempted: int
    pass_at_1_max: float | None
    pass_at_k_max: float | None
    pass_at_full_max: float | None
    avg_runtime_ms: float | None
    best_runtime_ms: float | None
    avg_memory_mb: float | None
    best_memory_mb: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None
    time_human_avg: float | None
    time_human_expert: float | None
    mem_human_avg: float | None
    mem_human_expert: float | None
    leading_submission_id: int | None


class ComparePromptsWinners(BaseModel):
    """Per-metric winning prompt across the compared strategies. The composite
    `overall` is the prompt with the most per-metric wins (pass@1 tie-break)."""
    best_pass_at_1: str | None
    best_pass_at_k: str | None
    best_pass_at_full: str | None
    most_problems_solved: str | None
    fastest_avg_runtime: str | None
    leanest_avg_memory: str | None
    best_runtime_percentile: str | None
    best_memory_percentile: str | None
    overall: str | None
    overall_wins_by_prompt: dict[str, int]


class ComparePromptsResponse(BaseModel):
    """Head-to-head comparison of prompt strategies within one experiment
    cell (model × version × language). Lets you read off whether a richer
    prompt (e.g. explain-then-code) buys correctness OR performance over the
    plain template, holding the model fixed."""
    model_name: str
    model_version: str | None
    language: str | None
    problem_slugs: list[str] | None
    prompt_ids: list[int]
    rows: list[ComparePromptRow]
    winners: ComparePromptsWinners


# ---- cross-language correctness × performance analysis ----------------------

class LangAnalysisRow(BaseModel):
    """Per-language aggregate of correctness + performance, for the
    cross-language grid. Means are over judged submissions in that language."""
    language: str
    n_submissions: int
    n_models: int
    mean_pass_at_1: float | None
    best_pass_at_1: float | None
    mean_pass_at_k: float | None
    mean_runtime_percentile: float | None
    mean_memory_percentile: float | None
    # Pearson r between pass@1 and runtime percentile WITHIN this language
    # (None when < 3 points). Positive ⇒ more-correct submissions also rank
    # higher on runtime; near-0 ⇒ correctness & speed are decoupled.
    pass1_runtime_corr: float | None


class AnalysisPoint(BaseModel):
    """One judged submission — a single dot in the correctness-vs-performance
    scatter."""
    submission_id: int
    model_name: str
    language: str
    pass_at_1: float | None
    pass_at_k: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None


class AnalysisResponse(BaseModel):
    by_language: list[LangAnalysisRow]
    points: list[AnalysisPoint]
    n_points: int
    # Overall Pearson r across ALL judged submissions (all languages pooled).
    pass1_vs_runtime_corr: float | None
    pass1_vs_memory_corr: float | None
    passk_vs_runtime_corr: float | None


# ---- efficiency-discrimination by language ---------------------------------

class DiscriminationLangSummary(BaseModel):
    """How well a language's LeetCode runtime histograms can DISTINGUISH a fast
    model from a slow one. A 'discriminative' problem has real runtime spread;
    a 'noise-floor' one has the whole population piled in one bucket."""
    language: str
    n_measured: int
    n_discriminative: int
    pct_discriminative: float
    mean_mode_share: float | None
    median_spread_ratio: float | None
    # counts of problems whose dominant-bucket share falls in each 10-pt band
    # [0-10), [10-20), …, [90-100]. Right-skew = lots of noise-floor problems.
    mode_share_histogram: list[int]


class DiscriminationCrossRow(BaseModel):
    """A problem measured in 2+ languages — shows where discrimination capacity
    flips across languages (e.g. discriminative in Python, noise-floor in Java)."""
    title_slug: str
    level: str | None
    by_language: dict[str, dict]   # lang -> {mode_share, discriminative, spread_ratio}
    flips: bool                    # True when discriminative in some langs, not others


class DiscriminationResponse(BaseModel):
    languages: list[DiscriminationLangSummary]
    cross_language: list[DiscriminationCrossRow]
    n_cross_language: int


class RegisteredModelPublic(BaseModel):
    id: int
    name: str
    version: str
    provider: str
    endpoint: str | None
    params: dict | None
    notes: str | None
    # Rich metadata. Auto-stubbed rows have these all null until an admin
    # populates them via the /models edit form.
    description: str | None = None
    model_type: str | None = None       # chat | instruct | reasoning | coder | …
    family: str | None = None           # qwen | llama | gpt | claude | …
    provenance: str | None = None       # links / citations / model-card URL (markdown)
    status: str                         # active | retired | unverified
    added_at: datetime


class RegisteredModelOverview(RegisteredModelPublic):
    """One row of the /api/models list — the registered model plus the
    activity stats the /models page surfaces in its header. Stats are
    computed from non-deleted Submissions matching (name, version)."""
    n_submissions: int = 0
    n_judged: int = 0
    last_submitted_at: datetime | None = None
    languages: list[str] = []
    best_pass_at_1: float | None = None


class RegisteredModelIn(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    endpoint: str | None = None
    params: dict | None = None
    notes: str | None = None
    description: str | None = None
    model_type: str | None = None
    family: str | None = None
    provenance: str | None = None
    status: str = "active"


class CompetitionPublic(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None
    kind: str
    status: str
    created_at: datetime
    # Activity stats — cheap to compute alongside the list query.
    n_problems: int = 0
    n_submissions: int = 0
    n_models: int = 0


class CompetitionDetail(CompetitionPublic):
    """Slug-keyed detail view: identity + the membership list + the per-
    competition leaderboard's top entries. The /competitions/:slug page
    renders this directly."""
    problems: list[ProblemPublic] = []          # defined above in this file
    languages: list[str] = []
    top_models: list[dict] = []                 # [{model_name, version, pass_at_1, …}, …]


class CompetitionIn(BaseModel):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    kind: str = "custom"
    status: str = "active"


class CompetitionProblemsIn(BaseModel):
    """Bulk membership update: replace this competition's problems with the
    given slug list. Empty list = clear membership."""
    problem_slugs: list[str]


class BlogPostPublic(BaseModel):
    id: int
    slug: str
    title: str
    summary: str | None
    body: str
    cover_image_url: str | None
    author_name: str | None
    status: str
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BlogPostCard(BaseModel):
    """Slimmer shape for the /blog index — drops the full body since cards
    only render the cover, title, summary, and date."""
    id: int
    slug: str
    title: str
    summary: str | None
    cover_image_url: str | None
    author_name: str | None
    status: str
    published_at: datetime | None
    created_at: datetime


class BlogPostIn(BaseModel):
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str | None = None
    body: str = ""
    cover_image_url: str | None = None
    author_name: str | None = None
    status: str = "draft"           # draft | published | retired


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
    # Verbatim LeetCode /check/ response for this run. Surfaces fields the
    # enumerated error_log doesn't cover (status_code, state, pretty_lang,
    # task_finish_time, …) so the UI can show whatever LC actually returned.
    # None on Accepted runs (the diagnostic isn't useful there), on fake-
    # mode runs, and on synthetic-failure dispatch results.
    leetcode_full_response: dict | None = None


class AttemptResult(BaseModel):
    attempt_id: int
    problem_slug: str
    problem_title: str
    level: str
    status: str            # "Accepted" | a rejection reason | "queued"
    accepted: bool
    # Refinement-chain link — same defaults as the DB.
    round_index: int = 0
    previous_attempt_id: int | None = None
    # Cost + tokens of the LLM call that produced this attempt.
    cost_usd: float | None = None
    response_tokens: int | None = None
    # Generation wall-clock of this attempt's solution (ms).
    wall_clock_ms: int | None = None
    # Generation energy of this attempt's solution (Joules). None when the
    # client didn't measure it (LeetCode never reports it).
    generation_energy_j: float | None = None
    # Client-detected malformed-response reason — set ⇒ this attempt was
    # marked failed without LeetCode dispatch (the trace still holds the
    # raw model response).
    prefailed_reason: str | None = None
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
