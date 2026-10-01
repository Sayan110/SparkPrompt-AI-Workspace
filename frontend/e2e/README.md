# Phase 4F — Browser & End-to-End Validation

SparkPrompt's only browser testing framework: **Playwright 1.51.1** driving real
journeys through the real frontend against a production-like proxy. No second
framework, no CI wiring (explicitly out of scope for 4F).

## Commands

| Command | What it does |
| --- | --- |
| `npm run test:e2e` | Boots the disposable stack, runs `setup` + `app` projects, tears down. |
| `npm run test:e2e:ui` | Same as above in Playwright UI mode. |
| `npm run test:e2e:prod` | Runs only the `production` project against an already-running proxy (`DEPLOYMENT.md` §11a) — sets `E2E_SKIP_DEV_STACK=1` so no dev stack is touched. |

Suite shape — **22 tests**: `setup` (1) + `app` (21) = auth 10, errors 2,
evaluations 2, prompts 3, security 2, studio 2. The `production` run adds
`prod-setup` (1) + 3 proxy tests.

## Security: evidence first, then enforcement (4F)

The first full run recorded what the app actually shipped (evidence mode);
the enforcement decision was then made from that evidence, and the specs
now assert it:

- `frontend/next.config.ts` `headers()` ships the CSP **and** the three HTML
  security headers on every document (dev server and production build
  alike) and removes `X-Powered-By` (`poweredByHeader: false`).
- `security/headers.spec.ts` asserts the enforced set on document + API and
  the absence of `x-powered-by`; `security/csp.spec.ts` asserts the
  header-only policy: `frame-ancestors 'none'`, `object-src 'none'`, no
  `unsafe-eval`, `connect-src 'self' http://localhost:8100` (the disposable
  API — the `localhost:8000` fallback is **not** baked in), plus the two
  named Google Fonts origins the product really depends on
  (`app/globals.css` `@import`s `fonts.googleapis.com`; that CSS pulls woff2
  from `fonts.gstatic.com` — proved by the enforcement run that blocked
  them).
- Nonce-based script CSP is deliberately deferred: Next.js RSC pages ship
  inline bootstrap scripts, so `'unsafe-inline'` for scripts stays while
  `unsafe-eval` remains forbidden.

## The disposable dev stack

Started automatically by `playwright.config.ts`'s `webServer`, torn down by
`e2e/scripts/global-teardown.ts`:

1. `e2e/scripts/start-api.mjs`
   - drops + recreates **`sparkprompt_e2e`** (never the persistent
     `sparkprompt` database, never `infra_sparkprompt_pgdata`),
   - runs `alembic upgrade head` against it,
   - starts FastAPI/uvicorn on **:8100** — a dedicated port, so the dev API on
     :8000 and the production stack are never involved,
   - waits for `/api/health` + ready before Playwright may start.
2. `e2e/scripts/start-web.mjs`
   - `next build` with `NEXT_PUBLIC_API_URL=http://localhost:8100` **baked in**,
   - `next start -p 3000`. This is why every default run rewrites `.next`:
     the build is E2E-specific. Run a plain `npm run build` afterwards if you
     need a clean `.next` for local dev.
3. `global-teardown.ts` stops uvicorn (SIGTERM inside the WSL session) and
   drops `sparkprompt_e2e`.

Rate limiters (login 10/60s, signup 5/300s) are process-local, and every run
restarts the API — so limits reset per run by construction.

## WSL lifecycle (machine-specific, learned the hard way)

This Windows machine exposes `docker` as `C:\Users\<your-user>\bin\docker.bat`, a WSL
proxy, and Node's `shell:true` concatenates arguments **unquoted** (DEP0190).
The rules that actually work here:

- **Run docker inside WSL as root**: `spawnSync("wsl", ["-d","Ubuntu","-u","root","--","bash","-c", cmd])`
  and pass SQL single-quoted for bash. The helper refuses SQL containing `'`.
  `docker exec -i` + stdin piping through `cmd.exe` silently loses stdin
  (psql exits 0 having executed nothing) — never use it.
- **No detached background WSL processes.** `… & setsid …` children are killed
  when `wsl.exe` exits (observed dead in ≤5 s). The uvicorn API therefore runs
  as a **foreground WSL child held by `start-api.mjs`** for the suite's whole
  lifetime; teardown PKILLs it inside WSL and closes the child.
- **Durable logs only.** WSL `/tmp` is wiped between invocations, so the API
  log lives on the Windows side: `%TEMP%\opencode\sparkprompt-e2e-8100.log`
  (readable from both OSes). If the API fails to start, that file holds the
  real startup output — read it before changing anything.
