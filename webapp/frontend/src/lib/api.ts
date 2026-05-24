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
  problems: (params: { level?: string; tag?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => !!v) as [string, string][],
    ).toString();
    return request<ProblemPublic[]>(`/api/problems${qs ? "?" + qs : ""}`);
  },
  problemDetail: (slug: string) => request<ProblemDetail>(`/api/problems/${slug}`),
  rankings: (params: { language?: string; level?: string; sort?: string; k?: number; prompt_id?: number } = {}) => {
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
    request<RegisteredModelPublic[]>(
      `/api/registered-models${includeRetired ? "?include_retired=true" : ""}`),
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
  compare: (params: { models: string[]; language?: string; problems?: string[] }) => {
    const qs = new URLSearchParams();
    params.models.forEach(m => qs.append("models", m));
    if (params.language) qs.set("language", params.language);
    if (params.problems && params.problems.length) qs.set("problems", params.problems.join(","));
    return request<CompareResponse>(`/api/compare?${qs.toString()}`);
  },
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
  adminApprove: (userId: number) =>
    request<{ api_key: string }>(`/api/admin/users/${userId}/approve`, { method: "POST" }),
  adminReject: (userId: number) =>
    request<{ status: string }>(`/api/admin/users/${userId}/reject`, { method: "POST" }),
  provenanceLog: (params: { limit?: number; offset?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    return request<ProvenanceLog>(`/api/provenance/log${qs.toString() ? "?" + qs : ""}`);
  },
};

// types ---------------------------------------------------------------------

export interface ProblemPublic {
  id: number;
  title_slug: string;
  title: string;
  level: "Easy" | "Medium" | "Hard";
  tags: string[];
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
}

export interface ProblemDetail extends ProblemPublic {
  content: string;
  hints: string | null;
  testcases: number | null;
  n_attempts: number;
  n_accepted: number;
  top_solutions: ProblemTopSolution[];
  best_per_language: ProblemTopSolution[];
  n_attempts_by_language: Record<string, number>;
  n_accepted_by_language: Record<string, number>;
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
  status: string;
  added_at: string;
}
export interface RegisteredModelIn {
  name: string;
  version: string;
  provider: string;
  endpoint?: string | null;
  params?: Record<string, unknown> | null;
  notes?: string | null;
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

export interface CompareResponse {
  models: string[];
  language: string | null;
  problem_slugs: string[] | null;
  summary: CompareSummaryRow[];
  problems: CompareProblemRow[];
  winners: CompareWinners;
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
}

export interface AttemptRunDetail {
  run_index: number;
  status_msg: string;
  runtime_ms: number | null;
  memory_mb: number | null;
  runtime_percentile: number | null;
  memory_percentile: number | null;
  total_correct: number | null;
  total_testcases: number | null;
  error_log?: RunErrorLog | null;
}

export interface AttemptResultPayload {
  attempt_id: number;
  problem_slug: string;
  problem_title: string;
  level: string;
  status: string;
  accepted: boolean;
  n_runs: number;
  n_accepted_runs: number;
  avg_runtime_ms: number | null;
  best_runtime_ms: number | null;
  avg_memory_mb: number | null;
  best_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
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

export interface LanguageListItem {
  name: string;
  display_name: string;
  category: string;
  n_submissions: number;
  n_fully_accepted: number;
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
  avg_runtime_ms: number | null;
  avg_memory_mb: number | null;
  avg_runtime_percentile: number | null;
  avg_memory_percentile: number | null;
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
