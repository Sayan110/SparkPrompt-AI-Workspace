/**
 * Phase 4F — state establishment helpers.
 *
 * Tests create their state through the real API (signup, prompts, projects)
 * from the browser's own request context, so cookies and ownership match the
 * UI sessions exactly. The single exception is `seedPromptRun`, which inserts
 * one deterministic PromptRun via the app's own ORM directly into the
 * **disposable E2E database** — there is no API to create a run without a
 * live AI provider (Phase 4F documents the AI boundary rather than faking one).
 * The seed script itself refuses any database except sparkprompt_e2e.
 */
import path from "node:path";

import type { APIRequestContext } from "@playwright/test";

import { DEV_API, E2E_DB, TEST_PASSWORD } from "../config";
import { BACKEND_DIR, REPO_ROOT, wslBashOrThrow, wslPath } from "./wsl";

export type ApiPrompt = {
  id: string;
  title: string;
  idea: string;
  body: string;
  version_number?: number | null;
  project_id?: string;
  updated_at?: string;
};

export type ApiProject = {
  id: string;
  name: string;
  description?: string | null;
  created_at?: string;
};

export type ApiProvider = { id: string; name: string; available: boolean };

const DB_URL = `postgresql+psycopg://sparkprompt:sparkprompt@127.0.0.1:5432/${E2E_DB}`;

/** POST /api/auth/signup; tolerates an already-created account (idempotent runs). */
export async function signupUser(
  request: APIRequestContext,
  email: string,
  base: string = DEV_API,
): Promise<void> {
  const response = await request.post(`${base}/api/auth/signup`, {
    data: { email, password: TEST_PASSWORD },
  });
  if (response.status() === 409) {
    const login = await request.post(`${base}/api/auth/login`, {
      data: { email, password: TEST_PASSWORD },
    });
    if (!login.ok()) {
      throw new Error(`login fallback for ${email} failed: ${login.status()}`);
    }
    return;
  }
  if (response.status() !== 201) {
    throw new Error(`signup for ${email} failed: ${response.status()} ${await response.text()}`);
  }
}

export async function createPromptViaApi(
  request: APIRequestContext,
  input: { title: string; idea: string; body?: string; audience?: string; project_id?: string },
): Promise<ApiPrompt> {
  const response = await request.post(`${DEV_API}/api/prompts`, {
    data: {
      title: input.title,
      idea: input.idea,
      audience: input.audience ?? "everyone",
      output_format: "best",
      depth: 2,
      body: input.body ?? undefined,
      project_id: input.project_id ?? undefined,
    },
  });
  if (response.status() !== 201) {
    throw new Error(`createPrompt failed: ${response.status()} ${await response.text()}`);
  }
  return (await response.json()) as ApiPrompt;
}

export async function listPromptsViaApi(request: APIRequestContext): Promise<ApiPrompt[]> {
  const response = await request.get(`${DEV_API}/api/prompts`);
  if (!response.ok()) {
    throw new Error(`listPrompts failed: ${response.status()}`);
  }
  return (await response.json()) as ApiPrompt[];
}

export async function listProjectsViaApi(request: APIRequestContext): Promise<ApiProject[]> {
  const response = await request.get(`${DEV_API}/api/projects`);
  if (!response.ok()) {
    throw new Error(`listProjects failed: ${response.status()}`);
  }
  return (await response.json()) as ApiProject[];
}

/**
 * PUT /api/prompts/{id} — used by the projects spec to move a prompt between
 * projects, because the product UI deliberately exposes no assignment control
 * (only project CRUD + counts). The browser's own request context keeps the
 * owner session identical to the UI session.
 */
export async function updatePromptViaApi(
  request: APIRequestContext,
  promptId: string,
  input: { project_id?: string; title?: string; idea?: string; body?: string },
): Promise<ApiPrompt> {
  const response = await request.put(`${DEV_API}/api/prompts/${promptId}`, {
    data: input,
  });
  if (!response.ok()) {
    throw new Error(`updatePrompt failed: ${response.status()} ${await response.text()}`);
  }
  return (await response.json()) as ApiPrompt;
}

export async function createProjectViaApi(request: APIRequestContext, name: string): Promise<{ id: string }> {
  const response = await request.post(`${DEV_API}/api/projects`, {
    data: { name },
  });
  if (response.status() !== 201) {
    throw new Error(`createProject failed: ${response.status()} ${await response.text()}`);
  }
  return (await response.json()) as { id: string };
}

/** Providers currently available on the server (expected: none — AI boundary). */
export async function fetchProviders(request: APIRequestContext): Promise<ApiProvider[]> {
  const response = await request.get(`${DEV_API}/api/ai/providers`);
  if (!response.ok()) {
    throw new Error(`providers failed: ${response.status()}`);
  }
  const rows = (await response.json()) as ApiProvider[];
  return rows.filter((row) => row.available);
}

/**
 * Insert ONE deterministic PromptRun for an owned prompt, via the app ORM,
 * into the disposable E2E database only. The run's output text is a fixed
 * fixture constant owned by seed-run.py (no arguments beyond the prompt id,
 * so nothing is interpolated through a shell).
 */
export async function seedPromptRun(promptId: string): Promise<string> {
  const script = path.join(REPO_ROOT, "frontend", "e2e", "scripts", "seed-run.py");
  // DATABASE_URL must prefix the python command — as a standalone `&&`
  // segment it would be a no-op assignment and python would see no env.
  const command = [
    `cd '${wslPath(BACKEND_DIR)}'`,
    `DATABASE_URL='${DB_URL}' .venv-linux/bin/python '${wslPath(script)}' '${promptId}'`,
  ].join(" && ");
  const stdout = wslBashOrThrow(command, `seed PromptRun for prompt ${promptId}`, 90_000);
  const runId = stdout
    .trim()
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => /^[0-9a-f-]{36}$/.test(line))
    .pop();
  if (!runId) {
    throw new Error(`seed-run.py printed no run id. stdout:\n${stdout}`);
  }
  return runId;
}

export type EvaluationRule = {
  id: string;
  label?: string;
  type: string;
  text?: string;
  case_sensitive?: boolean;
};

/**
 * Create one real evaluation record against a seeded run through the real
 * POST /api/evaluations/run code path (deterministic MODE A — no provider).
 */
export async function createEvaluationViaApi(
  request: APIRequestContext,
  runId: string,
  rules: EvaluationRule[],
): Promise<string> {
  const response = await request.post(`${DEV_API}/api/evaluations/run`, {
    data: { run_id: runId, evaluator: { name: "E2E rules", rules } },
  });
  if (response.status() !== 200) {
    throw new Error(`evaluation run failed: ${response.status()} ${await response.text()}`);
  }
  const body = (await response.json()) as { evaluation_id?: string | null };
  if (!body.evaluation_id) {
    throw new Error("evaluation returned no evaluation_id (not anchored to a run)");
  }
  return body.evaluation_id;
}