- Playwright (Windows) reaches the API through `localhost` port mapping.

## Deterministic accounts & fixtures

| Account | Created by | Used by |
| --- | --- | --- |
| `e2e_user_a@example.com` | `setup` project (real `POST /api/auth/signup`) → `e2e/.auth/user-a.json` | most authenticated specs |
| `e2e_user_b@example.com` | same → `e2e/.auth/user-b.json` | isolation spec |
| `e2e_user_c@example.com` | UI signup journey (`auth/signup.spec.ts`) | that spec only |
| `e2e_prod_user@example.com` | `production/prod.setup.ts` | production proxy suite |

Password: `E2E_PASSWORD` env or the local default `E2eLocal-Passw0rd!` —
test-fixture data for the throwaway database only, never a real credential.
Storage files live in `e2e/.auth/` (gitignored).

State is established through the **real API** from the browser's own request
context, so cookies and ownership match the UI sessions. The single exception:
`seed-run.py` inserts **one** deterministic `PromptRun` via the app's ORM,
because there is no API to create a run without a live AI provider. The script
refuses any database except `sparkprompt_e2e`. This is how 4F tests evaluation
history/comparison **without faking a provider** — reported honestly as
"AI BOUNDARY TESTED WITHOUT LIVE PROVIDER".

## The console/pageerror/network gate (`helpers/gate.ts`)

Every test automatically collects uncaught page exceptions, console errors,
failed requests, and HTTP ≥400 responses. Anything unexpected fails the test.
Rules:

- **pageerror** is always a failure — there is no expectation API for it.
- **5xx** must be declared per test even when intentional.
- **Status→console correlation**: Chrome mirrors ≥400 responses as
  `Failed to load resource: the server responded with a status of N …`.
  Such a line is expected when a declared status expectation matches the same
  status + URL — one declaration covers both surfaces.

Built-in (evidence-based) expectations:

| Expectation | Why |
| --- | --- |
| `401 GET /api/auth/me` | the Phase 4A boot probe answers 401 before login / after logout by design. |
| `404 /favicon.ico` | the product **ships no favicon**; the browser's automatic probe 404s on every page. Pre-existing cosmetic product observation (recorded in the 4F report; not a 4F defect, not "fixed" here). |
| `net::ERR_ABORTED` | the SPA cancels in-flight fetches on client-side navigation (`router.replace`). |

Test-scoped (kept out of the global allowlist on purpose): the protected-routes
spec declares the anonymous `401`s on `/api/prompts`, `/api/projects`,
`/api/ai/providers` the frozen 4D client layout fires before its redirect;
the offline test declares `net::ERR_INTERNET_DISCONNECTED`.

## Browser: Microsoft Edge channel

`playwright.config.ts` sets `use.channel = "msedge"` — the preinstalled Edge,
so **no Playwright browser is ever downloaded**. This is also the documented
mitigation for advisory GHSA-7mvr-c777-76hp (Playwright ≤1.51.1 downloads a
vulnerable ffmpeg for video capture); video is `off` regardless, traces are
`retain-on-failure`.

## Serial by design

`workers: 1`, `fullyParallel: false`: one shared disposable database,
process-local rate limiters, and deterministic data names would race under
parallelism (approved plan, step 29).

## Production project

`npm run test:e2e:prod` targets `E2E_PROD_BASE_URL` (default
`http://localhost:8080`) with `prod-setup` → `production` project dependency.
The proxy must already be up per `DEPLOYMENT.md` §11a (runtime secrets come
from a Temp env file — never committed). Run `scripts/deploy_smoke.py` (20
checks) alongside for the HTTP-level gate.

What the browser suite adds on top of the HTTP smoke
(`production/prod.setup.ts` seeds `e2e_prod_user` through the proxy →
`production/proxy.spec.ts`):

1. **routing** — `/` is a Next document, `/api/health` is the FastAPI
   upstream (`server: uvicorn`, its own header set, no CSP), and
   `/proxy-health` answers from nginx itself;
2. **the 4D header design** — exactly **one** copy of nosniff / XFO /
   Referrer-Policy (nginx hides the upstream copies and re-adds its own),
   the 4F CSP passes through untouched (single copy), HSTS stays off on the
   plain-HTTP listener, no `x-powered-by`;
3. **auth** — anonymous visitors route to `/login`, and the seeded HttpOnly
   session resumes through the proxy with an authenticated API call
   (library load) behind it.
