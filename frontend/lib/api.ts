import { apiUrl } from "@/lib/utils";

export type ApiHealth = { status: string; service: string };

export type AiStatusInfo = {
  status: string;
  phase: string;
  ai_runtime: string;
  providers: Record<string, string>;
  message: string;
};

export type ApiAiProvider = {
  id: string;
  name: string;
  available: boolean;
  configured: boolean;
  streaming: boolean;
  models: string[];
  default_model: string | null;
};

export type ApiAiModels = {
  provider: string;
  models: string[];
};

export type ApiAiUsage = {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
};

export type ApiAiGenerateResponse = {
  text: string;
  provider: string;
  model: string;
  finish_reason: string | null;
  usage: ApiAiUsage | null;
  request_id: string;
  latency_ms: number | null;
  streamed: boolean;
  created_at: string;
  prompt_id: string | null;
  run_id: string | null;
};

export type AiMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

export type AiGenerateInput = {
  provider?: string;
  model?: string;
  messages: AiMessage[];
  temperature?: number;
  max_tokens?: number;
  metadata?: Record<string, string>;
};

export type ApiPrompt = {
  id: string;
  project_id: string;
  title: string;
  idea: string;
  audience: string;
  output_format: string;
  depth: number;
  created_at: string;
  updated_at: string;
  body?: string | null;
  // Phase 3I: number of the version `body` came from, straight from the server. Lets the
  // Studio show "Saved as version N" without counting saves locally.
  version_number?: number | null;
};

export type ApiPromptVersion = {
  id: string;
  version_number: number;
  body: string;
  created_at: string;
};

export type ApiProject = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
};

// Phase 3A+3B+3C: prompt intelligence contracts. analyze, enhance, create are live at
// /api/intelligence/{analyze,enhance,create}. Contracts match the backend output shapes.
export type PromptAnalysis = {
  clarity: string | null;
  specificity: string | null;
  context: string | null;
  constraints: string | null;
  output_format: string | null;
  missing_information: string[];
  suggestions: string[];
};

export type IntelligenceAnalyzeInput = {
  prompt: string;
  provider?: string;
  model?: string;
};

export type PromptEnhancement = {
  original_prompt: string;
  enhanced_prompt: string;
  improvements: string[];
};

export type IntelligenceEnhanceInput = {
  prompt: string;
  provider?: string;
  model?: string;
};

export type PromptCreation = {
  prompt: string;
  rationale: string;
};

export type IntelligenceCreateInput = {
  goal: string;
  context?: string;
  provider?: string;
  model?: string;
};

// Phase 3D: prompt testing. testPrompt executes a prompt against the Phase 2 gateway
// and returns the raw execution result (output + metadata). Execute + observe only —
// no scoring, grading, or evaluation.
export type PromptTestUsage = {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
};

export type PromptTestResult = {
  output: string;
  provider: string;
  model: string;
  finish_reason: string | null;
  latency_ms: number | null;
  usage: PromptTestUsage | null;
  request_id: string;
  prompt_id: string | null;
  run_id: string | null;
  // Phase 3O: exact PromptVersion executed, when the test was version-scoped.
  version_id: string | null;
};

export type PromptTestInput = {
  prompt: string;
  input?: string;
  provider?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  prompt_id?: string;
  // Phase 3O: saved-version id; requires prompt_id. The server executes the
  // version's stored body and stamps the run — never a client assertion.
  version_id?: string;
};

// Phase 3E: deterministic prompt evaluation. evaluateEvaluation applies user-authored
// rules to an existing PromptRun (MODE A) or a fresh execution (MODE B) and returns a
// per-criterion PASS/FAIL verdict with bounded evidence plus an aggregate PASS/FAIL
// and a Phase 3K derived score. Deterministic only — no LLM-as-judge. Phase 3P adds
// durable history on top: a successful run evaluation is also saved server-side and
// echoed back as `evaluation_id` (null when nothing could be saved).
export type EvaluationRuleType =
  | "contains"
  | "not_contains"
  | "min_length"
  | "max_length"
  | "exact_match"
  | "normalized_match"
  | "regex_match";

export type EvaluationRuleInput = {
  id?: string;
  label?: string;
  type: EvaluationRuleType;
  text?: string;
  length?: number;
  pattern?: string;
  case_sensitive?: boolean;
  strip?: boolean;
};

export type EvaluatorInput = {
  name?: string;
  rules: EvaluationRuleInput[];
  expected_output?: string;
};

export type EvaluationUsage = {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
};

export type CriterionVerdict = {
  rule_id: string | null;
  label: string | null;
  type: EvaluationRuleType;
  passed: boolean;
  evidence: Record<string, string | number | boolean>;
};

