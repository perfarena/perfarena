const KEY_STORAGE = "arena.api_key";

export function getApiKey(): string | null {
  return localStorage.getItem(KEY_STORAGE);
}

const AUTH_EVENT = "arena.auth-changed";

export function setApiKey(key: string | null) {
  if (key) localStorage.setItem(KEY_STORAGE, key);
  else localStorage.removeItem(KEY_STORAGE);
  // Notify same-tab listeners (Layout's nav re-fetches on this) without
  // requiring a page reload. Cross-tab notifications come for free via the
  // browser's built-in `storage` event on localStorage.
  window.dispatchEvent(new CustomEvent(AUTH_EVENT));
}

export function subscribeAuth(cb: () => void): () => void {
  const handler = () => cb();
  window.addEventListener(AUTH_EVENT, handler);
  window.addEventListener("storage", (e) => {
    if (e.key === KEY_STORAGE) handler();
  });
  return () => {
    window.removeEventListener(AUTH_EVENT, handler);
    // (storage listener removal would require the same fn reference; for our
    // single global subscriber the leak is bounded — Layout mounts once.)
  };
}

export interface DatasetParams {
  language?: string;
  level?: string;
  model?: string;
  only_accepted?: boolean;
}

function datasetDownloadUrl(params: DatasetParams = {}): string {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  });
  return `/api/datasets/solutions${qs.toString() ? "?" + qs.toString() : ""}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const key = getApiKey();
  if (key) headers.set("Authorization", `Bearer ${key}`);
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    // A 401 on any request means the stored key is no longer valid (revoked,
    // DB was reseeded, etc.). Drop it so the UI stops sending it and the user
    // reverts to the signed-out state instead of seeing a confusing error.
    if (res.status === 401 && key) {
      setApiKey(null);
    }
    let detail: string;
    try { detail = JSON.stringify(await res.json()); }
    catch { detail = res.statusText; }
    throw new Error(`${res.status} ${detail}`);
  }
  if (res.status === 204) return null as unknown as T;
  return res.json() as Promise<T>;
}

export const api = {
  problems: (params: { level?: string; tag?: string; competition?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => !!v) as [string, string][],
    ).toString();
    return request<ProblemPublic[]>(`/api/problems${qs ? "?" + qs : ""}`);
  },
  // LeetCode credential pool (admin) -----------------------------------
  leetcodeAccounts: () =>
    request<LeetCodeAccountsResponse>(`/api/admin/leetcode-accounts`),
  leetcodeAccountsProbe: () =>
    request<LeetCodeAccountsResponse>(
      `/api/admin/leetcode-accounts/probe`, { method: "POST" }),
  /** Add/refresh a LeetCode account. Accepts either the split shape
   *  (session_token + csrf_token — preferred, what the SPA form sends)
   *  or the legacy cookie_header full string. */
  addLeetcodeAccount: (body: {
    name: string;
    session_token?: string;
    csrf_token?: string;
    cookie_header?: string;
  }) =>
    request<LeetCodeAccountsResponse>(
      `/api/admin/leetcode-accounts`, {
        method: "POST", body: JSON.stringify(body),
      }),
  removeLeetcodeAccount: (name: string) =>
    request<null>(
      `/api/admin/leetcode-accounts/${encodeURIComponent(name)}`,
      { method: "DELETE" }),
  clearLeetcodeAccountExhaustion: (name: string) =>
    request<LeetCodeAccountsResponse>(
      `/api/admin/leetcode-accounts/${encodeURIComponent(name)}/clear-exhaustion`,
      { method: "POST" }),

  costEstimatorStats: (params: {
    prompt_id?: number; language?: string;
    model_name?: string; competition?: string;
  } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]) as [string, string][],
    ).toString();
    return request<CostEstimatorStats>(
      `/api/cost-estimator-stats${qs ? "?" + qs : ""}`);
  },
  problemDetail: (slug: string) => request<ProblemDetail>(`/api/problems/${slug}`),
  rankings: (params: {
    language?: string; level?: string; sort?: string;
    k?: number; prompt_id?: number; competition?: string;
    harness?: string; correctness_oracle?: string;
  } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => !!v) as [string, string][],
    ).toString();
    return request<RankingEntry[]>(`/api/rankings${qs ? "?" + qs : ""}`);
  },
  submissions: () => request<SubmissionPublic[]>(`/api/submissions`),
  submissionDetail: (id: number | string) => request<SubmissionDetail>(`/api/submissions/${id}`),
  deleteSubmission: (id: number | string) =>
    request<null>(`/api/submissions/${id}`, { method: "DELETE" }),
  me: () => request<MePayload>(`/api/me`),
  mySubmissions: () => request<SubmissionPublic[]>(`/api/me/submissions`),
  modelDetail: (name: string) => request<ModelDetail>(`/api/models/${encodeURIComponent(name)}`),
  models: () => request<ModelListEntry[]>(`/api/models`),

  // Curated catalog ---------------------------------------------------------
  registeredModels: (includeRetired = false) =>
    request<RegisteredModelOverview[]>(
      `/api/registered-models${includeRetired ? "?include_retired=true" : ""}`),

  // Competitions ------------------------------------------------------------
  competitions: (includeRetired = false) =>
    request<CompetitionPublic[]>(
      `/api/competitions${includeRetired ? "?include_retired=true" : ""}`),
  competition: (slug: string, harnessSlug?: string | null) => {
    const qs = harnessSlug ? `?harness_slug=${encodeURIComponent(harnessSlug)}` : "";
    return request<CompetitionDetail>(
      `/api/competitions/${encodeURIComponent(slug)}${qs}`);
  },
  addCompetition: (body: CompetitionIn) =>
    request<CompetitionPublic>(`/api/admin/competitions`, {
      method: "POST", body: JSON.stringify(body),
    }),
  editCompetition: (id: number, body: CompetitionIn) =>
    request<CompetitionPublic>(`/api/admin/competitions/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  deleteCompetition: (id: number) =>
    request<null>(`/api/admin/competitions/${id}`, { method: "DELETE" }),
  setCompetitionProblems: (id: number, problem_slugs: string[]) =>
    request<CompetitionPublic>(`/api/admin/competitions/${id}/problems`, {
      method: "PUT", body: JSON.stringify({ problem_slugs }),
    }),

  // Harnesses ---------------------------------------------------------------
  // The benchmark harness / perf oracle registry. Every submission is scored
  // by exactly one harness (submission override → competition default), and
  // the UI uses these endpoints to render the /harnesses index + detail
  // pages plus the harness filter on the leaderboard.
  harnesses: (includeRetired = false) =>
    request<HarnessPublic[]>(
      `/api/harnesses${includeRetired ? "?include_retired=true" : ""}`),
  harness: (slug: string) =>
    request<HarnessDetail>(`/api/harnesses/${encodeURIComponent(slug)}`),
  addHarness: (body: HarnessIn) =>
    request<HarnessPublic>(`/api/admin/harnesses`, {
      method: "POST", body: JSON.stringify(body),
    }),
  editHarness: (id: number, body: HarnessIn) =>
    request<HarnessPublic>(`/api/admin/harnesses/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  deleteHarness: (id: number) =>
    request<null>(`/api/admin/harnesses/${id}`, { method: "DELETE" }),
  /** Probe the harness's remote runner /healthz. Only meaningful for
   *  arena-local / custom harnesses with a config.endpoint set. */
  probeHarness: (id: number) =>
    request<{ ok: boolean; message: string; not_applicable?: boolean }>(
      `/api/admin/harnesses/${id}/probe`, { method: "POST" }),

  // Correctness oracles ----------------------------------------------------
  // The correctness half of the two-oracle model (see Harnesses above for
  // the performance half). Every submission resolves to exactly one
  // correctness oracle (submission override → competition default); this
  // is what says "Accepted / Wrong Answer / Runtime Error" independently
  // of who measured runtime + memory.
  correctnessOracles: (includeRetired = false) =>
    request<CorrectnessOraclePublic[]>(
      `/api/correctness-oracles${includeRetired ? "?include_retired=true" : ""}`),
  correctnessOracle: (slug: string) =>
    request<CorrectnessOracleDetail>(
      `/api/correctness-oracles/${encodeURIComponent(slug)}`),
  addCorrectnessOracle: (body: CorrectnessOracleIn) =>
    request<CorrectnessOraclePublic>(`/api/admin/correctness-oracles`, {
      method: "POST", body: JSON.stringify(body),
    }),
  editCorrectnessOracle: (id: number, body: CorrectnessOracleIn) =>
    request<CorrectnessOraclePublic>(`/api/admin/correctness-oracles/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  deleteCorrectnessOracle: (id: number) =>
    request<null>(`/api/admin/correctness-oracles/${id}`, { method: "DELETE" }),

  // Blog --------------------------------------------------------------------
  blogPosts: () => request<BlogPostCard[]>(`/api/blog`),
  blogPost: (slug: string) =>
    request<BlogPostPublic>(`/api/blog/${encodeURIComponent(slug)}`),
  adminBlogPosts: () => request<BlogPostPublic[]>(`/api/admin/blog`),
  addBlogPost: (body: BlogPostIn) =>
    request<BlogPostPublic>(`/api/admin/blog`, {
      method: "POST", body: JSON.stringify(body),
    }),
  editBlogPost: (id: number, body: BlogPostIn) =>
    request<BlogPostPublic>(`/api/admin/blog/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  deleteBlogPost: (id: number) =>
    request<null>(`/api/admin/blog/${id}`, { method: "DELETE" }),
  addRegisteredModel: (body: RegisteredModelIn) =>
    request<RegisteredModelPublic>(`/api/admin/registered-models`, {
      method: "POST", body: JSON.stringify(body),
    }),
  editRegisteredModel: (id: number, body: RegisteredModelIn) =>
    request<RegisteredModelPublic>(`/api/admin/registered-models/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  deleteRegisteredModel: (id: number) =>
    request<null>(`/api/admin/registered-models/${id}`, { method: "DELETE" }),
  updateRegisteredModelPricing: (id: number, body: RegisteredModelPricingPatch) =>
    request<RegisteredModelPublic>(
      `/api/admin/registered-models/${id}/pricing`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  seedRegisteredModelPricing: (overwrite = false) =>
    request<RegisteredModelSeedPricingResponse>(
      `/api/admin/registered-models/seed-pricing${overwrite ? "?overwrite=true" : ""}`,
      { method: "POST" },
    ),

  prompts: (includeRetired = false) =>
    request<PromptTemplatePublic[]>(
      `/api/prompts${includeRetired ? "?include_retired=true" : ""}`),
  addPrompt: (body: PromptTemplateIn) =>
    request<PromptTemplatePublic>(`/api/admin/prompts`, {
      method: "POST", body: JSON.stringify(body),
    }),
  editPrompt: (id: number, body: PromptTemplateIn) =>
    request<PromptTemplatePublic>(`/api/admin/prompts/${id}`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  deletePrompt: (id: number) =>
    request<null>(`/api/admin/prompts/${id}`, { method: "DELETE" }),
  compare: (params: {
    models: string[]; language?: string; problems?: string[];
    promptId?: number;
  }) => {
    const qs = new URLSearchParams();
    params.models.forEach(m => qs.append("models", m));
    if (params.language) qs.set("language", params.language);
    if (params.problems && params.problems.length) qs.set("problems", params.problems.join(","));
    if (params.promptId != null) qs.set("prompt_id", String(params.promptId));
    return request<CompareResponse>(`/api/compare?${qs.toString()}`);
  },
  /** Same shape as compare(), but the comparison axis is specific
   *  submission IDs rather than model names. Labels for each row are
   *  synthesized server-side as `#<id> <model> · <language>`. */
  compareSubmissions: (params: {
    ids: number[]; problems?: string[];
  }) => {
    const qs = new URLSearchParams();
    params.ids.forEach(n => qs.append("ids", String(n)));
    if (params.problems && params.problems.length) qs.set("problems", params.problems.join(","));
    return request<CompareResponse>(`/api/compare/submissions?${qs.toString()}`);
  },
  comparePrompts: (params: {
    modelName: string; modelVersion?: string; language?: string;
    promptIds: number[]; problems?: string[];
  }) => {
    const qs = new URLSearchParams();
    qs.set("model_name", params.modelName);
    if (params.modelVersion) qs.set("model_version", params.modelVersion);
    if (params.language) qs.set("language", params.language);
    params.promptIds.forEach(id => qs.append("prompt_ids", String(id)));
    if (params.problems && params.problems.length) qs.set("problems", params.problems.join(","));
    return request<ComparePromptsResponse>(`/api/compare/prompts?${qs.toString()}`);
  },
  analysis: () => request<AnalysisResponse>("/api/analysis"),
  discrimination: () => request<DiscriminationResponse>("/api/discrimination"),
  submitterDetail: (slug: string) => request<SubmitterDetail>(`/api/submitters/${encodeURIComponent(slug)}`),
  datasetDownloadUrl: datasetDownloadUrl,
  downloadDataset: async (params: DatasetParams = {}) => {
    const key = getApiKey();
    if (!key) throw new Error("not authenticated");
    const res = await fetch(datasetDownloadUrl(params), { headers: { Authorization: `Bearer ${key}` } });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "arena_solutions.jsonl";
    a.click();
    URL.revokeObjectURL(url);
  },
  captcha: () => request<{ token: string; prompt: string }>(`/api/captcha`),
  snapshots: () => request<SnapshotSummary[]>(`/api/snapshots`),
  snapshotDetail: (id: number | string) => request<SnapshotDetail>(`/api/snapshots/${id}`),
  register: (body: RegisterPayload) =>
    request<{ status: string; message: string }>(`/api/register`, {
      method: "POST", body: JSON.stringify(body),
    }),
  login: (body: LoginPayload) =>
    request<LoginResponse>(`/api/login`, {
      method: "POST", body: JSON.stringify(body),
    }),
  regenerateApiKey: () =>
    request<{ api_key: string }>(`/api/me/api-key/regenerate`, { method: "POST" }),
  discardApiKey: () =>
    request<null>(`/api/me/api-key`, { method: "DELETE" }),
  submitBatch: (body: BatchPayload) =>
    request<{ submission_id: number; status: string; attempts_received: number }>(`/api/submissions`, {
      method: "POST", body: JSON.stringify(body),
    }),
  appendAttempts: (id: number, attempts: AttemptIn[]) =>
    request<{ submission_id: number; appended: number; status: string }>(
      `/api/submissions/${id}/attempts`, {
        method: "POST", body: JSON.stringify({ attempts }),
      },
    ),
  submissionProgress: (id: number) =>
    request<SubmissionProgressPayload>(`/api/submissions/${id}/progress`),
  submissionAttempts: (id: number | string) =>
    request<AttemptResultPayload[]>(`/api/submissions/${id}/attempts`),
  myProgress: () => request<ProgressEntryPayload[]>(`/api/me/progress`),
  myByLanguage: () => request<MyByLanguagePayload[]>(`/api/me/by-language`),
  languages: () => request<LanguageListItem[]>(`/api/languages`),
  languageDetail: (name: string) => request<LanguageDetailPayload>(`/api/languages/${encodeURIComponent(name)}`),
  adminPending: () => request<PendingUser[]>(`/api/admin/pending`),
  adminExportTables: () => request<{ tables: string[] }>(`/api/admin/export/tables`),
  /** Builds the download URL with auth fold-in so the admin can just click.
   * `includeCredentials` only has effect when `leetcode_accounts` is in the
   * tables list — when true, the bundle ships raw session cookies; when
   * false (default), cookies are redacted to `<head>…<redacted>`. */
  adminExportUrl: (tables: string[], format: "json" | "csv",
                   includeCredentials: boolean = false) => {
    const qs = new URLSearchParams();
    if (tables.length) qs.set("tables", tables.join(","));
    qs.set("format", format);
    if (includeCredentials) qs.set("include_credentials", "true");
    // Many browsers can't attach an Authorization header to a plain <a download>
    // click; the file is fetched via fetch() in the page and then turned into a
    // blob URL there. This helper just returns the API path for that fetch.
    return `/api/admin/export?${qs.toString()}`;
  },
  adminImport: (bundle: unknown) =>
    request<{ counts: Record<string, { inserted: number; skipped: number; errored: number }>; bundle_version: number }>(
      `/api/admin/import`, { method: "POST", body: JSON.stringify(bundle) },
    ),
  adminApprove: (userId: number) =>
    request<{ api_key: string }>(`/api/admin/users/${userId}/approve`, { method: "POST" }),
  adminReject: (userId: number) =>
    request<{ status: string }>(`/api/admin/users/${userId}/reject`, { method: "POST" }),
  refreshBaselines: () =>
    request<{
      real: number; synthetic_placeholders: number;
      already_real: number; languages_touched: string[];
    }>(`/api/admin/baselines/refresh`, { method: "POST" }),
  provenanceLog: (params: { limit?: number; offset?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    return request<ProvenanceLog>(`/api/provenance/log${qs.toString() ? "?" + qs : ""}`);
  },
};

// types ---------------------------------------------------------------------

/** Light pointer used wherever we surface "this thing belongs to a
 *  competition" without needing the full CompetitionPublic shape. */
export interface CompetitionRef {
  slug: string;
  name: string;
}

export interface ProblemPublic {
  id: number;
  title_slug: string;
  title: string;
  level: "Easy" | "Medium" | "Hard";
  tags: string[];
  /** Competitions this problem is a member of. Usually one entry; empty
   *  for orphan problems with no competition link (legacy data). */
  competitions: CompetitionRef[];
}

export interface ProblemTopSolution {
  submission_id: number;
  model_name: string;
  model_version: string;
  language: string;
  submitter: string;
  submitter_slug: string;
  status_msg: string;
  runtime_ms: number | null;
  memory_mb: number | null;
  runtime_percentile: number | null;
  memory_percentile: number | null;
  /* Mean across the 5 dispatch repeats (for noise-band context next to the
   * `runtime_ms` headline which is the best of the 5). null on legacy rows
   * where the per-run detail wasn't aggregated up. */
  avg_runtime_ms?: number | null;
  avg_memory_mb?: number | null;
  avg_runtime_percentile?: number | null;
  avg_memory_percentile?: number | null;
  /* Runtime energy (Joules) of the solution — best of N runs is the
   * headline (least energy consumed), plus the noise-band avg. Populated
   * only by execution-instrumented harnesses; null on LeetCode-scored
   * attempts. */
  runtime_energy_j?: number | null;
  avg_runtime_energy_j?: number | null;
  runtime_energy_percentile?: number | null;
  avg_runtime_energy_percentile?: number | null;
  n_runs?: number | null;
  n_accepted_runs?: number | null;
}

export interface ProblemDetail extends ProblemPublic {
  content: string;
  hints: string | null;
  /** LeetCode-provided starter-code stubs per language (langSlug → code). The
   * backend populates this via `app.fetch_code_snippets`; the keys are
   * LeetCode's lang slugs (e.g. "python3", "cpp", "java"). Empty dict when
   * the fetcher hasn't run yet for this problem. */
  code_snippets: Record<string, string>;
  testcases: number | null;
  n_attempts: number;
  n_accepted: number;
  top_solutions: ProblemTopSolution[];
  best_per_language: ProblemTopSolution[];
  n_attempts_by_language: Record<string, number>;
  n_accepted_by_language: Record<string, number>;
  /** Anti-contamination metadata — populated when this Problem is a
   *  rephrased variant of another. Null on canonical problems. The
   *  model being evaluated never sees these fields; they exist purely
   *  so researchers can verify the mapping at a glance in the SPA. */
  original_slug: string | null;
  original_title: string | null;
  original_function_name: string | null;
  function_name: string | null;
}

export interface RankingEntry {
  rank: number;
  model_name: string;
  model_version: string;
  language: string;
  pass_at_1: number;
  avg_runtime_ms: number | null;
  avg_memory_mb: number | null;
  best_runtime_ms: number | null;
  best_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  /** Solution-runtime energy (Joules) — the third perf axis, populated only
   *  by execution-instrumented harnesses (LeetCode never reports it). SPA
   *  hides the column when no row in scope has a value. */
  avg_runtime_energy_j: number | null;
  best_runtime_energy_j: number | null;
  avg_runtime_energy_percentile: number | null;
  best_runtime_energy_percentile: number | null;
  /** Measurement provenance: the machine + energy tool this entry was measured
   *  on. Null for LeetCode-scored rows. Lets the leaderboard show which machine
   *  each entry was measured on (energy is only comparable within one). */
  machine_name: string | null;
  energy_tool: string | null;
  time_human_avg: number | null;
  time_human_expert: number | null;
  mem_human_avg: number | null;
  mem_human_expert: number | null;
  total_solved: number;
  total_problems: number;
  n_attempts: number;
  n_samples_per_problem: number;
  pass_at_k: number | null;
  pass_at_full: number | null;
  submission_id: number;
  submitter: string;
  submitter_slug: string;
  prompt_id: number | null;
  prompt_name: string | null;
  /** Competition this submission targeted. null on legacy rows. */
  competition_slug: string | null;
  competition_name: string | null;
  /** PERFORMANCE oracle (Harness) whose numbers this row is scored on.
   *  Resolved per submission (own override → competition default → null).
   *  Every legacy row resolves to `leetcode` via the startup backfill. */
  harness_slug: string | null;
  harness_name: string | null;
  /** CORRECTNESS oracle whose Accepted / Wrong Answer / RE / TLE verdict
   *  drives pass@1 for this row. Decoupled from the perf oracle so a
   *  client can filter by "who said this was correct" independently of
   *  "who measured how fast it was". */
  correctness_oracle_slug: string | null;
  correctness_oracle_name: string | null;
  /** Token / $ accounting. Total = sum across all attempts in this submission;
   *  median = per-attempt median so submissions covering different attempt
   *  counts compare on the same axis. Null when no attempt reported the
   *  field (e.g. all-Ollama runs with no cost telemetry, legacy ingests). */
  total_response_tokens: number | null;
  median_response_tokens_per_attempt: number | null;
  total_cost_usd: number | null;
  median_cost_usd_per_attempt: number | null;
  /** Generation wall-clock — sum across attempts (ms) + per-attempt
   *  median. The median is the apples-to-apples "typical seconds per
   *  solution" regardless of how many problems the submission covers. */
  total_generation_ms: number | null;
  median_generation_ms_per_attempt: number | null;
}

export interface SubmissionPublic {
  id: number;
  user_display_name: string;
  user_org: string | null;
  submitter_slug: string;
  model_name: string;
  model_version: string;
  language: string;
  status: string;
  created_at: string;
  pass_at_1: number | null;
  avg_runtime_ms: number | null;
  avg_memory_mb: number | null;
  best_runtime_ms: number | null;
  best_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  /** Solution-runtime energy (Joules) aggregated across accepted attempts —
   *  populated only when the scoring harness measures execution energy. */
  avg_runtime_energy_j: number | null;
  best_runtime_energy_j: number | null;
  avg_runtime_energy_percentile: number | null;
  best_runtime_energy_percentile: number | null;
  /** Submission-level per function call rollup (looped curated methodology): energy /
   *  time normalized to a single solve (µs / J — the cross-language-comparable
   *  figures) plus the mean solve count behind each measurement (confidence).
   *  Null on LeetCode-scored submissions. */
  avg_time_us_per_op: number | null;
  avg_energy_j_per_op: number | null;
  avg_iterations: number | null;
  time_human_avg: number | null;
  time_human_expert: number | null;
  mem_human_avg: number | null;
  mem_human_expert: number | null;
  total_solved: number;
  total_problems: number;
  n_attempts: number;
  n_samples_per_problem: number;
  pass_at_k: number | null;
  pass_at_full: number | null;
  fully_accepted: boolean;
  leaderboard_eligible: boolean;
  leaderboard_reason: "eligible" | "incomplete" | "pass-rate-too-low" | "not-judged" | null;
  prompt_id: number | null;
  prompt_name: string | null;
  /* Experiment-cell knobs (see app/main.py::_DEFAULT_PROMPTS for templates that
   * consume them). Two submissions that differ on any of these are different
   * experimental cells — submit_with_local_model.py's --append-if-existing
   * lookup matches on all three. */
  n_rounds: number;
  include_hints: boolean;
  optimization_target: "latency" | "memory" | null;
  /* Cost + token totals across all attempts (k samples × N problems × rounds).
   * total_cost_usd is the LIVE total — current per-model rates × stored
   * tokens, falling back to each attempt's snapshot when its tokens are
   * missing. total_cost_usd_snapshot preserves the at-submit value so
   * "what we actually paid" stays available. null = not reported (e.g.
   * pre-feature submissions); 0 = genuinely free (all-Ollama). */
  total_cost_usd: number | null;
  total_cost_usd_snapshot: number | null;
  total_response_tokens: number | null;
  /** Per-attempt median spend, computed server-side. Lets the sortable
   *  tables rank submissions by "typical cost / tokens per problem"
   *  instead of just the total (which scales with attempt count). */
  median_response_tokens_per_attempt: number | null;
  median_cost_usd_per_attempt: number | null;
  median_generation_ms_per_attempt: number | null;
  /* Total generation wall-clock (ms) summed across all attempts. null =
   * not reported (pre-feature submissions). */
  total_generation_ms: number | null;
  /* Total generation energy (Joules) summed across all attempts. null when
   * no attempt reported one (LeetCode never reports it) — the UI omits the
   * column / stat in that case. */
  total_generation_energy_j: number | null;
  /** Competition this submission targeted. null on legacy rows. */
  competition_slug: string | null;
  competition_name: string | null;
  /** PERFORMANCE oracle (Harness) that scored this submission's runs.
   *  Resolved per submission (own override → competition default → null). */
  harness_slug: string | null;
  harness_name: string | null;
  /** CORRECTNESS oracle (Accepted / WA / RE / TLE verdict source) for this
   *  submission. Same resolution as harness. */
  correctness_oracle_slug: string | null;
  correctness_oracle_name: string | null;
  /** Measurement provenance: the single energy tool + machine this submission
   *  was measured on. Energy / time numbers are only comparable within one
   *  (energy_tool, machine) pair. Null on LeetCode-scored submissions
   *  ("unknown machine"). run_uid correlates with external measurement logs. */
  energy_tool: string | null;
  machine_id: number | null;
  machine_name: string | null;
  run_uid: string | null;
  /** Timestamp of the most recent attempt on this submission. null when
   *  the submission has no attempts yet. The /submissions table sorts
   *  by this so a still-running sweep stays at the top of the list. */
  last_attempt_at: string | null;
}

export interface ModelDetailLanguageStat {
  language: string;
  n_submissions: number;
  best_pass_at_1: number | null;
  best_avg_runtime_ms: number | null;
  best_avg_memory_mb: number | null;
  best_avg_runtime_percentile: number | null;
  best_avg_memory_percentile: number | null;
  fully_accepted_any: boolean;
  leaderboard_eligible_any: boolean;
}

export interface ModelDetail {
  name: string;
  versions: string[];
  languages: string[];
  n_submissions: number;
  best_pass_at_1: number | null;
  best_avg_runtime_percentile: number | null;
  best_avg_memory_percentile: number | null;
  submitters: string[];
  submissions: SubmissionPublic[];
  by_language: ModelDetailLanguageStat[];
}

export interface RegisteredModelPublic {
  id: number;
  name: string;
  version: string;
  provider: string;
  endpoint: string | null;
  params: Record<string, unknown> | null;
  notes: string | null;
  description: string | null;
  model_type: string | null;     // chat | instruct | reasoning | coder | …
  family: string | null;         // qwen | llama | gpt | claude | …
  provenance: string | null;     // model-card URL, citation, source links (markdown)
  status: string;                // active | retired | unverified
  added_at: string;
  /** USD per 1M tokens. When BOTH are set, the SPA renders attempt-level
   *  cost as live-recomputed (current rate × stored tokens) instead of
   *  the snapshot stored at submit time. Edit inline on /models. */
  input_price_per_1m_usd: number | null;
  output_price_per_1m_usd: number | null;
}
export interface RegisteredModelPricingPatch {
  input_price_per_1m_usd: number | null;
  output_price_per_1m_usd: number | null;
}
export interface RegisteredModelSeedPricingResponse {
  filled: number;
  skipped_has_rates: number;
  skipped_no_default: number;
  overwrite_mode: boolean;
  total_registered_models: number;
}
/** GET /api/registered-models — adds activity stats per row. */
export interface RegisteredModelOverview extends RegisteredModelPublic {
  n_submissions: number;
  n_judged: number;
  last_submitted_at: string | null;
  languages: string[];
  best_pass_at_1: number | null;
}
export interface RegisteredModelIn {
  name: string;
  version: string;
  provider: string;
  endpoint?: string | null;
  params?: Record<string, unknown> | null;
  notes?: string | null;
  description?: string | null;
  model_type?: string | null;
  family?: string | null;
  provenance?: string | null;
  status?: string;
  input_price_per_1m_usd?: number | null;
  output_price_per_1m_usd?: number | null;
}

export interface CompetitionPublic {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  kind: string;                    // leetcode | humaneval | mbpp | custom | …
  status: string;                  // active | retired
  created_at: string;
  n_problems: number;
  n_submissions: number;
  n_models: number;
  /** Slugs of every harness that HAS runs recorded for this competition,
   *  so the SPA can render a per-harness leaderboard tab per slug (and
   *  only show tabs that have data). Competition itself no longer binds
   *  to a harness — every submission carries its own explicitly. */
  harnesses_with_data: string[];
  /** Admin allow-list of harness slugs blessed for this competition.
   *  POST /api/submissions rejects (400) any submission whose harness
   *  isn't in this list. Distinct from harnesses_with_data (which is
   *  the retrospective list of what has actually landed) — this is
   *  the forward-looking policy the admin sets. */
  compatible_harness_slugs: string[];
}
export interface CompetitionDetail extends CompetitionPublic {
  problems: ProblemPublic[];
  languages: string[];
  /** Per-model leaderboard scoped to THIS competition's problem set.
   *  All metrics are recomputed across only the attempts on the
   *  competition's problems — a model's pass@1 here will differ from
   *  its pass@1 on a different competition that includes a different
   *  subset of problems. */
  top_models: {
    model_name: string;
    model_version: string;
    best_pass_at_1: number;
    pass_at_k: number | null;
    n_submissions: number;
    n_solved: number | null;
    n_attempted: number | null;
    n_problems: number | null;
    avg_runtime_ms: number | null;
    avg_memory_mb: number | null;
    avg_runtime_percentile: number | null;
    avg_memory_percentile: number | null;
  }[];
}
export interface CompetitionIn {
  slug: string;
  name: string;
  description?: string | null;
  kind?: string;
  status?: string;
  /** Admin allow-list of harness slugs. Empty [] = no submissions
   *  accepted until at least one harness is opted in. */
  compatible_harness_slugs?: string[];
}

/** Light-weight pointer to a harness — slug + name only. Rendered inline on
 *  ranking rows and submission cards so the SPA doesn't have to fetch the
 *  full harness object just to draw a pill. */
export interface HarnessRef {
  slug: string;
  name: string;
}

export interface HarnessPublic {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  kind: string;                    // leetcode | arena-local | custom | …
  status: string;                  // active | retired
  created_at: string;
  /** Operator-defined runtime knobs (endpoint URL, docker image, resource
   *  limits, …). Free-form JSON so each harness kind can carry its own
   *  fields without a schema migration. */
  config: Record<string, unknown> | null;
  /** Activity rollup so the /harnesses index cards don't need a second
   *  round-trip. n_submissions counts EFFECTIVE-harness rows (own override
   *  OR competition default). */
  n_competitions: number;
  n_submissions: number;
  n_runs: number;
}
export interface HarnessDetail extends HarnessPublic {
  /** Competitions bound to this harness (their default oracle). Rendered
   *  as pills on the detail page's "used by" section. */
  competitions: CompetitionRef[];
  languages: string[];
  /** Per-model leaderboard scoped to submissions on THIS harness. Uses
   *  each submission's own pass@1 directly since the harness applies
   *  uniformly across every problem the submission touched. */
  top_models: {
    model_name: string;
    model_version: string;
    best_pass_at_1: number;
    pass_at_k: number | null;
    n_submissions: number;
    avg_runtime_ms: number | null;
    avg_memory_mb: number | null;
    avg_runtime_percentile: number | null;
    avg_memory_percentile: number | null;
  }[];
}
export interface HarnessIn {
  slug: string;
  name: string;
  description?: string | null;
  kind?: string;
  status?: string;
  config?: Record<string, unknown> | null;
}

/** Correctness-oracle pointer (slug + name). Same role as HarnessRef on the
 *  perf side — inline chips + links on ranking rows / submission cards. */
export interface CorrectnessOracleRef {
  slug: string;
  name: string;
}

export interface CorrectnessOraclePublic {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  kind: string;                    // leetcode | unittest | humaneval | custom
  status: string;                  // active | retired
  created_at: string;
  config: Record<string, unknown> | null;
  n_competitions: number;
  n_submissions: number;
  n_runs: number;
}
export interface CorrectnessOracleDetail extends CorrectnessOraclePublic {
  /** Competitions bound to this correctness oracle (their default judge). */
  competitions: CompetitionRef[];
  languages: string[];
  /** Per-model leaderboard scoped to submissions judged by THIS oracle. */
  top_models: {
    model_name: string;
    model_version: string;
    best_pass_at_1: number;
    pass_at_k: number | null;
    n_submissions: number;
    avg_runtime_ms: number | null;
    avg_memory_mb: number | null;
    avg_runtime_percentile: number | null;
    avg_memory_percentile: number | null;
  }[];
}
export interface CorrectnessOracleIn {
  slug: string;
  name: string;
  description?: string | null;
  kind?: string;
  status?: string;
  config?: Record<string, unknown> | null;
}

export interface BlogPostCard {
  id: number;
  slug: string;
  title: string;
  summary: string | null;
  cover_image_url: string | null;
  author_name: string | null;
  status: string;                  // draft | published | retired
  published_at: string | null;
  created_at: string;
}
export interface BlogPostPublic extends BlogPostCard {
  body: string;                    // markdown
  updated_at: string;
}
export interface BlogPostIn {
  slug: string;
  title: string;
  summary?: string | null;
  body?: string;
  cover_image_url?: string | null;
  author_name?: string | null;
  status?: string;
}

export interface PromptTemplatePublic {
  id: number;
  name: string;
  system_prompt: string;
  user_prompt_template: string;
  language_hint: string | null;
  notes: string | null;
  status: string;
  created_at: string;
}
export interface PromptTemplateIn {
  name: string;
  system_prompt: string;
  user_prompt_template: string;
  language_hint?: string | null;
  notes?: string | null;
  status?: string;
}

export interface ModelListEntry {
  name: string;
  n_submissions: number;
  languages: string[];
}

export interface CompareModelOnProblem {
  accepted: boolean;
  submission_id: number | null;
  attempt_id: number | null;
  language: string | null;
  status: string | null;
  runtime_ms: number | null;
  memory_mb: number | null;
  runtime_percentile: number | null;
  memory_percentile: number | null;
  /** Generation energy of the best attempt (Joules) — LLM-side. null for
   *  the LC-only data path; surfaced so the comparison histogram can
   *  render an energy bar when models in scope report it. */
  generation_energy_j: number | null;
  /** RUNTIME energy of the best attempt (Joules) — execution-side, the
   *  third perf axis. Populated by execution-instrumented harnesses (e.g.
   *  arena-local). This is the field the head-to-head plots by default
   *  when present; generation_energy_j is the fallback. */
  runtime_energy_j: number | null;
}

export interface CompareProblemRow {
  problem_slug: string;
  problem_title: string;
  level: "Easy" | "Medium" | "Hard" | string;
  per_model: Record<string, CompareModelOnProblem>;
}

export interface CompareSummaryRow {
  model_name: string;
  n_submissions: number;
  n_problems_accepted: number;
  n_problems_attempted: number;
  languages: string[];
  pass_at_1_max: number | null;
  pass_at_k_max: number | null;
  pass_at_full_max: number | null;
  avg_runtime_ms: number | null;
  best_runtime_ms: number | null;
  avg_memory_mb: number | null;
  best_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  time_human_avg: number | null;
  time_human_expert: number | null;
  mem_human_avg: number | null;
  mem_human_expert: number | null;
  leading_submission_id: number | null;
}

export interface CompareWinners {
  best_pass_at_1: string | null;
  best_pass_at_k: string | null;
  best_pass_at_full: string | null;
  most_problems_solved: string | null;
  fastest_avg_runtime: string | null;
  leanest_avg_memory: string | null;
  best_runtime_percentile: string | null;
  best_memory_percentile: string | null;
  overall: string | null;
  overall_wins_by_model: Record<string, number>;
}

export interface CompareByLevelRow {
  model_name: string;
  level: "Easy" | "Medium" | "Hard";
  n_attempted: number;
  n_accepted: number;
  pass_rate: number;                            // 0..1
  // Judge-side perf (accepted attempts only)
  avg_runtime_ms: number | null;
  avg_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  /** Runtime energy (Joules) — execution-side third perf axis. Populated
   *  by execution-instrumented harnesses (arena-local etc.). Null on LC-
   *  only data; the SPA hides the chart when every cell is null. */
  avg_runtime_energy_j: number | null;
  // Generator-side cost & throughput per problem in scope
  avg_cost_usd: number | null;
  avg_response_tokens: number | null;
  avg_wall_clock_ms: number | null;
}

/** One (model × problem tag) cell — same metric vocab as CompareByLevelRow
 *  but the axis is the LeetCode tag (Dynamic Programming, Math, Array, …)
 *  rather than difficulty. Used by the SPA's radar charts. */
export interface CompareByTagRow {
  model_name: string;
  tag: string;
  n_attempted: number;
  n_accepted: number;
  pass_rate: number;                            // 0..1
  avg_runtime_ms: number | null;
  avg_memory_mb: number | null;
  avg_runtime_energy_j: number | null;
}

export interface CompareResponse {
  models: string[];
  language: string | null;
  problem_slugs: string[] | null;
  summary: CompareSummaryRow[];
  problems: CompareProblemRow[];
  winners: CompareWinners;
  /** Per-(model, level) breakdown for the Easy/Medium/Hard grouped bar
   *  charts in the SPA. One row per (model, level) — sorted Easy →
   *  Medium → Hard. Empty array if the comparison ran on zero models. */
  by_level: CompareByLevelRow[];
  /** Per-(model, tag) breakdown for the radar charts. One row per
   *  (model, tag) — sorted alphabetically by tag, then by model. Empty
   *  array when the comparison ran on zero models or tagless problems. */
  by_tag: CompareByTagRow[];
}

/* Prompt-strategy comparison response shape — mirrors CompareResponse but
 * keyed off prompts rather than models, scoped to ONE (model, version, lang)
 * cell so the comparison is a clean apples-to-apples. */
export interface ComparePromptRow {
  prompt_id: number;
  prompt_name: string;
  status: string;
  n_submissions: number;
  n_problems_accepted: number;
  n_problems_attempted: number;
  pass_at_1_max: number | null;
  pass_at_k_max: number | null;
  pass_at_full_max: number | null;
  avg_runtime_ms: number | null;
  best_runtime_ms: number | null;
  avg_memory_mb: number | null;
  best_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  time_human_avg: number | null;
  time_human_expert: number | null;
  mem_human_avg: number | null;
  mem_human_expert: number | null;
  leading_submission_id: number | null;
}
export interface ComparePromptsWinners {
  best_pass_at_1: string | null;
  best_pass_at_k: string | null;
  best_pass_at_full: string | null;
  most_problems_solved: string | null;
  fastest_avg_runtime: string | null;
  leanest_avg_memory: string | null;
  best_runtime_percentile: string | null;
  best_memory_percentile: string | null;
  overall: string | null;
  overall_wins_by_prompt: Record<string, number>;
}
export interface ComparePromptsResponse {
  model_name: string;
  model_version: string | null;
  language: string | null;
  problem_slugs: string[] | null;
  prompt_ids: number[];
  rows: ComparePromptRow[];
  winners: ComparePromptsWinners;
}

export interface LangAnalysisRow {
  language: string;
  n_submissions: number;
  n_models: number;
  mean_pass_at_1: number | null;
  best_pass_at_1: number | null;
  mean_pass_at_k: number | null;
  mean_runtime_percentile: number | null;
  mean_memory_percentile: number | null;
  pass1_runtime_corr: number | null;
}

export interface AnalysisPoint {
  submission_id: number;
  model_name: string;
  language: string;
  pass_at_1: number | null;
  pass_at_k: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
}

export interface AnalysisResponse {
  by_language: LangAnalysisRow[];
  points: AnalysisPoint[];
  n_points: number;
  pass1_vs_runtime_corr: number | null;
  pass1_vs_memory_corr: number | null;
  passk_vs_runtime_corr: number | null;
}

export interface DiscriminationLangSummary {
  language: string;
  n_measured: number;
  n_discriminative: number;
  pct_discriminative: number;
  mean_mode_share: number | null;
  median_spread_ratio: number | null;
  mode_share_histogram: number[];   // 10 buckets [0-10)…[90-100]
}

export interface DiscriminationCrossRow {
  title_slug: string;
  level: string | null;
  by_language: Record<string, { mode_share: number | null; discriminative: boolean; spread_ratio: number | null }>;
  flips: boolean;
}

export interface DiscriminationResponse {
  languages: DiscriminationLangSummary[];
  cross_language: DiscriminationCrossRow[];
  n_cross_language: number;
}

export interface SnapshotSummary {
  id: number;
  label: string;
  taken_at: string;
  totals: {
    n_submissions?: number;
    n_distinct_submitters?: number;
    n_distinct_models?: number;
    n_languages?: number;
  };
}

export interface SnapshotTopModel {
  rank: number;
  model_name: string;
  model_version: string;
  language: string;
  submitter: string;
  pass_at_1: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  total_solved: number;
  total_problems: number;
  submission_id: number;
  /* Prompt-template attribution. Optional because pre-existing snapshots
   * (captured before this field was added) won't have them — the frontend
   * falls back to "ad-hoc" rendering. */
  prompt_id?: number | null;
  prompt_name?: string | null;
}

export interface SnapshotDetail {
  id: number;
  label: string;
  taken_at: string;
  report: {
    generated_at: string;
    totals: SnapshotSummary["totals"];
    top_models: SnapshotTopModel[];
    top_submitters: { submitter: string; n_submissions: number }[];
    language_breakdown: { language: string; n_submissions: number; best_pass_at_1: number | null }[];
  };
}

export interface SubmitterDetail {
  slug: string;
  display_name: string;
  org: string | null;
  n_submissions: number;
  n_models: number;
  languages: string[];
  submissions: SubmissionPublic[];
}

export interface AttemptProvenance {
  trace_hash: string | null;
  sig_status: "verified" | "invalid" | "unsigned" | "unsupported" | null;
  trace_signer: string | null;
  model_digest: string | null;
  model_digest_source: "submitted" | "derived" | null;
  log_seq: number | null;
  logged_at: string | null;
  prev_hash?: string | null;
  entry_hash?: string | null;
  leaf_hash?: string | null;
}

export interface ProvenanceLogEntry {
  seq: number;
  logged_at: string;
  leaf_hash: string;
  prev_hash: string;
  entry_hash: string;
  attempt_id: number;
  submission_id: number | null;
  problem_slug: string | null;
  model_name: string | null;
  model_digest: string | null;
  sig_status: string | null;
}

export interface ProvenanceLog {
  count: number;
  head: string;
  genesis: string;
  verified: boolean;
  entries: ProvenanceLogEntry[];
}

export interface AttemptTrace {
  problem_slug: string;
  attempt_id?: number;
  inference_trace: string;
  provenance?: AttemptProvenance;
  trace: {
    prompt?: string;
    system_prompt?: string;
    raw_response?: string;
    reasoning?: string | null;
    model?: string;
    provider?: string;
    endpoint?: string | null;
    params?: Record<string, unknown>;
    created_at?: string;
    provider_meta?: Record<string, unknown>;
  } | null;
}

export interface BaselineSummary {
  language: string;
  n_used: number;
  by_source: Record<string, number>;
  has_synthetic: boolean;
  oldest_fetched_at: string | null;
  newest_fetched_at: string | null;
}

export interface SubmissionDetail {
  submission: SubmissionPublic;
  model_params: Record<string, unknown>;
  prompt_template: string;
  submitter: { display_name: string; org: string | null };
  runs: RunPublic[];
  traces: AttemptTrace[];
  baselines?: BaselineSummary;
}

export interface RunPublic {
  problem_slug: string;
  status_msg: string;
  runtime_ms: number | null;
  memory_mb: number | null;
  runtime_percentile: number | null;
  memory_percentile: number | null;
  /** Per-function-call metrics from the looped curated methodology: calls in the
   *  measured window (confidence), and energy / time normalized to a single
   *  solve (the cross-language-comparable numbers). Null on LeetCode-scored
   *  runs and on legacy runs measured before the looped methodology. */
  iterations: number | null;
  energy_j_per_op: number | null;
  time_us_per_op: number | null;
  total_correct: number | null;
  total_testcases: number | null;
  error_log?: RunErrorLog | null;
}

export interface PendingUser {
  id: number;
  email: string;
  display_name: string;
  org: string | null;
  motivation: string | null;
  created_at: string;
}

export interface MePayload {
  email: string;
  display_name: string;
  org: string | null;
  role: "user" | "admin";
}

export interface RegisterPayload {
  email: string;
  display_name: string;
  org?: string;
  motivation?: string;
  password?: string;
  captcha_token: string;
  captcha_answer: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface LoginResponse {
  email: string;
  display_name: string;
  org: string | null;
  role: string;
  api_key: string;
}

export interface AttemptIn {
  title_slug: string;
  code: string;
  inference_trace: string;
  wall_clock_ms?: number;
  cost_usd?: number;
}

export interface BatchPayload {
  model_name: string;
  model_version: string;
  language: string;
  prompt_template: string;
  model_params: Record<string, unknown>;
  attempts: AttemptIn[];
}

export interface RunErrorLog {
  runtime_error?: string | null;
  full_runtime_error?: string | null;
  compile_error?: string | null;
  full_compile_error?: string | null;
  last_testcase?: string | null;
  expected_output?: string | null;
  code_output?: string | null;
  std_output?: string | null;
  note?: string | null;
  raw_stdout?: string | null;
  raw_stderr?: string | null;
  error_log?: string | null;
}

export interface AttemptRunDetail {
  run_index: number;
  status_msg: string;
  runtime_ms: number | null;
  memory_mb: number | null;
  runtime_percentile: number | null;
  memory_percentile: number | null;
  runtime_energy_j: number | null;
  energy_j_per_op: number | null;
  time_us_per_op: number | null;
  total_correct: number | null;
  total_testcases: number | null;
  error_log?: RunErrorLog | null;
  /** Verbatim LeetCode /check/ response — present on non-Accepted runs so
   *  the UI can show whatever LC returned, even fields the enumerated
   *  RunErrorLog doesn't cover. null on the happy path. */
  leetcode_full_response?: Record<string, unknown> | null;
}

export interface AttemptResultPayload {
  attempt_id: number;
  problem_slug: string;
  problem_title: string;
  level: string;
  /** Comma-separated LeetCode tags (`"Array, Hash Table, Dynamic Programming"`).
   *  Defaults to `""` for back-compat with older servers / problems that
   *  have no tags. Used by SubmissionDetail's per-tag radar charts. */
  tags?: string;
  status: string;
  accepted: boolean;
  round_index?: number;
  previous_attempt_id?: number | null;
  cost_usd?: number | null;
  /** Live-recomputed cost from RegisteredModel's current rates × stored
   *  prompt+response tokens. null when the model has no rates set or
   *  the attempt is missing prompt_tokens (legacy). The SPA prefers
   *  this for display; cost_usd is the snapshot fallback. */
  cost_usd_live?: number | null;
  response_tokens?: number | null;
  prompt_tokens?: number | null;
  wall_clock_ms?: number | null;
  /* Generation energy of this attempt (Joules). null when not measured. */
  generation_energy_j?: number | null;
  /* When set, the client detected the model's response wasn't a valid
   * solution (wrong language, no code, etc.) and recorded it as failed
   * without LeetCode dispatch. The raw response is still in trace. */
  prefailed_reason?: string | null;
  n_runs: number;
  n_accepted_runs: number;
  avg_runtime_ms: number | null;
  best_runtime_ms: number | null;
  avg_memory_mb: number | null;
  best_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  /** Runtime energy of the solution (Joules) — execution-side. Populated
   *  by execution-instrumented harnesses (arena-local etc.). Null on
   *  LC-only attempts. */
  avg_runtime_energy_j: number | null;
  best_runtime_energy_j: number | null;
  submitted_at: string | null;
  runs: AttemptRunDetail[];
  inference_trace?: string | null;
  trace?: AttemptTrace["trace"];
  provenance?: AttemptProvenance | null;
}

export interface SubmissionProgressPayload {
  submission_id: number;
  language: string;
  attempted: number;
  accepted: number;
  queued: number;
  total_supported: number;
  fully_accepted: boolean;
  missing: string[];
  rejected: string[];
}

export interface LanguageTopSubmission {
  submission_id: number;
  model_name: string;
  model_version: string;
  submitter: string;
  pass_at_1: number | null;
  pass_at_k: number | null;
  avg_runtime_ms: number | null;
  avg_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
}

export interface LanguageListItem {
  name: string;
  display_name: string;
  category: string;
  /** Card-level metadata surfaced on /languages so a user can scan
   *  languages without drilling into each detail page. */
  paradigms: string[];
  tiobe_rank_2025: number | null;
  n_submissions: number;
  n_fully_accepted: number;
  n_models: number;
  /** Top 3 submissions by pass@k (then by avg runtime). */
  top_submissions: LanguageTopSubmission[];
}

export interface LanguageInfo {
  name: string;
  display_name: string;
  category: string;
  paradigms: string[];
  typing: string;
  gc: string;
  runtime: string;
  design_notes: string;
  key_traits: string[];
  so_used_pct_2024: number | null;
  so_admired_pct_2024: number | null;
  tiobe_rank_2025: number | null;
  cold_start: string;
  peak_speed: string;
  memory_baseline: string;
}

export interface LanguageTopModel {
  submission_id: number;
  model_name: string;
  model_version: string;
  submitter: string;
  submitter_slug: string;
  pass_at_1: number | null;
  pass_at_k: number | null;
  avg_runtime_ms: number | null;
  avg_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
  /* Submission-level totals — surfaced on the language page's sort
   * dropdown (rank by cost / energy / generation time). null on legacy
   * submissions that pre-date the field. */
  total_cost_usd: number | null;
  total_response_tokens: number | null;
  total_generation_ms: number | null;
  total_generation_energy_j: number | null;
  /* Prompt-template attribution — server adds these so the per-language
   * leaderboard can show which prompt produced each row. */
  prompt_id: number | null;
  prompt_name: string | null;
}

export interface LanguageDetailPayload {
  info: LanguageInfo;
  stats: {
    n_submissions: number;
    n_judged: number;
    n_fully_accepted: number;
    n_distinct_models: number;
    n_distinct_submitters: number;
    best: {
      pass_at_1: number | null;
      avg_runtime_ms: number | null;
      avg_memory_mb: number | null;
      best_runtime_ms: number | null;
      best_memory_mb: number | null;
      avg_runtime_percentile: number | null;
      avg_memory_percentile: number | null;
      time_human_expert: number | null;
      mem_human_expert: number | null;
    };
  };
  top_models: LanguageTopModel[];
}

export interface MyLangProblem {
  problem_slug: string;
  problem_title: string;
  level: "Easy" | "Medium" | "Hard";
  status: string | null;
  accepted: boolean;
  n_attempts: number;
  submission_id: number | null;
  attempt_id: number | null;
  runtime_ms: number | null;
  memory_mb: number | null;
}

export interface MyByLanguagePayload {
  language: string;
  submissions: SubmissionPublic[];
  n_supported: number;
  n_attempted: number;
  n_accepted: number;
  problems: MyLangProblem[];
}

export interface ProgressEntryPayload {
  submission_id: number;
  language: string;
  model_name: string;
  model_version: string;
  attempted: number;
  accepted: number;
  queued: number;
  total_supported: number;
  missing_count: number;
  fully_accepted: boolean;
  created_at: string;
}

// Cost estimator -----------------------------------------------------------
// Returns the observed total-token ranges across already-completed full-sweep
// submissions, plus the universes for each filter dropdown. The page itself
// multiplies the returned min/max by the user-supplied $-per-1M rates to
// produce a cost envelope — see CostEstimator.tsx.

export interface CostEstimatorSubmissionRow {
  submission_id: number;
  model_name: string;
  model_version: string;
  language: string;
  prompt_id: number | null;
  prompt_name: string | null;
  competition_slug: string | null;
  competition_name: string | null;
  n_attempts: number;
  n_problems_attempted: number;
  n_problems_in_scope: number;
  is_full_sweep: boolean;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  n_attempts_with_token_data: number;
}

export interface CostEstimatorRange {
  min: number | null;
  max: number | null;
  median: number | null;
  n: number;
}

export interface CostEstimatorStats {
  applied: {
    prompt_id: number | null;
    language: string | null;
    model_name: string | null;
    competition: string | null;
  };
  available_prompts: { id: number; name: string }[];
  available_languages: string[];
  available_models: string[];
  available_competitions: { slug: string; name: string }[];
  submissions: CostEstimatorSubmissionRow[];
  input_tokens: CostEstimatorRange;
  output_tokens: CostEstimatorRange;
}

// LeetCode credential pool (admin) -----------------------------------------

export interface LeetCodeAccountStatus {
  name: string;
  is_exhausted: boolean;
  exhausted_until: string | null;
  // Populated only by the /probe endpoint.
  is_signed_in: boolean | null;
  username: string | null;
  probe_message: string | null;
}

export interface LeetCodeAccountsResponse {
  total: number;
  available: number;
  exhausted: number;
  earliest_available_after: string | null;
  accounts: LeetCodeAccountStatus[];
}
