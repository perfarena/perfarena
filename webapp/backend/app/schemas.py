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


class CompetitionRef(BaseModel):
    """Light-weight pointer to a competition — slug + name only. Used
    everywhere we need to show "this thing belongs to competition X"
    without dragging stats / leaderboard / membership lists along."""
    slug: str
    name: str


class ProblemPublic(BaseModel):
    id: int
    title_slug: str
    title: str
    level: str
    tags: list[str]
    # Competitions this problem is a member of. Usually one — the platform
    # pivoted to "every problem lives under a competition" — but the
    # CompetitionProblem join is many-to-many so a problem MAY appear in
    # several. Empty list = orphan (no competition links it; legacy data).
    competitions: list[CompetitionRef] = []


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
    # Runtime energy of the solution (Joules) — best of N runs is the
    # headline (least energy consumed), plus the noise-band average. Null
    # unless the scoring harness measured execution energy.
    runtime_energy_j: float | None = None
    avg_runtime_energy_j: float | None = None
    runtime_energy_percentile: float | None = None
    avg_runtime_energy_percentile: float | None = None
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
    # When this Problem is an anti-contamination variant of another (created
    # by app.rewrite_competition), surface the original so researchers can
    # verify the mapping at a glance. None for canonical problems. The model
    # being evaluated only sees the variant's content/snippets — these
    # fields are metadata for human reviewers in the SPA.
    original_slug: str | None = None
    original_title: str | None = None
    original_function_name: str | None = None
    function_name: str | None = None
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
    inference_trace: str = Field(min_length=1, description="Human-readable reasoning summary; must be non-empty. A terse reasoning (e.g. a model that just says 'Simple.') is valid provenance and must not be rejected; the 20-char minimum used to drop such solutions.")
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
    prompt_tokens: int | None = Field(
        default=None,
        description="Token count of the PROMPT for the LLM call that produced "
                    "this attempt. Lets the backend recompute cost dynamically "
                    "when the operator edits per-model pricing on /models. "
                    "Optional for older clients; when omitted the snapshot "
                    "cost_usd above is the only cost the SPA can show.",
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
    stop_on_accept: bool = Field(
        default=False,
        description=(
            "Retry-until-accepted workflow. When true, the client's round "
            "loop stops appending attempts for a problem the moment one "
            "lands `Accepted` — n_rounds is a MAXIMUM, not a fixed count. "
            "When false (default), all n_rounds rounds run regardless."
        ),
    )
    # Which competition this submission targets. The server uses the
    # competition's problem count for total_problems / pass@k denominators,
    # and rejects any attempt whose title_slug isn't in that competition's
    # membership. Default = leetcode-99-perf so the legacy sweep behaviour
    # (canonical 99 problems) is preserved when callers don't pass anything.
    competition_slug: str | None = Field(
        default="leetcode-99-perf",
        description="Competition slug — defaults to leetcode-99-perf. "
                    "Pass-at-k denominator is the size of this competition.",
    )
    # REQUIRED. Competition and harness are fully decoupled: the
    # competition owns a problem set + rules, the harness owns *how
    # solutions are measured*. Every submission picks its own harness
    # explicitly. The old "inherit from competition" fallback is gone.
    #
    # A single competition can therefore be run under N different
    # harnesses side-by-side (LC's judge for one bundle, arena-local
    # for another, a custom in-house rig for a third) — the
    # leaderboard groups by (competition × harness).
    harness_slug: str = Field(
        min_length=1,
        description="Harness that will measure this submission's runs. "
                    "REQUIRED — the competition no longer supplies a "
                    "default. Use the harness's slug from /api/harnesses.",
    )
    # Optional correctness oracle. When set (and the target oracle has
    # kind=arena-local/custom + config.endpoint), the dispatcher calls
    # its POST /validate BEFORE the perf harness's /measure and skips
    # /measure on any non-Accepted verdict. Independent of the perf
    # harness. When null, the perf harness's own status_msg is the
    # correctness signal (LC gives both; arena-local's perfarena_handler
    # runs `make validate` inside `make measure`).
    correctness_oracle_slug: str | None = Field(
        default=None,
        description="Optional correctness oracle. When set, the dispatcher "
                    "calls this oracle's /validate before /measure and "
                    "skips /measure when the correctness verdict is not "
                    "Accepted. When null, correctness comes from the "
                    "perf harness's own response.",
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
    # Solution-runtime energy (Joules) — third perf axis alongside runtime
    # / memory. Populated by execution-instrumented harnesses; null on
    # every LeetCode-scored submission. When no submission in scope has
    # a value the SPA hides the entire column.
    avg_runtime_energy_j: float | None = None
    best_runtime_energy_j: float | None = None
    avg_runtime_energy_percentile: float | None = None
    best_runtime_energy_percentile: float | None = None
    # Submission-level per function call rollup (B3): avg_runtime_ms IS per function call time and
    # avg_runtime_energy_j IS per function call energy under the looped methodology; these
    # expose them explicitly (µs / J) plus the solve count behind each
    # measurement. NULL on LeetCode-scored submissions.
    avg_time_us_per_op: float | None = None
    avg_energy_j_per_op: float | None = None
    avg_iterations: float | None = None
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
    # Retry-until-accepted workflow flag — n_rounds acts as a MAXIMUM when
    # true; the client stops once a problem lands Accepted. False (default)
    # means every problem walks all n_rounds regardless.
    stop_on_accept: bool = False
    # Cost + token totals across all attempts (k samples included).
    # total_cost_usd is the LIVE value: when the model has pricing rows
    # set on /models AND attempts have stored prompt+response tokens, we
    # recompute from the current rates and sum across attempts (falling
    # back to each attempt's snapshot when its tokens are missing). When
    # the model has no rates configured, this stays at the historical
    # snapshot below. `total_cost_usd_snapshot` preserves the original
    # at-submit value so an operator can still see "what we actually
    # paid" vs "what it would cost today" by comparing the two.
    total_cost_usd: float | None = None
    total_cost_usd_snapshot: float | None = None
    total_response_tokens: int | None = None
    # Per-attempt medians — let the SPA rank submissions by "typical $/
    # tokens spent per problem" rather than just the total, which scales
    # with attempt count and would unfairly punish a submission that
    # covers more problems. Both are null when no attempt in the
    # submission has the corresponding field populated.
    median_response_tokens_per_attempt: float | None = None
    median_cost_usd_per_attempt: float | None = None
    median_generation_ms_per_attempt: float | None = None
    # Total generation wall-clock (ms) summed across all attempts.
    total_generation_ms: int | None = None
    # Total generation energy (Joules) summed across all attempts. None when
    # no attempt reported it — the UI omits the stat in that case.
    total_generation_energy_j: float | None = None
    # Competition this submission targets. Used by the SPA tables to render
    # a pill in the new "Competition" column. None on legacy rows where the
    # backfill script wasn't run.
    competition_slug: str | None = None
    competition_name: str | None = None
    # PERFORMANCE oracle for this submission (runtime / memory / percentile
    # / energy). Resolved: submission override → competition default → NULL.
    # Practically always populated after the startup backfill.
    harness_slug: str | None = None
    harness_name: str | None = None
    # CORRECTNESS oracle for this submission (Accepted / WA / RE / TLE
    # verdicts). Same resolution + backfill rules as harness. Decoupled
    # so a client can tell "which judge said this was correct" apart from
    # "which harness measured how fast it was".
    correctness_oracle_slug: str | None = None
    correctness_oracle_name: str | None = None
    # Measurement provenance (Parts 2+3): the single energy tool + machine
    # this submission was measured on. Energy/time numbers are only
    # comparable within one (energy_tool, machine) pair. NULL on
    # LeetCode-scored submissions — those ran on LeetCode's judges, so the
    # machine is "unknown" and no execution energy was tooled.
    energy_tool: str | None = None
    machine_id: int | None = None
    machine_name: str | None = None
    # Opaque unique identifier for this submission's measurement run, for
    # correlating with external measurement logs.
    run_uid: str | None = None
    # Last attempt's `logged_at` for this submission — when something
    # actually moved on the row. The /submissions table sorts by this
    # rather than `created_at` so a long-running sweep stays at the
    # top of the list while it's still landing attempts. None when the
    # submission has no attempts yet (just created, dispatch hasn't
    # fired). Falls back to `created_at` in the UI.
    last_attempt_at: datetime | None = None


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
    # Solution-runtime energy (Joules) aggregated across accepted attempts
    # — mirrors avg/best_runtime_ms. Null when no attempt in this submission
    # was scored by an execution-instrumented harness.
    avg_runtime_energy_j: float | None = None
    best_runtime_energy_j: float | None = None
    avg_runtime_energy_percentile: float | None = None
    best_runtime_energy_percentile: float | None = None
    # Measurement provenance (B4): the machine + energy tool this entry was
    # measured on. NULL for LeetCode-scored rows. Lets the leaderboard show /
    # filter "which machine", since energy is only comparable within one.
    machine_name: str | None = None
    energy_tool: str | None = None
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
    # Competition this submission targets — None on legacy rows.
    competition_slug: str | None = None
    competition_name: str | None = None
    # PERFORMANCE oracle (Harness) whose numbers this row is scored on.
    # Resolved per submission: explicit override → competition default →
    # NULL. UI renders a pill linking to /harnesses/{slug}.
    harness_slug: str | None = None
    harness_name: str | None = None
    # CORRECTNESS oracle that produced the Accepted / WA / RE / TLE
    # verdict driving pass@1 for this row. Decoupled from the perf oracle
    # above so a client can filter by "judged correct by X" independently
    # of "measured perf by Y".
    correctness_oracle_slug: str | None = None
    correctness_oracle_name: str | None = None
    # Token + cost accounting — surfaced as sortable columns on the SPA
    # rankings table. `total_*` is the sum across every attempt in this
    # submission; `median_*_per_attempt` is the per-attempt median so two
    # submissions with different attempt counts compare on the same axis.
    # All four are null on submissions whose attempts didn't report
    # tokens/cost (e.g. legacy ingests, all-Ollama runs with no cost
    # accounting). `total_cost_usd` is the LIVE value when the model has
    # /models pricing set, falling back to the snapshot per attempt.
    total_response_tokens: int | None = None
    median_response_tokens_per_attempt: float | None = None
    total_cost_usd: float | None = None
    median_cost_usd_per_attempt: float | None = None
    # Generation wall-clock — sum across attempts (ms) + per-attempt median.
    # The median is the apples-to-apples "how long does ONE solution from
    # this model typically take to generate" — comparable across submissions
    # that cover different numbers of problems.
    total_generation_ms: int | None = None
    median_generation_ms_per_attempt: float | None = None
    # Generation energy — sum across attempts (Joules) + per-attempt median.
    # Populated only for submit clients with a power probe (NVML / RAPL /
    # provider telemetry); LeetCode-only submissions leave both null and the
    # UI hides the column. Same shape as the cost/tokens/time totals above
    # so the SPA can treat energy as one more sortable spend axis.
    total_generation_energy_j: float | None = None
    median_generation_energy_j_per_attempt: float | None = None


class RunPublic(BaseModel):
    problem_slug: str
    status_msg: str
    runtime_ms: float | None
    memory_mb: float | None
    runtime_percentile: float | None
    memory_percentile: float | None
    # Runtime energy consumed while executing the solution (Joules) — the
    # third perf axis alongside runtime_ms / memory_mb. Reported only by
    # harnesses that instrument execution; LeetCode never does, so this
    # stays null on every LC-scored run.
    runtime_energy_j: float | None = None
    runtime_energy_percentile: float | None = None
    # Per function call metrics from the looped curated methodology: iterations solved
    # in the measured window, and energy / time normalized to a single solve
    # (the cross-language-comparable numbers). Null on LeetCode-scored runs
    # and on legacy runs measured before the looped methodology.
    iterations: int | None = None
    energy_j_per_op: float | None = None
    time_us_per_op: float | None = None
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
    # RUNTIME energy — the third perf axis, populated by execution-instrumented
    # harnesses (e.g. arena-local). This is the field the head-to-head
    # comparison plots BY DEFAULT when present — it's what actually varies
    # across models solving the same problem. generation_energy_j above is
    # LLM-side (energy spent producing the code); runtime_energy_j is
    # execution-side (energy spent running it).
    runtime_energy_j: float | None = None


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


class CompareByLevelRow(BaseModel):
    """One (model × difficulty level) cell of the comparison's difficulty
    breakdown. Lets the SPA chart Easy / Medium / Hard performance side
    by side so the operator can see which model is strongest at each
    level rather than averaging them together.

    Two metric families coexist on this row:
      * judge-side: pass_rate, runtime/memory (the LeetCode verdict)
      * generator-side: cost_usd, response_tokens, wall_clock_ms (what
        the LLM spent producing the code — the model pays for these
        regardless of whether LC accepted the result, so they're averaged
        over n_attempted problems, not n_accepted)"""
    model_name: str
    level: str                                # "Easy" | "Medium" | "Hard"
    n_attempted: int
    n_accepted: int
    pass_rate: float                          # n_accepted / n_attempted (0 if 0)
    # Judge-side perf (Accepted attempts only — failing code doesn't have
    # a runtime/memory we want to mix in).
    avg_runtime_ms: float | None              # mean over each problem's best accepted
    avg_memory_mb: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None
    # Runtime energy — third perf axis, execution-side. Populated when the
    # scoring harness measures it (arena-local etc.). None on LC-only data
    # paths; the SPA hides the chart entirely when no cell has a value.
    avg_runtime_energy_j: float | None = None
    # Generator-side cost & throughput per PROBLEM in scope. Includes
    # every attempt the model made for that problem (failed attempts
    # cost the same as accepted ones to generate). None when no attempt
    # at this level reported the metric.
    avg_cost_usd: float | None
    avg_response_tokens: float | None
    avg_wall_clock_ms: float | None


class CompareByTagRow(BaseModel):
    """One (model × problem tag) cell. Same metric vocabulary as the
    by_level breakdown — pass_rate, runtime, memory — but the axis is the
    tag (Dynamic Programming, Math, Array, …) rather than difficulty.

    Powers the SPA's three radar charts on /compare?tab=models: one
    polygon per model, axes = the tags any selected model touched.
    Per-(model, tag) attempt counts are emitted alongside the metrics
    so the SPA can grey out spokes where a model has insufficient
    coverage (<3 problems with the tag, by default)."""
    model_name: str
    tag: str
    n_attempted: int
    n_accepted: int
    pass_rate: float                          # n_accepted / n_attempted (0 if 0)
    avg_runtime_ms: float | None              # mean over each problem's best-accepted
    avg_memory_mb: float | None
    # Runtime energy — third perf axis (arena-local etc.).
    avg_runtime_energy_j: float | None = None


class CompareResponse(BaseModel):
    models: list[str]
    language: str | None
    problem_slugs: list[str] | None
    summary: list[CompareSummaryRow]
    problems: list[CompareProblemRow]
    winners: CompareWinners
    # Per-(model, level) breakdown — 3 levels × N models cells, used by the
    # SPA's "Performance by difficulty" grouped bar charts.
    by_level: list[CompareByLevelRow] = []
    # Per-(model, tag) breakdown — used by the SPA's three radar charts
    # (pass rate / runtime / memory across problem categories). One row
    # per (model, tag) for every tag any selected model touched. Empty
    # when the selected models have no judged attempts.
    by_tag: list[CompareByTagRow] = []


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
    # Per-million-token pricing. When both are set, the SPA recomputes
    # attempt.cost_usd dynamically from these × stored prompt/response
    # tokens. Null on either ⇒ the snapshot cost stamped on each attempt
    # at submit time is used as the fallback display.
    input_price_per_1m_usd: float | None = None
    output_price_per_1m_usd: float | None = None


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
    input_price_per_1m_usd: float | None = None
    output_price_per_1m_usd: float | None = None


class RegisteredModelPricingPatch(BaseModel):
    """Inline-edit payload for the /models pricing cells. Either field
    may be None to clear the rate (which falls back to snapshot cost
    on every attempt for this model)."""
    input_price_per_1m_usd: float | None = None
    output_price_per_1m_usd: float | None = None


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
    # Which harnesses have runs recorded for this competition. Populated
    # from the actual Submission → Harness join so the SPA can offer
    # exactly the leaderboard tabs that have data (rather than the full
    # harness registry). Empty list = no submissions yet.
    #
    # NOTE: Competition itself has no `harness_slug` field anymore —
    # competition and harness are decoupled; every submission carries
    # its own harness explicitly.
    harnesses_with_data: list[str] = []
    # Admin-blessed allow-list — the harnesses considered valid for this
    # competition. POST /api/submissions rejects (400) any submission
    # whose harness_slug isn't in this list. Empty list means "no
    # submissions currently accepted"; edit on the SPA admin form to
    # add. Distinct from harnesses_with_data (which reflects what
    # actually landed) — this is the FORWARD-LOOKING allow-list.
    compatible_harness_slugs: list[str] = []


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
    # NOTE: no harness_slug / correctness_oracle_slug fields. Competition
    # is a problem-set + rules bundle only; every submission picks its
    # own harness at POST time. See SubmissionIn.harness_slug.
    #
    # `compatible_harness_slugs` is the ADMIN allow-list — which harnesses
    # the operator has blessed as valid for this competition. Empty
    # list = no submissions accepted yet (POST /api/submissions returns
    # 400 with a "not compatible" note). Unknown slugs in the list are
    # rejected up-front so a typo surfaces at the admin form rather
    # than at first-submit time.
    compatible_harness_slugs: list[str] = Field(default_factory=list)


class CompetitionProblemsIn(BaseModel):
    """Bulk membership update: replace this competition's problems with the
    given slug list. Empty list = clear membership."""
    problem_slugs: list[str]


class HarnessRef(BaseModel):
    """Light-weight pointer to a harness — slug + name only. Used on ranking
    rows and submission cards so the UI can render a pill + link without
    dragging the full harness object along."""
    slug: str
    name: str


class HarnessPublic(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None
    kind: str
    status: str
    created_at: datetime
    # Optional operational config the operator pinned on the harness — e.g.
    # endpoint URL, docker image tag, resource limits. Free-form JSON so
    # each harness kind can carry its own knobs without a schema migration.
    config: dict | None = None
    # Activity stats — cheap to compute alongside the list query so the
    # /harnesses index card can show "N competitions / N submissions / N runs"
    # without a second round-trip.
    n_competitions: int = 0
    n_submissions: int = 0
    n_runs: int = 0


class HarnessDetail(HarnessPublic):
    """Slug-keyed detail view: identity + the competitions bound to this
    harness + the per-harness leaderboard's top entries. The /harnesses/:slug
    page renders this directly."""
    competitions: list[CompetitionRef] = []
    languages: list[str] = []
    top_models: list[dict] = []                 # same shape as CompetitionDetail.top_models


class HarnessIn(BaseModel):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    kind: str = "custom"                        # leetcode | arena-local | custom | …
    status: str = "active"                      # active | retired
    config: dict | None = None


class CorrectnessOracleRef(BaseModel):
    """Slug + name pointer for the correctness half of the two-oracle model.
    Same role as HarnessRef for the performance oracle. Rendered inline on
    ranking rows / submission cards so a client can link to
    /correctness-oracles/{slug} without fetching the full record."""
    slug: str
    name: str


class CorrectnessOraclePublic(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None
    kind: str
    status: str
    created_at: datetime
    config: dict | None = None
    # Activity rollup — same shape as HarnessPublic so the SPA can render
    # a matching card on /correctness-oracles.
    n_competitions: int = 0
    n_submissions: int = 0
    n_runs: int = 0


class CorrectnessOracleDetail(CorrectnessOraclePublic):
    """Slug-keyed detail view: identity + competitions bound to this
    correctness oracle + top-5 models judged by it. Companion to
    HarnessDetail on the performance side."""
    competitions: list[CompetitionRef] = []
    languages: list[str] = []
    top_models: list[dict] = []


class CorrectnessOracleIn(BaseModel):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    kind: str = "custom"                        # leetcode | unittest | humaneval | custom
    status: str = "active"
    config: dict | None = None


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
    error_log: str | None = None         # verbatim handler/validator error (arena-local)


class AttemptRunDetail(BaseModel):
    run_index: int
    status_msg: str
    runtime_ms: float | None
    memory_mb: float | None
    runtime_percentile: float | None
    memory_percentile: float | None
    # Execution energy + per function call metrics — populated only by execution-
    # instrumented harnesses (arena-local); None on LeetCode-scored runs. Let
    # the per-run charts/tables plot energy and per function call time in native units.
    runtime_energy_j: float | None = None
    energy_j_per_op: float | None = None
    time_us_per_op: float | None = None
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
    # Comma-separated LeetCode problem tags (Array, Dynamic Programming,
    # Math, …). Empty string when the problem row has no tags. Used by
    # the SubmissionDetail page's per-tag radar charts to bucket each
    # attempt without needing a second API call per problem. Empty
    # default keeps older API consumers happy.
    tags: str = ""
    status: str            # "Accepted" | a rejection reason | "queued"
    accepted: bool
    # Refinement-chain link — same defaults as the DB.
    round_index: int = 0
    previous_attempt_id: int | None = None
    # Cost + tokens of the LLM call that produced this attempt.
    # cost_usd is the SNAPSHOT value the client computed at submit time.
    # The SPA prefers to recompute cost dynamically from the RegisteredModel's
    # current per-1M-token rates × prompt_tokens / response_tokens — see
    # `cost_usd_live` below for the recomputed value.
    cost_usd: float | None = None
    response_tokens: int | None = None
    prompt_tokens: int | None = None
    # Recomputed cost from the model's current per-1M-token rates. Null when
    # the registered model has no rates set, or when the attempt is missing
    # prompt_tokens. The SPA prefers this for display; cost_usd is the
    # fallback for legacy attempts whose tokens weren't captured.
    cost_usd_live: float | None = None
    # Generation wall-clock of this attempt's solution (ms).
    wall_clock_ms: int | None = None
    # Generation energy of this attempt's solution (Joules). None when the
    # client didn't measure it (LeetCode never reports it).
    generation_energy_j: float | None = None
    # Client-detected malformed-response reason — set ⇒ this attempt was
    # marked failed without LeetCode dispatch (the trace still holds the
    # raw model response).
    prefailed_reason: str | None = None
    # System-level dispatch failure (auth dead, retries exhausted, …). When
    # set, n_runs is 0 and the attempt is pending a future dispatch retry.
    # Sweep clients should NOT treat this as a verdict — show the message
    # to the user and either retry or give up the run, but don't roll it
    # into pass@k.
    dispatch_warning: str | None = None
    n_runs: int
    n_accepted_runs: int
    avg_runtime_ms: float | None
    best_runtime_ms: float | None
    avg_memory_mb: float | None
    best_memory_mb: float | None
    avg_runtime_percentile: float | None
    avg_memory_percentile: float | None
    # Runtime energy of this attempt's solution (Joules). Populated by
    # execution-instrumented harnesses (arena-local etc.). Null on LC-only
    # attempts.
    avg_runtime_energy_j: float | None = None
    best_runtime_energy_j: float | None = None
    submitted_at: datetime | None
    runs: list[AttemptRunDetail]
    # Same shape submission_detail returns on its traces[] — so the
    # AttemptResults page can render the same "Provenance" and "Log entry"
    # expanders SubmissionDetail does.
    inference_trace: str | None = None
    trace: dict | None = None
    provenance: dict | None = None


# ---------- Cost estimator ---------------------------------------------------
#
# Powers the "how much would it cost me to run THIS model across the platform"
# calculator. Instead of multiplying noisy per-attempt averages, we report the
# observed RANGE (min / max) of total input+output tokens across already-
# completed full sweeps. Multiplying that range by the user's $-per-1M rates
# gives a defensible cost envelope rather than a single point estimate that
# pretends to be precise.

class CostEstimatorSubmission(BaseModel):
    """One full-sweep submission's token footprint, used as a data point in
    the min/max envelope. We compute input + output totals at query time by
    summing per-attempt usage out of each attempt's trace.provider_meta
    (no schema migration needed; the data is already there for every
    OpenAI and Ollama attempt the platform has ingested)."""
    submission_id: int
    model_name: str
    model_version: str
    language: str
    prompt_id: int | None
    prompt_name: str | None
    competition_slug: str | None
    competition_name: str | None
    # Harness that scored the runs backing this submission. Follows the
    # same resolution rule as everywhere else: submission override →
    # competition default → null. Lets the cost-estimator distinguish
    # "sweeps on our in-house harness" from "sweeps against LeetCode".
    harness_slug: str | None = None
    harness_name: str | None = None
    n_attempts: int                        # how many attempts in the submission
    n_problems_attempted: int              # unique problems hit
    n_problems_in_scope: int               # the comparison floor (competition size, or 99)
    is_full_sweep: bool                    # n_problems_attempted >= n_problems_in_scope
    total_input_tokens: int | None         # sum of provider_meta input tokens
    total_output_tokens: int | None        # sum of provider_meta output tokens
    n_attempts_with_token_data: int        # how many of the n_attempts had usage stats
    # Generation energy (Joules) summed across the sweep's attempts. Only
    # populated when at least one attempt in the submission has a power
    # probe reading; null otherwise (all-LeetCode data paths never report
    # energy). Included so the calculator can build a "energy per sweep"
    # range alongside the token / dollar envelopes.
    total_generation_energy_j: float | None = None
    n_attempts_with_energy_data: int = 0


class CostEstimatorRange(BaseModel):
    """Min / max / median over the full sweeps in scope. None when no full
    sweep with token data exists for the requested filter combo."""
    min: int | None = None
    max: int | None = None
    median: int | None = None
    n: int = 0                             # sample size feeding this range


class CostEstimatorStats(BaseModel):
    """The full payload the calculator page consumes."""
    # Echo of the filters that were applied, so the frontend can display
    # "filtered to language=python3, prompt=experiment-system" without
    # re-deriving from the URL.
    applied: dict
    # Universes for the dropdowns. Each filter dropdown shows the values
    # that actually appear in the platform's data (not a hard-coded list).
    available_prompts: list[dict]          # {id, name}
    available_languages: list[str]
    available_models: list[str]
    available_competitions: list[dict]     # {slug, name}
    # Harness slugs actually referenced by full sweeps in scope — same shape
    # as available_competitions so the calculator can hang a dropdown off it.
    available_harnesses: list[dict] = []   # {slug, name}
    # All full-sweep submissions matching the filters — drives the bottom
    # table of the calculator page so the user can see which runs the
    # envelope came from.
    submissions: list[CostEstimatorSubmission]
    # Min / max / median over the matching full sweeps. These are the
    # numbers the user multiplies by their $-per-1M rates.
    input_tokens: CostEstimatorRange
    output_tokens: CostEstimatorRange
    # Generation energy (Joules) across full sweeps in scope. Zero-sized
    # when no submission in the filter had a power-probe reading — the
    # frontend can hide the panel entirely in that case.
    generation_energy_j: CostEstimatorRange = CostEstimatorRange()


# ---------- LeetCode credential pool (admin) --------------------------------
#
# Wraps app.leetcode_accounts (which manages data/leetcode_accounts.json) so
# admins can manage the pool from the SPA instead of having to docker-exec
# in and run the CLI. Probe results are populated only on the dedicated
# /probe endpoint (one LC GraphQL call per account is slow enough that we
# don't want to inline it into a plain LIST).

class LeetCodeAccountStatus(BaseModel):
    name: str
    is_exhausted: bool
    exhausted_until: str | None = None
    # When the dispatcher last submitted with this account. ISO 8601 UTC,
    # null when the account has never been used. Lets the matrix_sweep
    # client (or any external pacing tool) compute the per-account 120 s
    # cooldown without guessing — pair with `min_gap_s` on the parent
    # response to know when `last_used_at + min_gap_s` is reached.
    last_used_at: str | None = None
    # Populated by the /probe endpoint; null on the plain LIST.
    is_signed_in: bool | None = None
    username: str | None = None
    probe_message: str | None = None


class LeetCodeAccountIn(BaseModel):
    """Accepts either a full cookie header OR the two cookies split out.
    Pre-SPA-era callers used `cookie_header`; the new SPA form posts the
    session_token + csrf_token pair directly because that's easier to grab
    from DevTools (Application → Cookies → leetcode.com, two clicks each)
    than copying the entire concatenated Cookie request header."""
    name: str
    # New shape — preferred. Either field alone is rejected.
    session_token: str | None = None
    csrf_token: str | None = None
    # Legacy shape — kept for back-compat with the CLI and existing scripts.
    cookie_header: str | None = None


class LeetCodeAccountsResponse(BaseModel):
    total: int
    available: int
    exhausted: int
    earliest_available_after: str | None = None
    # Backend's configured per-account inter-submission gap (seconds) —
    # what `pick_for_submission` enforces. Exposed so the matrix_sweep
    # client can compute the pacing it should observe locally without
    # hardcoding 120 s in two places. Default 0 keeps older clients
    # safe (they just don't pace by it).
    min_gap_s: float = 0.0
    accounts: list[LeetCodeAccountStatus]
