# SparkPrompt AI Workspace

Created by **Sayan Deb Nath**  
BTech CSE — AI & ML  
Email: [SHAYANDNATH0@gmail.com](mailto:SHAYANDNATH0@gmail.com)

SparkPrompt is a prompt-engineering workspace: create, organize, version, run, evaluate, compare, and iterate on prompts, with optional AI assistance through a provider-agnostic gateway. Development runs entirely on your own machine (Next.js + FastAPI + PostgreSQL + Docker) with one-click start/stop; the production stack (Docker images behind nginx) is described in [DEPLOYMENT.md](DEPLOYMENT.md).

## Core capabilities

- **Prompt lifecycle** — create prompts from Magic Studio, rework them over time, browse version history, and restore any earlier version. History is append-only: restores and edits add versions, they never renumber or overwrite what came before.
- **Projects & library** — organize prompts into projects; search and filter the saved-prompt library.
- **Magic Studio** — a local prompt enhancer as the offline fallback, plus an optional "Enhance with AI" through the gateway; generated runs are persisted and linked to the prompt.
- **Quick Tools (Lab)** — local helpers that need no AI provider, plus a factual map of the capabilities that live in Studio.
- **Prompt intelligence** — `analyze`, `enhance`, and `create` capabilities behind strict output contracts with prompt-injection defense and provider independence.
- **Testing** — run a saved prompt against a configured model and keep the result for review.
- **Evaluation & suites** — score runs with structured rules, group them into suites, and keep every result in history with scoring profiles.
- **Comparison** — compare two evaluated results side by side (criteria, output, prompt, metadata).
- **Experiments** — run every saved version of one prompt through one evaluator and see what changed.
- **Authentication & isolation** — real accounts with signed session cookies; every resource is scoped to its owner.
- **Health & readiness** — liveness plus a readiness endpoint that verifies the database and migration state.
- **Observability** — structured privacy-safe logs, request-ID correlation across nginx and the API, and log rotation in both development and Docker.

## Architecture

- `frontend/` — Next.js 15, React 19, TypeScript, Tailwind CSS (`lib/api.ts` is the single typed API client, including the AI gateway methods)
- `backend/` — FastAPI + SQLAlchemy, split into `api/routes` (HTTP), `services` (logic), `schemas` (Pydantic), `models` (ORM), and `core/` (config, database, security, logging)
- `backend/app/ai/` — the AI gateway: `types`, `errors`, `providers/{gemini,nvidia,ollama}`, `registry`, `router`, `gateway`; adapters normalize every provider to one internal surface and never leak raw provider payloads or secrets
- `backend/app/{intelligence,evaluation,comparison,experiments,testing}/` — prompt-intelligence and evaluation domain packages
- `backend/migrations/` — Alembic migrations (current head `0002`)
- `infra/docker-compose.yml` — development PostgreSQL 16
- `infra/docker-compose.prod.yml` + `infra/nginx/` — production stack behind a reverse proxy (API and web publish no ports of their own)
- `scripts/` + `_stack-off.ps1` — automation behind `ON.bat` / `OFF.bat`, plus `deploy_smoke.py` and backup/restore helpers
- Original static files live in `legacy/`

**Tech stack:** Next.js 15 / React 19 / TypeScript / Tailwind CSS 4 · FastAPI / SQLAlchemy / Pydantic · PostgreSQL 16 + Alembic · Docker + nginx · pytest + Playwright.

## One-click start & stop (Windows)

`ON.bat` and `OFF.bat` in the repository root manage the whole stack:

- **`ON.bat`** — starts PostgreSQL (Docker Desktop), then FastAPI (detached inside WSL Ubuntu via `setsid + nohup`), then the Next.js dev server (Windows host), waits for each health check, and opens `http://localhost:3000`. Every step skips if it is already running.
- **`OFF.bat`** — stops Next.js (only the process owning port 3000), stops FastAPI (port 8000 / pid file / project-scoped `uvicorn app.main:app`), then stops the PostgreSQL container. The `sparkprompt_pgdata` volume is **never removed** — prompts, projects and settings survive every OFF/ON cycle.

> Tip: ON.bat and OFF.bat are idempotent — running ON twice is safe, and OFF only stops processes that actually belong to this stack (port 3000 Next.js, WSL `uvicorn app.main:app`, the `sparkprompt-postgres` container).

The stack topology: **Node/npm on Windows** runs the frontend, **WSL Ubuntu** (`backend/.venv-linux`) runs the API, **Docker Desktop** runs PostgreSQL. Logs: `%TEMP%\spark-next.log` and `/tmp/sparkprompt-uvicorn.log` (inside WSL).