export type EvaluationResult = {
  run_id: string | null;
  output: string;
  provider: string | null;
  model: string | null;
  latency_ms: number | null;
  usage: EvaluationUsage | null;
  evaluator_snapshot: EvaluatorInput;
  verdicts: CriterionVerdict[];
  passed: boolean;
  // Phase 3K: deterministic percentage of evaluator criteria passed
  // (round(passed/total*100, 2)), or null when there are no verdicts.
  // Server truth — the client never computes it.
  score: number | null;
  // Phase 3L: the scoring profile that produced `score` (default unweighted).
  scoring: ScoringInput;
  // Phase 3O: exact PromptVersion executed, when the evaluation was
  // version-scoped (run's stored identity or requested version execution).
  version_id: string | null;
  // Phase 3P: id of the durable EvaluationRecord this evaluation was saved as,
  // set only by POST /api/evaluations/run when the evaluation could be anchored
  // to a persisted PromptRun. null = nothing was saved (draft execution with no
  // stored run, or a suite / experiment / comparison evaluation — Phase 3P
  // stores individual evaluations only). This is the history id, never the run.
  evaluation_id: string | null;
  generated_at: string;
};

// Phase 3P: persistent Evaluation History. Append-only server-side: there is no
// update or delete call — a stored evaluation describes what happened then and
// is never rewritten when rules, weights, or the prompt body change.
// `getPromptEvaluations` is a bounded, newest-first locator (no verdict
// evidence); `getEvaluation` is the full immutable detail (snapshots by value).
export type EvaluationHistoryItem = {
  evaluation_id: string;
  prompt_version_id: string | null;
  // Server-joined immutable version number; null when not version-scoped.
  version_number: number | null;
  prompt_run_id: string;
  score: number | null;
  passed: boolean;
  // How THIS row's score was derived (stored), not the current UI selection.
  scoring_mode: ScoringMode;
  provider: string | null;
  model: string | null;
  created_at: string;
};

export type EvaluationRecordDetail = {
  evaluation_id: string;
  prompt_id: string;
  prompt_version_id: string | null;
  version_number: number | null;
  prompt_run_id: string;
  output: string;
  provider: string | null;
  model: string | null;
  latency_ms: number | null;
  usage: EvaluationUsage | null;
  // Snapshots by value: the rules and weights this row was evaluated with.
  evaluator_snapshot: EvaluatorInput;
  verdicts: CriterionVerdict[];
  passed: boolean;
  scoring: ScoringInput;
  // Stored verbatim from creation — never recomputed on read.
  score: number | null;
  created_at: string;
};

export type EvaluationInput = {
  run_id?: string;
  execution?: PromptTestInput;
  evaluator: EvaluatorInput;
  // Phase 3L: optional scoring profile (omitted = unweighted Phase 3K behavior).
  scoring?: ScoringInput;
};

// Phase 3L: explicit deterministic scoring configuration. Weights are keyed by
// the rules' existing stable ids — never by position — and every rule needs
// exactly one weight in weighted mode. The server stays authoritative.
export type ScoringMode = "unweighted" | "weighted";

export type RuleWeightInput = {
  rule_id: string;
  weight: number;
};

export type ScoringInput = {
  mode: ScoringMode;
  weights: RuleWeightInput[];
};

// Phase 3F: prompt comparison. compareEvaluation describes how two already-evaluated
// results differ — positional criterion states, bounded output/prompt diffs, metadata
// differences — never which is better. Winner-free vocabulary: different/same/changed/
// passed/failed/added/removed/unchanged/left/right. The payload mirrors the backend
// ComparisonRequest: exactly one mode is populated (run pair OR supplied results);
// JSON.stringify drops the undefined fields, so the server-side XOR validator governs.
export type ComparisonInput = {
  left_run_id?: string;
  right_run_id?: string;
  evaluator?: EvaluatorInput;
  left_evaluation?: EvaluationResult;
  right_evaluation?: EvaluationResult;
};

export type FieldMismatch = {
  rule_index: number;
  field: string;
  left_value: string | number | boolean | null;
  right_value: string | number | boolean | null;
};

export type EvaluatorCompatibility = {
  comparable: boolean;
  status: "identical" | "incompatible";
  mismatches: FieldMismatch[];
};

export type CriterionSide = {
  passed: boolean;
  evidence: Record<string, string | number | boolean>;
};

export type CriterionLabel = { left: string | null; right: string | null };

export type CriterionState =
  | "same_pass"
  | "same_fail"
  | "left_only_pass"
  | "right_only_pass";

export type CriterionDiff = {
  rule_index: number;
  type: EvaluationRuleType;
  label: CriterionLabel;
  state: CriterionState;
  left: CriterionSide;
  right: CriterionSide;
  evidence_delta: Record<string, { left: number; right: number }> | null;
};

export type DiffLine = {
  kind: "context" | "added" | "removed";
  left_line: string | null;
  right_line: string | null;
};