## Prerequisites

- Node.js 20+ (Windows)
- Python 3.11+ (inside WSL Ubuntu — the project venv is `backend/.venv-linux`)
- Docker Desktop for PostgreSQL
- WSL 2 with the `Ubuntu` distro installed

## 1. Environment

```powershell
copy .env.example .env
copy frontend\.env.example frontend\.env.local
```

Do not commit `.env` files. The example values are for local development only.

## 2. Start PostgreSQL (manual alternative)

> **Recommended:** use `ON.bat` / `OFF.bat` as described above — this section runs PostgreSQL by hand for development without the helper scripts.

```powershell
cd infra
docker compose --env-file ..\.env up -d
```

Wait until the container is healthy, then confirm:

```powershell
docker compose --env-file ..\.env ps
```

## 3. Start FastAPI (manual alternative — runs inside WSL Ubuntu)

The backend runs on Python inside **WSL Ubuntu** (venv `backend/.venv-linux`), not on the Windows host. Open WSL and start it there:

```bash
wsl -d Ubuntu
# Adjust the path to wherever you cloned this repository inside WSL:
cd /mnt/c/Users/<your-user>/Projects/SparkPrompt-AI-Workspace/backend
source .venv-linux/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

One-time setup inside WSL (skip if `backend/.venv-linux` already exists):

```bash
python3 -m venv .venv-linux
source .venv-linux/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # optional: pytest for tests (httpx already comes from requirements.txt)
```

PostgreSQL must be running first (via `ON.bat` or section 2). If the schema has not been applied yet, run the migrations once from `backend/`:

```bash
alembic -c alembic.ini upgrade head
```

Health check (from Windows PowerShell):

```powershell
curl http://127.0.0.1:8000/api/health
```

Expected:

```json
{"status":"ok","service":"sparkprompt-api"}
```

On startup the API connects to PostgreSQL and verifies that the applied schema matches the expected Alembic head — it refuses to serve on a stale or missing schema and tells you to run `alembic upgrade head`. Migrations are applied for you by `ON.bat` / `scripts/backend-on.sh` in development and by the container entrypoint in production (see [Database & migrations](#database--migrations)). All data belongs to the signed-in account: each user sees only their own prompts and projects.

## 4. Start Next.js (manual alternative)

> **Recommended:** `ON.bat` starts the Next.js dev server for you. To run it by hand instead:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000).

## 5. Production frontend build

```powershell
cd frontend
npm run build
npm start
```

This builds/serves the frontend alone. For the full production stack (Docker images, nginx proxy, fail-closed configuration), see [DEPLOYMENT.md](DEPLOYMENT.md).

## Routes

- `/` — entry point; redirects to `/dashboard` when signed in, otherwise `/login`
- `/login`, `/signup`
- `/dashboard` — workspace overview
- `/studio` — Magic Studio (local prompt enhancer + optional "Enhance with AI" through the AI gateway; saving persists to the workspace database)
- `/library` — recipes + session sparks + saved prompts from the database, with search
- `/projects` — live project CRUD (create, list, edit, delete)
- `/lab` — Quick Tools (local helpers, no AI provider required) and a map of the capabilities that ship in Studio
- `/settings` — appearance (theme), workspace account info, live system status (frontend / API / database), and AI provider registry status

Command palette: `Ctrl` / `⌘` + `K`.

## API surface

Except for the health endpoints and the auth endpoints themselves, every route below requires an authenticated session (mounted behind a session dependency on each resource router).

- **Health** — `GET /api/health` (liveness); `GET /api/health/ready` (readiness: database reachable + schema at the expected Alembic head; answers 200 `ok` or 503 `unavailable`, reporting only check names and the applied revision)
- **Auth** — `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
- **Prompts** — `GET|POST /api/prompts`, `GET|PUT|DELETE /api/prompts/{id}`, `GET /api/prompts/{id}/versions`, `POST /api/prompts/{id}/versions/{version_id}/restore`, `GET /api/prompts/{id}/evaluations`
- **Projects** — `GET|POST /api/projects`, `GET|PUT|DELETE /api/projects/{id}`
- **AI gateway** — `GET /api/ai/status` (phase + per-provider availability), `GET /api/ai/providers` (provider cards: id, name, available, configured, streaming, models, default model), `GET /api/ai/models`, `POST /api/ai/generate` (`stream: false` returns JSON, `stream: true` returns Server-Sent Events — `delta` → `done` events, or an `error` event)
- **Intelligence** — `POST /api/intelligence/analyze`, `/enhance`, `/create`
- **Testing** — `POST /api/testing/run`
- **Evaluations** — `POST /api/evaluations/run`, `POST /api/evaluations/suite`, `GET /api/evaluations/{evaluation_id}`
- **Comparison** — `POST /api/comparisons/run`
- **Experiments** — `POST /api/experiments/run`

Prompts can carry an initial `body` (the generated prompt text), which is stored as version 1 in `prompt_versions`. Updating with a new body **appends** the next version (`max(version_number) + 1`); restoring a historical version appends its exact body as a new version. Version numbers are never renumbered or replaced.

### AI Gateway

Every request is validated, routed to a provider adapter, executed with a timeout, and normalized to one response shape. Errors are normalized to stable codes mapped to predictable HTTP statuses:

| Code | HTTP | Meaning |
| --- | --- | --- |
| `invalid_request` | 400 | Malformed request (empty messages, temperature out of range…) |
| `unknown_provider` | 404 | Provider id does not exist |
| `provider_unavailable` | 409 | Provider not configured on the server, or unreachable |
| `streaming_not_supported` | 400 | `stream: true` for a provider that cannot stream |
| `authentication_error`, `invalid_model`, `rate_limit`, `timeout`, `provider_error`, `invalid_response` | 502 | Upstream failure; raw provider internals are never leaked |
| `internal_error` | 500 | Unexpected internal failure (e.g. a crash mid-stream); delivered to the client as a sanitized SSE `error` event |

Provider availability: **Gemini** and **NVIDIA** are available when their API key is set server-side; **Ollama** is available when `{OLLAMA_BASE_URL}/api/tags` answers (checked with a 5s cache). The gateway records a `prompt_runs` row per generation when the request sends `metadata.prompt_id` (the Studio saves the draft first, then links the run).

### Provider setup (server-side only)

Copy `.env.example` to `.env` and add keys. Keys are read only by the backend — never shipped to the browser and never committed.