export type OutputSummary = {
  identical: boolean;
  length_a: number;
  length_b: number;
  length_delta: number;
  added_lines: number;
  removed_lines: number;
  unchanged_lines: number;
  common_prefix_len: number;
  common_suffix_len: number;
  diff: DiffLine[];
  truncated: boolean;
};

export type FieldDiff = {
  left: string | number | null;
  right: string | number | null;
  changed: boolean;
};

export type UsageFieldDiff = {
  left: number | null;
  right: number | null;
  changed: boolean;
};

export type UsageDiff = {
  prompt_tokens: UsageFieldDiff;
  completion_tokens: UsageFieldDiff;
  total_tokens: UsageFieldDiff;
};

export type MetadataDiff = {
  provider: FieldDiff;
  model: FieldDiff;
  finish_reason: FieldDiff;
  latency_ms: FieldDiff;
  latency_delta: number | null;
  // Phase 3K: factual right-minus-left score difference, when both sides reported one.
  score_delta: number | null;
  usage: UsageDiff | null;
};

export type PromptDiff = {
  present: boolean;
  identical: boolean;
  left_length: number;
  right_length: number;
  length_delta: number;
  diff: DiffLine[];
};

export type ExecutionOutput = { text: string; length: number; truncated: boolean };

export type ExecutionSummary = {
  run_id: string | null;
  provider: string | null;
  model: string | null;
  latency_ms: number | null;
  finish_reason: string | null;
  // Phase 3K: this side's deterministic criteria-passed percentage, when reported.
  score: number | null;
  // Phase 3L: the profile mode behind that score, so differently-profiled
  // sides are never read as sharing one semantics.
  scoring_mode: string | null;
  // Phase 3O: exact PromptVersion this side executed, when version-scoped.
  version_id: string | null;
  usage: EvaluationUsage | null;
  output: ExecutionOutput;
};

export type ComparisonResult = {
  left_execution: ExecutionSummary;
  right_execution: ExecutionSummary;
  evaluator_compatibility: EvaluatorCompatibility;
  criterion_diffs: CriterionDiff[];
  output_summary: OutputSummary;
  metadata_diff: MetadataDiff;
  prompt_diff: PromptDiff | null;
  generated_at: string;
};

// Phase 3G: evaluation suite. runEvaluationSuite applies ONE evaluator to MULTIPLE
// targets — a persisted run (run_id) and/or fresh executions (execution, PromptTestInput
// shape) — and reports integer counts only (total targets evaluated, how many passed)
// plus each per-target EvaluationResult unchanged, in the request's order. No scores,
// ratios, percentages, rankings, or LLM judges: the summary is counting only.
export type EvaluationSuiteTarget = {
  run_id?: string;
  execution?: PromptTestInput;
};

export type EvaluationSuiteInput = {
  evaluator: EvaluatorInput;
  targets: EvaluationSuiteTarget[];
};

export type EvaluationSuiteResult = {
  total: number;
  passed: number;
  evaluations: EvaluationResult[];
  generated_at: string;
};

// Phase 3H: prompt experiments. runExperiment applies ONE evaluator to EVERY saved
// version of ONE owned prompt and reports integer counts only (total_versions measured,
// how many passed) plus each per-version result unchanged, ordered oldest to newest by
// version number. The version axis is resolved server-side, so a caller can neither
// under-report nor reorder the measurement. No scores, ratios, percentages, grades,
// rankings, winners, recommendations, or pass rates: counting is the only aggregation.
export type ExperimentVersionResult = EvaluationResult;
// A named alias, not a second structure: the per-version payload IS the existing
// Phase 3E EvaluationResult, byte-for-byte.

export type ExperimentInput = {
  prompt_id: string;
  evaluator: EvaluatorInput;
};