- **Google Gemini** — `GEMINI_API_KEY` (https://aistudio.google.com/app/apikey); optional `GEMINI_DEFAULT_MODEL`
- **NVIDIA NIM** — `NVIDIA_API_KEY` (https://build.nvidia.com); optional `NVIDIA_BASE_URL`, `NVIDIA_DEFAULT_MODEL`
- **Ollama (local)** — `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`); optional `OLLAMA_DEFAULT_MODEL`. If Ollama runs on the Windows host and the API runs in WSL2, point `OLLAMA_BASE_URL` at the Windows host IP instead of `127.0.0.1`.
- `AI_DEFAULT_PROVIDER` (default `gemini`) and `AI_TIMEOUT_SECONDS` (default 60) tune routing and timeouts.

Without a configured provider the gateway answers `provider_unavailable` (409) or an SSE `error` event — it never fabricates output.

## Authentication & security

- **Accounts** — `POST /api/auth/signup` / `login` with email + password. Passwords are stored as salted **scrypt** hashes, and unknown-email logins run against a throwaway hash so timing does not reveal whether an account exists.
- **Sessions** — login issues a signed token (`v1.<payload>.<signature>`, HMAC-SHA256 over user id + expiry) delivered in the `sparkprompt_session` cookie: **HttpOnly**, **SameSite=Lax**, `Secure` in production. Development uses an ephemeral per-process signing key; production **requires** `SESSION_SECRET` and refuses to start without it (fail-closed).
- **Logout** — the token is revoked server-side (remembered until its natural expiry) and the cookie is cleared.
- **Ownership isolation** — every resource router requires a valid session, and a foreign or unknown id always answers the same non-leaking `404` — cross-user access is impossible by construction and covered by tests.
- **Rate limiting** — in-process limits on signup and login.
- **Transport policy** — explicit CORS allowlist, security headers, and a Content-Security-Policy enforced at the framework layer (`frontend/next.config.ts`) and browser-verified by the E2E suite.
- **Fail-closed production configuration** — `python -m app.core.config_check --require production` validates required secrets and flags before anything starts (prints no values).
- **Privacy-safe logging** — access logs, request correlation, and failure logs never contain emails, prompts, completions, or keys.

Passwords and session material are never sent to an AI provider; provider keys never leave the backend.

## Database & migrations

- **PostgreSQL 16** (`postgres:16-alpine`) in a Docker volume (`infra_sparkprompt_pgdata`) that `OFF.bat` never removes.
- **Alembic owns the schema.** Current head: **`0002`** — `0001_baseline_sparkprompt_schema.py`, `0002_add_prompt_run_version_foreign_key.py`.
- **Tables (7):** `users`, `projects`, `prompts`, `prompt_versions`, `prompt_runs`, `evaluation_records`, `alembic_version`.
- **No runtime schema mutation.** Migrations are applied explicitly and before serving traffic: by `scripts/backend-on.sh` in development and by `backend/docker-entrypoint.sh` in production. The API verifies the applied revision at startup and refuses to serve on a stale schema.
- **Referential integrity** — `prompts → projects → users`, with `prompt_versions`, `prompt_runs`, and `evaluation_records` keyed to their prompts; parent deletes cascade so no orphan rows remain; `users.email` is unique.

## Production deployment

Deployment is documented end-to-end in **[DEPLOYMENT.md](DEPLOYMENT.md)**: architecture, environments & configuration model, secrets, startup ordering (migrations before application), process supervision, reverse proxy & trusted headers, HTTPS/TLS structure, security headers, health & readiness, backups & restore (§10), running a production deployment (§11), explicit non-claims (§12), the verification record (§13), and observability & operations (§14).

Highlights:

- `infra/docker-compose.prod.yml` runs `api` and `web` with **no published ports** — only nginx is exposed, bound to loopback (`127.0.0.1:8080`) by default; `infra/nginx/nginx-tls.conf.example` shows the TLS variant.
- The production entrypoint runs config check → `alembic upgrade head` → uvicorn, in that order.
- A 20-check production smoke lives in `scripts/deploy_smoke.py`; backups are `scripts/backup-db.sh` / `scripts/restore-db.sh` (dump → restore into a disposable database → per-table row counts match).

No production instance is deployed or implied by this repository.

## Testing

Backend (from `backend/`, using the WSL venv):

```powershell
cd backend
pip install -r requirements-dev.txt
pytest                       # complete suite — requires PostgreSQL (see below)
```

**PostgreSQL must be running and correctly configured before running the complete backend test suite** (start it with `ON.bat` or section 2, then apply migrations with `alembic -c alembic.ini upgrade head` if the schema has not been created yet). Without a database the suite does **not** pass whole:

- tests marked `integration` (`backend/pytest.ini`) **skip** automatically (the `Postgres not reachable` guard in `backend/tests/conftest.py`);
- the database-backed route/session tests **fail** instead of skipping, because no authenticated test session can be created — failures are confined to 9 route/scoring test files (`test_ai_routes`, `test_comparison_routes`, `test_evaluation_routes`, `test_evaluation_scoring`, `test_evaluation_scoring_profiles`, `test_experiments_routes`, `test_intelligence_routes`, `test_suite_routes`, `test_testing_routes`);
- the remaining test files run fine without PostgreSQL.

Verified split at the current baseline: **without** PostgreSQL → 343 passed / 195 skipped / 144 failed; **with** a running, migrated PostgreSQL → 682 passed / 0 failed.

The suite (**682 tests** at the current verified baseline) covers provider registry + availability, request validation, model routing, gateway success/streaming/error paths, HTTP route behavior, SSE events (resolved defaults, latency, sanitized errors), upstream error-body non-leakage, `prompt_runs` persistence with mocked providers, the prompt-intelligence layer (contracts, strict JSON parsing, output-contract enforcement, prompt-injection defense, and AST-level guarantees that provider adapters are never imported by the intelligence package), authentication & session handling, ownership isolation, prompt versioning & restore, evaluation scoring/profiles/history, comparison/experiments/testing routes, migrations, deployment configuration, and observability (log privacy, correlation, rate limiting, readiness transitions) — no live AI keys required.

Frontend end-to-end (from `frontend/`):

```powershell
npm run test:e2e        # development suite: auth, prompts, isolation, projects, evaluations, studio, errors, security headers/CSP
npm run test:e2e:prod   # production proxy suite against the Docker prod stack (see frontend/e2e/README.md)
npm run test:e2e:ui     # interactive Playwright UI
```

Production smoke and backup/restore verification are described in [DEPLOYMENT.md](DEPLOYMENT.md) §10–§11.

## Environment variables

- **`.env.example` → `.env` (development)** — PostgreSQL connection (`POSTGRES_*`, `DATABASE_URL`), API host/port, `CORS_ORIGINS`, AI gateway defaults (`AI_DEFAULT_PROVIDER`, `AI_TIMEOUT_SECONDS`), provider keys (`GEMINI_API_KEY`, `NVIDIA_API_KEY`, `OLLAMA_BASE_URL`…), session settings (`SESSION_SECRET`, `SESSION_TTL_SECONDS`, `SESSION_COOKIE_SECURE`), and `NEXT_PUBLIC_API_URL`. All values are placeholders for local use.
- **`frontend/.env.example` → `frontend/.env.local`** — optional absolute API origin. When unset, the frontend derives it from the page hostname so the session cookie stays same-site; if set, its hostname must match the page origin's hostname.
- **`.env.production.example` → production** — fail-closed template. Validate with `python -m app.core.config_check --require production` (the check prints no values).
- `.env*` files are git-ignored; keys are read only by the backend and never shipped to the browser.

## Project structure

```
.
├── ON.bat / OFF.bat            # one-click dev stack (Windows)
├── _stack-off.ps1
├── README.md
├── DEPLOYMENT.md               # production deployment & operations guide
├── .env.example                # development environment template
├── .env.production.example     # production template (fail-closed)
├── backend/
│   ├── alembic.ini             # migrations in migrations/versions/ (0001, 0002)
│   ├── docker-entrypoint.sh    # config check → alembic upgrade head → uvicorn
│   ├── app/
│   │   ├── api/                # routers + session dependencies
│   │   ├── core/               # config, database, security, logging
│   │   ├── ai/                 # gateway: registry, router, providers/
│   │   ├── intelligence/       # analyze / enhance / create contracts + service
│   │   ├── evaluation/ comparison/ experiments/ testing/
│   │   ├── models/ schemas/ services/
│   │   └── main.py             # app wiring, middleware, lifespan verification
│   ├── tests/                  # pytest suite
│   └── requirements.txt / requirements-dev.txt
├── frontend/
│   ├── app/                    # routes: /, dashboard, studio, library, projects, lab, settings, login, signup
│   ├── components/ lib/        # UI + the single typed API client (lib/api.ts)
│   ├── e2e/                    # Playwright specs (see frontend/e2e/README.md)
│   └── next.config.ts          # CSP enforced at the framework layer
├── infra/
│   ├── docker-compose.yml      # development PostgreSQL 16
│   ├── docker-compose.prod.yml # api + web + nginx (proxy-only ports)
│   └── nginx/                  # proxy config + TLS example
├── scripts/                    # stack helpers, deploy_smoke.py, backup/restore
└── legacy/                     # original static Windows app (START.bat / STOP.bat)
```

## Current verification status

Verified on this environment at the completion audit — a snapshot of the state at that time, not a permanent guarantee:

| Gate | Result |
| --- | --- |
| Backend suite (`pytest`) | **682 passed / 0 failed** |
| Live API probe | **71/71 checks** |
| Playwright (development) | **22/22** |
| Playwright (production proxy) | **4/4** |
| Production smoke (`scripts/deploy_smoke.py`) | **20/20** |
| TypeScript (`tsc --noEmit`) | clean |
| Next production build | **12/12 routes** |
| Database | 7 tables, Alembic head `0002` |

## Development history

By area, in order:

1. Browser-only static workspace (original files preserved under `legacy/`).
2. Migration to Next.js + FastAPI + PostgreSQL + Docker with the one-click `ON.bat` / `OFF.bat` workflow.
3. Provider-agnostic AI gateway (Gemini, NVIDIA, Ollama): normalization, model routing, streaming, failure mapping, run persistence.
4. Prompt intelligence (`analyze` / `enhance` / `create`) behind strict contracts.
5. Testing, evaluation (scoring profiles, suites, history), comparison, and experiments.
6. Real authentication and per-user ownership isolation.
7. Alembic migration discipline (baseline → `0002`) with runtime schema patching removed.
8. Production deployment stack (Docker + nginx, fail-closed configuration, backups, health/readiness) and structured observability (request correlation, privacy-safe logs, rotation).
9. Browser end-to-end coverage (Playwright) across auth, isolation, evaluations, studio, and security headers/CSP.

## Deferred capabilities

Not implemented and not claimed (see DEPLOYMENT.md §12 and §14):

- RAG, agents, embeddings, billing/teams, prompt analytics.
- Frontend observability surface (P8): no `console.*` hooks, `error.tsx`, or telemetry.
- Metrics endpoint (P9): no `/metrics` — structured logs are the current signal.
- Live TLS/HTTPS: the configuration is prepared (`infra/nginx/nginx-tls.conf.example`); certificates are a deployment prerequisite.
- Off-host backups, external secret managers, and external monitoring integrations.
- Multi-worker distributed session revocation — a single worker is the supported production shape.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file in the repository root for the full text.