export type ExperimentResult = {
  total_versions: number;
  passed: number;
  evaluations: ExperimentVersionResult[];
  generated_at: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Phase 4A: server-side session user, exactly as /api/auth returns it
// (display_name maps to the UI's displayName; password fields never appear).
export type ApiSessionUser = {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
};

type PromptInput = {
  title: string;
  idea: string;
  audience?: string;
  output_format?: string;
  depth?: number;
  body?: string;
};

// Phase 3I: every field is optional, so a caller that only wants a new body can send
// `{ body }` and a caller that only wants metadata can omit it entirely — both keep
// compiling, and omitted fields are left untouched by the server.
type PromptUpdateInput = {
  title?: string;
  idea?: string;
  audience?: string;
  output_format?: string;
  depth?: number;
  project_id?: string;
  // Supplying a body appends a NEW version; the previous one is never overwritten.
  body?: string;
};

// Phase 3R (D4): description accepts an explicit null so an edit can CLEAR a stored
// description — the server applies only keys that are set (`exclude_unset=True`), so
// callers always send the key they intend to change and omit what they don't.
type ProjectInput = { name: string; description?: string | null };

// Phase 4A: one central reaction to any 401 in the app. The workspace provider
// registers a handler that clears the signed-in user, which flips AppShell into
// its redirect-to-login gate; the login page renders no AppShell, so clearing
// can never loop.
type UnauthorizedHandler = (() => void) | null;
let unauthorizedHandler: UnauthorizedHandler = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    // credentials: "include" sends the HttpOnly session cookie on every call —
    // the cookie is the only credential; no token lives in JS-accessible storage.
    response = await fetch(apiUrl(path), {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "The API is not reachable. Start FastAPI on port 8000.",
      0,
    );
  }
  if (response.status === 401) {
    unauthorizedHandler?.();
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      // Keep the generic message when the body is not JSON.
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<ApiHealth>("/api/health"),
  aiStatus: () => request<AiStatusInfo>("/api/ai/status"),
  aiProviders: () => request<ApiAiProvider[]>("/api/ai/providers"),
  aiModels: () => request<ApiAiModels[]>("/api/ai/models"),
  generateAi: (input: AiGenerateInput) =>
    request<ApiAiGenerateResponse>("/api/ai/generate", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  analyzePrompt: (input: IntelligenceAnalyzeInput) =>
    request<PromptAnalysis>("/api/intelligence/analyze", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  enhancePrompt: (input: IntelligenceEnhanceInput) =>
    request<PromptEnhancement>("/api/intelligence/enhance", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  createPromptWithAI: (input: IntelligenceCreateInput) =>
    request<PromptCreation>("/api/intelligence/create", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  testPrompt: (input: PromptTestInput) =>
    request<PromptTestResult>("/api/testing/run", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  evaluateEvaluation: (input: EvaluationInput) =>
    request<EvaluationResult>("/api/evaluations/run", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  // Phase 3P: read-only Evaluation History. GETs only — they never trigger a
  // provider call, a re-run, or a recomputation of the stored score.
  getEvaluation: (evaluationId: string) =>
    request<EvaluationRecordDetail>(`/api/evaluations/${evaluationId}`),
  getPromptEvaluations: (promptId: string) =>
    request<EvaluationHistoryItem[]>(`/api/prompts/${promptId}/evaluations`),
  compareEvaluation: (input: ComparisonInput) =>
    request<ComparisonResult>("/api/comparisons/run", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  runEvaluationSuite: (input: EvaluationSuiteInput) =>
    request<EvaluationSuiteResult>("/api/evaluations/suite", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  runExperiment: (input: ExperimentInput) =>
    request<ExperimentResult>("/api/experiments/run", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  listPrompts: () => request<ApiPrompt[]>("/api/prompts"),
  getPrompt: (id: string) => request<ApiPrompt>(`/api/prompts/${id}`),
  createPrompt: (input: PromptInput) =>
    request<ApiPrompt>("/api/prompts", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updatePrompt: (id: string, input: PromptUpdateInput) =>
    request<ApiPrompt>(`/api/prompts/${id}`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  // Phase 3J: version history is read-only and restore appends a NEW version.
  // Both return server-truth version numbers; the client never computes one.
  getPromptVersions: (id: string) =>
    request<ApiPromptVersion[]>(`/api/prompts/${id}/versions`),
  restorePromptVersion: (promptId: string, versionId: string) =>
    request<ApiPromptVersion>(
      `/api/prompts/${promptId}/versions/${versionId}/restore`,
      { method: "POST" },
    ),
  deletePrompt: (id: string) =>
    request<{ message: string }>(`/api/prompts/${id}`, { method: "DELETE" }),
  listProjects: () => request<ApiProject[]>("/api/projects"),
  createProject: (input: ProjectInput) =>
    request<ApiProject>("/api/projects", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  // Phase 3R (D4): the server already exposes PUT /api/projects/{id}. Every field is
  // optional server-side, so a caller sends only what changed; omitted fields are left
  // untouched. The response is the server's own state, which callers re-list from.
  updateProject: (id: string, input: ProjectInput) =>
    request<ApiProject>(`/api/projects/${id}`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  deleteProject: (id: string) =>
    request<{ message: string }>(`/api/projects/${id}`, { method: "DELETE" }),
  // Phase 4A — authentication. The session cookie is set/cleared by these calls;
  // responses carry only the user record, never a token or password material.
  signup: (input: { email: string; password: string }) =>
    request<ApiSessionUser>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  login: (input: { email: string; password: string }) =>
    request<ApiSessionUser>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  logout: () => request<{ message: string }>("/api/auth/logout", { method: "POST" }),
  getSession: () => request<ApiSessionUser>("/api/auth/me"),
};
