# SparkPrompt Deployment Guide (Phase 4D)

This document defines the production deployment boundary: configuration →
secrets → database → Alembic migrations → FastAPI → Next.js → reverse
proxy / HTTPS. It records what is **implemented**, what was **verified** in
this environment, what is only **documented**, and what is **deferred** — no
section claims more than was actually proven.

**Status legend:** IMPLEMENTED (code/config exists) · VERIFIED (exercised
end-to-end in this environment) · DOCUMENTED (specified, not exercised) ·
DEFERRED (explicitly out of scope for 4D).

---

## 1. Architecture

```
                    INTERNET
                       │  HTTPS :443 (TLS termination at the proxy)
                ┌──────┴──────┐
                │  nginx proxy │  infra/docker-compose.prod.yml
                └──────┬──────┘
             /api/*    │    /*
        ┌──────────────┼──────────────┐
        │              │              │
   FastAPI :8000   Next.js :3000   (no published ports)
        │
   private network ── PostgreSQL (never published)
```

| Component   | Local validation exposure    | Production exposure            | Status |
|-------------|------------------------------|--------------------------------|--------|
| proxy       | `127.0.0.1:8080` (HTTP)      | `80`/`443` + TLS (template)    | IMPLEMENTED, proxy VERIFIED locally; TLS DOCUMENTED |
| FastAPI     | none (compose-internal)      | none — only via proxy          | IMPLEMENTED, VERIFIED |
| Next.js     | none (compose-internal)      | none — only via proxy          | IMPLEMENTED, VERIFIED |
| PostgreSQL  | dev stack publishes `5432` (development only); prod service publishes nothing | private network only | IMPLEMENTED (profile `with-db`), DOCUMENTED |

Direct ports for local development (`:8000`, `:3000`, `:5432`) are
**development behavior**, unchanged from the sanctioned ON/OFF workflow.

## 2. Environments & configuration model

Small and explicit — one field, three values, no framework:

| `ENVIRONMENT` | SESSION_SECRET | DB credentials | cookie `Secure` | CORS |
|---|---|---|---|---|
| `development` (default) | optional (ephemeral allowed) | local defaults OK | false (HTTP works) | localhost defaults OK |
| `test` | optional | test values | per test | per test |
| `production` | **required, ≥32 chars** | **required, no dev default/password** | **forced true** | **explicit, never `*`** |

- An unknown `ENVIRONMENT` value fails Pydantic validation at construction.
- Production rules live in `Settings.production_problems()` /
  `validate_for_startup()` (`backend/app/core/config.py`) and run in three
  places: the container entrypoint, the FastAPI lifespan (before schema
  verification), and the standalone CLI below. **No silent development
  fallback in production** (fail closed).

**Validation path** (safe for CI/logs — prints variable names and fixes,
never values):

```bash
python -m app.core.config_check                    # current environment
python -m app.core.config_check --require production   # deployment gate
```

Exit `0` = valid, `1` = actionable `SETUP ERROR:` lines on stderr.

## 3. Secrets

| Secret | Source in production | Ever in git? |
|---|---|---|
| `SESSION_SECRET` | env / secret infrastructure (`${SESSION_SECRET:?}` in compose) | no (example placeholder only) |
| `DATABASE_URL` (incl. password) | env / secret infrastructure | no |
| `POSTGRES_PASSWORD` (bundled DB) | env / secret infrastructure | no |
| `GEMINI_API_KEY`, `NVIDIA_API_KEY` | env (server-side containers only) | no |
| TLS private key | mounted at deploy time (`/etc/nginx/certs/`) | no (`.gitignore`: `*.pem`) |

- `ENVIRONMENT` and `SESSION_COOKIE_SECURE` are policy, not secrets, and are
  pinned inside `docker-compose.prod.yml`.
- `NEXT_PUBLIC_*` carries only the public API origin — no server secret can
  travel through it (verified by scan).
- `.env`, `.env.local`, `.env.*.local`, `.env.production` are git-ignored
  (verified with `git check-ignore`); templates are `.env.example` and
  `.env.production.example`.
- **DEFERRED:** no external secret manager (vault/KMS) is connected — values
  come from process environment. Wiring a real secret manager is a
  deployment-time task, documented, not implemented here.

## 4. Startup ordering (migrations before application)

Production container entrypoint (`backend/docker-entrypoint.sh`), in order:

1. `python -m app.core.config_check --require production` — verify config,
2. `alembic upgrade head` — migrate (idempotent, exactly-once by design),
3. `exec uvicorn app.main:app` — start (PID 1, clean SIGTERM shutdown).

The application itself never mutates schema (Phase 4B invariant):
`verify_schema_ready()` runs in the lifespan and refuses stale databases.
`create_all`, runtime `ALTER`, and schema patching do not exist anywhere.

**Race safety:** one `api` container instance with `WEB_CONCURRENCY=1`
default → migrations cannot run concurrently. Multiple workers are possible
(`WEB_CONCURRENCY`) but explicitly documented as **not recommended** in 4D:
Phase 4A auth rate-limit and logout-revocation state is *process-local*, so
extra workers would each hold independent counters/sets. A shared store
(Redis etc.) is out of scope. **No claim of distributed state is made.**

Development ordering is unchanged: `scripts/backend-on.sh` runs
`alembic upgrade head` before uvicorn.

## 5. Process supervision

**Chosen model: container restart policies** (`restart: unless-stopped` on
postgres/api/web/proxy) — the smallest supervisor that satisfies: restart on
failure, clean stop, preserved database volume, health visibility, and no
duplicate migration execution (see §4). Healthchecks live in the images
(API/web) and compose (proxy).

- IMPLEMENTED: restart policies + healthchecks (all services).
- DOCUMENTED: systemd/supervisor alternatives are unnecessary on a
  Docker-host deployment; nothing platform-specific is required.

## 6. Reverse proxy & trusted headers

`infra/nginx/nginx.conf` + `locations.conf`:

- `/api/*` → `api:8000`, `/*` → `web:3000`; cookies, `Origin`, `Host`
  preserved; `X-Forwarded-For/Proto/Host` **overwritten** (never appended to)
  so a public client cannot mint forwarded headers — the proxy is the single
  trusted source.
- uvicorn runs with `--proxy-headers --forwarded-allow-ips <proxy-ip>`; the
  entrypoint resolves the compose service `proxy` to its IP (fallback
  loopback). Peers outside that list have their forwarded headers ignored.
- **Verified:** `nginx -t` against the running proxy, live routing, and
  that `/api/*` responses arrive with the Phase 4A security headers intact.
- **Known local caveat (DOCUMENTED):** local port forwarding (docker-proxy /
  Docker Desktop) can present all browsers as one source IP to the proxy, so
  per-client rate-limit keys collapse locally. With iptables/NAT on a Linux
  host the real client IP reaches nginx; the structure supports it either way.

## 7. HTTPS / TLS

- **DOCUMENTED / STRUCTURAL, NOT DEPLOYED:** `infra/nginx/nginx-tls.conf.example`
  contains the full production listener: `80 → 443` redirect, `listen 443
  ssl`, TLS 1.2/1.3, certificate paths (`/etc/nginx/certs/`), ACME challenge
  location, HSTS. It is never executed here because **no certificate exists
  in this environment and generating fake certificates is out of scope**.
- TLS termination location: the nginx proxy (edge).
- Certificate source: your CA or ACME (e.g. Let's Encrypt via certbot
  container) mounted at deploy time; renewal is the platform's job.
- Forwarded protocol: `X-Forwarded-Proto: $scheme` set by the proxy —
  `https` on the TLS listener.
- Honest statement: *"TLS termination configuration is prepared and
  documented but not externally deployed in this local environment."*

## 8. Security headers & HSTS/CSP

- Preserved everywhere (4A, API responses): `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` — pass through the
  proxy untouched on `/api/*`.
- HTML responses get the same three headers deterministically (proxy hides
  upstream copies and sets one of each).
- **HSTS:** wired as a variable — empty on the HTTP listener (never sent,
  verified live), `max-age=63072000; includeSubDomains` in the TLS template.
  Rule honored: HSTS only where HTTPS is guaranteed.
- **CSP: ENFORCED (Phase 4F, browser-validated).** Delivered as a response
  header from `frontend/next.config.ts` `headers()` — the one layer that
  reaches every document, dev server and production build alike — together
  with the three HTML headers above and `X-Powered-By` suppression
  (`poweredByHeader: false`). Policy: `default-src 'self'`;
  `base-uri`/`form-action` `'self'`; `frame-ancestors 'none'`;
  `object-src 'none'`; `script-src 'self' 'unsafe-inline'` (RSC inline
  scripts — nonce deferred; `unsafe-eval` stays forbidden);
  `style-src 'self' 'unsafe-inline' https://fonts.googleapis.com` and
  `font-src 'self' data: https://fonts.gstatic.com` (the app's real runtime
  Google Fonts dependency, proved by the first enforcement run);
  `img-src 'self' data: blob:`; `connect-src 'self'` + the build-time
  `NEXT_PUBLIC_API_URL` with `localhost` fallbacks and `ws:`/`wss:` for dev
  HMR. Validated by the Phase 4F Playwright suite: `e2e/security/csp.spec.ts`
  and `headers.spec.ts` assert the header, the hard directives, and the
  absence of `unsafe-eval`/`x-powered-by` on every page the tests visit;
  `e2e/production/proxy.spec.ts` asserts exactly one copy through the proxy.

## 9. Health & readiness

| Endpoint | Meaning | Cost | Depends on AI? |
|---|---|---|---|
| `GET /api/health` | **liveness** — process serving HTTP (unchanged contract) | trivial | no |
| `GET /api/health/ready` | **readiness** — PostgreSQL reachable **and** Alembic at expected head | 2 tiny queries | no |

- Readiness returns `200 {status:"ok", checks:{...}, revision}` or `503`
  with `{status:"unavailable"}`; payload contains check names/statuses and
  the migration revision only — never connection strings or secrets
  (unit + integration tested).
- Container healthchecks use liveness; operators/orchestrators gate on
  readiness.

## 10. Database backups & restore

**Local Docker volume persistence is not a backup.** Strategy:

| Aspect | Policy | Status |
|---|---|---|
| Method | PostgreSQL logical backup: `pg_dump -Fc` (`scripts/backup-db.sh`) | IMPLEMENTED, VERIFIED (see below) |
| Destination | local `BACKUP_DIR` (default `./backups`) — **no upload**; rotate off-host manually | DOCUMENTED |
| Encryption | set `GPG_RECIPIENT` → `gpg -e` (plaintext dump never written); encrypt at rest before off-host copy | IMPLEMENTED (optional), **VERIFIED** (encrypted dump decrypted back byte-identical to the plaintext dump) |
| Frequency | daily (cron/systemd timer at deploy) | DOCUMENTED (scheduler not configured here) |
| Retention | newest `BACKUP_KEEP` (default 14) kept in `BACKUP_DIR`; keep 4 weekly + 12 monthly off-host | DOCUMENTED |
| Restore verification | `scripts/restore-db.sh` restores **only** into a fresh disposable database — refuses `sparkprompt`, `postgres`, `template1`, existing DBs, or non-identifier names | IMPLEMENTED, VERIFIED (restore + row-count comparison into a disposable DB, then dropped) |
| Credentials | never in scripts — libpq environment / container socket trust only | IMPLEMENTED, verified by scan |

Backups were produced from a **disposable smoke database on the development
PostgreSQL instance** and restored into a second disposable database for
verification (per-table row counts compared, then dropped `WITH (FORCE)`);
**no destructive test ever ran against the persistent database**, and no
backup was uploaded anywhere.

## 11. Running a production deployment

```bash
# 1. Provide secrets through the environment (see .env.production.example)
export SESSION_SECRET=$(openssl rand -hex 32)
export DATABASE_URL='postgresql+psycopg://…'          # production credential
export CORS_ORIGINS='https://app.example.com'
export NEXT_PUBLIC_API_URL='https://app.example.com'

# 2. Validate configuration (fail closed, prints no values)
(cd backend && python -m app.core.config_check --require production)

# 3. Build + start the stack (bundled DB: add --profile with-db + POSTGRES_*)
docker compose -f infra/docker-compose.prod.yml up -d --build

# 4. Verify
curl -fsS https://app.example.com/api/health          # liveness
curl -fsS https://app.example.com/api/health/ready    # db + alembic head
```

- Migration execution: automatic, once, in the `api` entrypoint (§4).
- **Development remains exactly as before:** `ON.bat`/`OFF.bat` (or the
  sanctioned `on_3q.ps1`/`off_3q.ps1`) — never modified by 4D.

### 11a. Local validation sequence (the exact flow executed in Phase 4D)

Run from the repository root. It never touches the development database:
compose interpolation is file-wide, so `POSTGRES_*` must be exported even
though the `with-db` profile stays **off** (verified behavior).

```bash
# 1. Disposable database + role on the EXISTING development postgres
#    (runtime-generated password; both objects are dropped in step 7).
PW=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')   # hex, SQL-safe
docker exec sparkprompt-postgres psql -U sparkprompt -d postgres \
  -c "CREATE ROLE sp_deploy LOGIN PASSWORD '${PW}';"
docker exec sparkprompt-postgres psql -U sparkprompt -d postgres \
  -c "CREATE DATABASE sparkprompt_prod_smoke OWNER sp_deploy;"

# 2. Runtime secrets (kept OUT of the repository, e.g. in a 0600 temp file)
export SESSION_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
export DATABASE_URL="postgresql+psycopg://sp_deploy:${PW}@host.docker.internal:5432/sparkprompt_prod_smoke"
export CORS_ORIGINS='http://localhost:8080'
export NEXT_PUBLIC_API_URL='http://localhost:8080'
export POSTGRES_USER=placeholder POSTGRES_PASSWORD=placeholder POSTGRES_DB=sparkprompt

# 3. Validate, build, start (api entrypoint runs config check → migrations → uvicorn)
(cd backend && python -m app.core.config_check --require production)
docker compose -f infra/docker-compose.prod.yml build
docker compose -f infra/docker-compose.prod.yml up -d

# 4. 20-check smoke (health/readiness, auth, prompts/versions, CORS,
#    fail-closed config, port/secret hygiene) against http://localhost:8080
python scripts/deploy_smoke.py

# 5. Backup + restore verification into a second disposable database
#    (see §10; PG* client overrides use the postgres container's socket)

# 6. Teardown — NEVER `-v`/`volume rm`/`prune`
docker compose -f infra/docker-compose.prod.yml down

# 7. Drop the disposable fixtures (the development volume is untouched)
docker exec sparkprompt-postgres psql -U sparkprompt -d postgres \
  -c "DROP DATABASE sparkprompt_prod_smoke WITH (FORCE);" \
  -c "DROP ROLE sp_deploy;"

## 12. What this phase does NOT claim

1. TLS/HTTPS is **not** live: the structure is prepared and lint-checked,
   certificates are a deployment prerequisite.
2. Reverse proxy configuration is **locally validated** (running nginx,
   `nginx -t`, live routing), not validated on a public host.
3. Backups are **local**: no off-host storage or external schedule is
   connected; no external secret manager is wired.
4. HSTS is only active with TLS; the CSP is now enforced at the framework
   layer (`frontend/next.config.ts`) and browser-validated by the Phase 4F
   Playwright suite (this was the "pending 4F" item).
5. Multi-worker/multi-instance auth state is **not** distributed — single
   worker is the supported production shape in 4D.
6. `docker-compose.prod.yml`'s bundled PostgreSQL service is
   **documented/structural** for local smoke (profile not started here to
   avoid creating unreapable volumes); its configuration is exercised only
   as far as compose validation — plus file-wide interpolation behavior,
   verified both ways (fails without `POSTGRES_*`, parses with placeholders).

## 13. Verification record (this environment, Phase 4D)

| Gate | Result |
|---|---|
| Backend suite | **662 passed** (642 baseline + 20 new deployment-config tests) |
| TypeScript / Next build | `tsc --noEmit` clean; production build **12/12** routes |
| Production stack | images build; api/web/proxy **all healthy**; loopback-only proxy |
| Smoke (`scripts/deploy_smoke.py`) | **20/20** (health, readiness, auth lifecycle, prompts/versions, CORS preflights, fail-closed config, port/secret hygiene) |
| Backup/restore | dump → restore into disposable DB → per-table row counts match; GPG round-trip byte-identical; all refusal guards trip |
| Template fail-closed | `.env.production.example` as shipped → rejected naming `SESSION_SECRET`; real secret → passes |
| Dev regression | OFF→ON cycle, hashes unchanged, live E2E probe **71/71** |
| Migrations | `alembic current`/`heads` = `0002` single head; `alembic check` clean |
| Hygiene | runtime secrets absent from repo; no key material; staged files **0**; ON/OFF SHA-256 unchanged |
| Volumes | `infra_sparkprompt_pgdata` preserved throughout; no prod volume ever created |

## 14. Observability & operations (Phase 4E)

Additive, privacy-safe, standard-library only: `logging` + context variables in
the backend, timing fields in nginx, bounded Docker log rotation, and one
rotated file in the dev lifecycle. **No new dependencies** were added — no
OpenTelemetry, Prometheus, structlog, Grafana/Loki, analytics, tracking, or
SaaS of any kind (`backend/requirements.txt` and `frontend/package.json` are
byte-identical to Phase 4F).

### 14.1 The eighteen items

1. **Central logging** — `backend/app/core/logging.py` owns exactly one stderr
   handler on the root logger, configured by the FastAPI lifespan *before*
   startup validation so a failed boot is visible in structured form. Alembic's
   own logging (separate CLI process, `alembic.ini`) is untouched.
2. **`LOG_LEVEL`** — default `INFO`; accepts `DEBUG|INFO|WARNING|ERROR|CRITICAL`
   (case-insensitive). Anything else fails Pydantic validation at Settings
   construction with a message naming the variable and the allowed values —
   rejected loudly, never silently accepted, and the offending value is not
   echoed. Configured via `LOG_LEVEL` env / `.env`.
3. **Formats** — development: plain, single line
   `timestamp LEVEL logger [request_id] message field=value …` (UTC,
   ISO-8601 with milliseconds). Production (`ENVIRONMENT=production`): one
   valid JSON object per line —
   `{"timestamp":"…Z","level":"INFO","logger":"app.access","request_id":"…","message":"request","method":"GET",…}`.
   Embedded newlines (messages, tracebacks) stay single-line via JSON escaping.
4. **Request correlation** — outermost middleware accepts an inbound
   `X-Request-ID` only when it matches `^[A-Za-z0-9._-]{1,64}$`, otherwise
   generates a UUID4; the id is stored in a context variable, attached to every
   log record, and echoed as the `X-Request-ID` response header. nginx supplies
   `$request_id` (32 hex — always matches) via `proxy_set_header` on **both**
   proxy locations, so browser → nginx → FastAPI → log line is one id end to end.
5. **Single access record** — exactly one `request` record per request with
   `method`, route **template** (e.g. `/api/prompts/{prompt_id}`, never a query
   string), `status`, `duration_ms` (`time.perf_counter()`), `client_ip`
   (socket peer address only — never raw `X-Forwarded-For`), and `user_id`
   (internal UUID, omitted when anonymous). uvicorn's own access log is disabled
   with `--no-access-log` in both lifecycle entrypoints (item 17 is the
   documented exception).
6. **Status-based levels** — 2xx/3xx `INFO`, 4xx `WARNING`, 5xx `ERROR`.
7. **Health probe suppression** — `/api/health*` access records log at `DEBUG`
   only, so 15-second readiness polls add no `INFO` noise; failure visibility
   comes from the transition log (item 8), not from per-poll lines.
8. **Readiness transitions** — `app.api.routes.health` logs `ERROR` exactly
   once when the database check goes OK → unavailable and `INFO` exactly once
   on recovery; unchanged state logs nothing. Response body, status code, and
   schema are untouched.
9. **Lifespan logs** — startup logs one `INFO` line confirming configuration
   validated, database reachable, schema revision verified, demo workspace
   ready; any startup failure logs `ERROR` with traceback and re-raises
   (fail-fast preserved); shutdown logs `INFO`.
10. **Auth event logs** — login/signup success (`INFO`, internal UUID only),
    rejections (`WARNING` with reason codes `invalid_credentials`,
    `email_taken`), session 401s (`auth rejected` with `missing_cookie` at
    `INFO` — the normal anonymous case — and `invalid_token`, `revoked`,
    `user_not_found` at `WARNING`), logout (`INFO`, event only, token never
    logged). Client responses stay byte-identical: uniform 401s remain uniform.
11. **Rate-limit logs** — blocked attempts log `WARNING` with `limiter`
    (`login`/`signup`), `client_ip`, `retry_after`. The bucket key *is* the
    socket IP (already approved); no identity data beyond it.
12. **AI provider failure logs** — gateway logs `ERROR ai provider failure:
    <ExcClass>` with `provider`, `error_code`, `model`, `ai_request_id`
    (internal UUID), `latency_ms`. Exception text/tracebacks are excluded:
    provider/library messages can embed upstream responses or request
    fragments. Logging only — retries, routing, and provider selection are
    unchanged.
13. **Swallowed persistence failure visible** — when failure-run persistence
    fails, the previously silent `_failure_record_exc` swallow now also logs
    `ERROR ai failure record persistence failed: <Class>` (class name only —
    SQLAlchemy messages can embed statement text and bound values).
14. **SQLAlchemy `hide_parameters=True`** — bound values (prompt content,
    credentials) are redacted from SQL error messages/tracebacks everywhere,
    including unexpected-500 tracebacks.
15. **Unexpected exceptions** — the middleware logs one `ERROR
    request failed: <Class>` record with request id, route, and traceback,
    then **re-raises**; Starlette still produces its plain generic `500
    Internal Server Error` body (no traceback, no detail to the client).
16. **nginx access format** — `main` (kept byte-identical in `nginx.conf` and
    `nginx-tls.conf.example`) appends labeled timing/correlation fields:
    `rid=$request_id rt=$request_time urt=$upstream_response_time
    us=$upstream_status`. No security-header, CORS, or port changes;
    `nginx -t` validates. nginx access/error lines go to the container log
    stream **only if** `docker logs proxy` empirically lacks them — verified
    before any redirect, never assumed.
17. **Bounded container logs** — every service in **both** compose files sets
    `logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}}`.
    No volume, restart, port, network, or healthcheck changes; existing
    containers keep running until recreated, after which `docker inspect`
    shows the limits.
18. **Dev file rotation** — `scripts/backend-on.sh` keeps the frozen ON.bat
    hint path `/tmp/sparkprompt-uvicorn.log`, rotates the previous boot to
    `.prev` (one generation only) **before** Alembic runs, then *appends*
    Alembic and Uvicorn output — migration output is never truncated away
    again. The frozen E2E third startup path
    (`frontend/e2e/scripts/start-api.mjs`, untouched) starts uvicorn **without**
    `--no-access-log`, so the E2E log shows both uvicorn's access lines and the
    application's `request` record — an accepted, documented duplication in
    test tooling only.

### 14.2 Deliberately NOT implemented

* **Frontend observability (P8)** — no `console.*`/`error.tsx`/telemetry
  changes: the frozen Playwright gate (R10) asserts the current behavior and
  `frontend/` source is out of scope for this phase. Revisit with a dedicated
  frontend phase.
* **Metrics (P9)** — no counters/histograms endpoint exists. The signals this
  phase ships (per-request `duration_ms`, status distribution, readiness
  transitions, provider failure codes) are the prerequisites; a `/metrics`
  surface is deferred and should be revisited **if and when the deployment
  becomes multi-instance** (single worker is still the supported shape).
* **Log shipping/aggregation** — logs stay on the host/container stream;
  no external sink.

### 14.3 Privacy rules (what never appears in a log record)

Passwords and password hashes, cookies and session tokens, `Authorization`
values, `SESSION_SECRET`, `DATABASE_URL` credentials, API keys, user emails,
prompt bodies, generated AI output, request/response bodies, arbitrary objects
(structured fields are emitted from a fixed allow-list — `SAFE_LOG_FIELDS` —
of scalars only). Identity, when logged at all, is the internal user UUID.
The correlation id carries no identity.

### 14.4 Failure matrix

| Symptom | Layer | Log source | Command | Expected evidence |
|---|---|---|---|---|
| API refuses to start (bad config) | lifespan | app log / `SETUP ERROR` | dev: `tail -n 50 /tmp/sparkprompt-uvicorn.log` (WSL); prod: `docker compose -f infra/docker-compose.prod.yml logs api` | `startup failed` with traceback, or `SETUP ERROR: … LOG_LEVEL`-style message naming the variable |
| Migration fails at boot | startup script / entrypoint | `$LOG` (Alembic output) | `grep -n -i "alembic\|error" /tmp/sparkprompt-uvicorn.log` | Alembic error text present in the same file (append-only), API not started |
| Schema stale vs code | readiness | HTTP body | `curl -s http://127.0.0.1:8080/api/health/ready \| jq` | 503, `checks.migrations="stale"`, `revision` present |
| Database unreachable | readiness | app log (transition) | `curl -s -o /dev/null -w '%{http_code}' …/api/health/ready` + `grep "readiness:" /tmp/sparkprompt-uvicorn.log` | 503; **one** `ERROR readiness: database check transitioned (ok -> unavailable)`; recovery logs `INFO` once |
| 401 spike / session failures | auth | app log | `grep "auth rejected" /tmp/sparkprompt-uvicorn.log` | `reason=missing_cookie|invalid_token|revoked|user_not_found` (no emails, no cookies) |
| Credential stuffing / signup abuse | rate limiter | app log + HTTP | `grep "rate limit exceeded" …` ; observe 429 | `limiter=login` `client_ip=…` `retry_after=…`; response `Retry-After` header |
| AI request fails | gateway | app log | `grep "ai provider failure" /tmp/sparkprompt-uvicorn.log` | `provider=… error_code=… model=… ai_request_id=… latency_ms=…` + access record `status=502` |
| Prompt-run history has gaps | gateway persistence | app log | `grep "persistence failed" …` | `ai failure record persistence failed: <ExceptionClass>` |
| Unexpected 500 | middleware | app log | `grep "request failed" …` | `<Class>` + traceback + `request_id`; client sees only generic `Internal Server Error` |
| Slow endpoint | access record / nginx | app log / proxy log | `grep "route=/api/prompts " /tmp/sparkprompt-uvicorn.log \| grep -o "duration_ms=[0-9.]*"` ; prod: `docker compose -f infra/docker-compose.prod.yml logs proxy \| grep rt=` | `duration_ms=…` per request; `rt=`/`urt=` in nginx lines |
| Proxy 502/504 vs app up | nginx | proxy log | `docker compose -f infra/docker-compose.prod.yml logs proxy \| grep "us=50"` | `us=502|504` with `urt=` while app access log lacks the request |
| One request lost/uncorrelated | correlation chain | nginx + app | send `curl -H 'X-Request-ID: probe-4e-1' -i …/api/health` | same id in response header, `grep probe-4e-1` in app log; through proxy: `rid=` in nginx line matches `request_id=` in app record |
| Disk growth from logs | rotation | host | prod: `docker inspect sparkprompt-prod-api-1 --format '{{json .HostConfig.LogConfig}}'`; dev: `ls -l /tmp/sparkprompt-uvicorn.log*` | `{"Type":"json-file","Config":{"max-size":"10m","max-file":"3"}}`; `.prev` exists after a reboot cycle |

### 14.5 Known limitations (documented, deliberate)

* **Streaming duration** — `duration_ms` measures until response *start*
  (middleware returns the streaming response object); SSE body time is not
  included. nginx's `rt=` covers the full exchange.
* **Exception-generated 500s carry no `X-Request-ID` header** — Starlette's
  `ServerErrorMiddleware` sits outside every user middleware and produces the
  response after our middleware re-raised (the re-raise is mandatory to keep
  500 semantics). Correlation for those requests is log-based: nginx `rid=`
  ↔ app `request_id=` on the `request failed:` record.
* **uvicorn's banner lines** (startup/shutdown messages from `uvicorn.error`)
  use uvicorn's own timestamp-less formatter; application lines use ours. With
  `--no-access-log`, uvicorn access lines are disabled in dev and prod —
  except the E2E path (item 18).
* **Health 5xx access records are `DEBUG`** — a `503` on readiness is signalled
  by the transition `ERROR` (once), not by a per-poll access line.
* **Local logging only** — nothing ships logs off-host; rotation bounds disk
  use but alerting is manual (grep the matrix above).

### 14.6 Day-to-day commands

```bash
# Dev (WSL)
tail -f /tmp/sparkprompt-uvicorn.log              # current cycle (plain format)
less /tmp/sparkprompt-uvicorn.log.prev            # previous cycle incl. Alembic output
grep -E 'request_id=[0-9a-f-]{36}' /tmp/sparkprompt-uvicorn.log | tail

# Production stack
docker compose -f infra/docker-compose.prod.yml logs -f api     # JSON lines
docker compose -f infra/docker-compose.prod.yml logs proxy      # rid/rt/urt/us
docker compose -f infra/docker-compose.prod.yml exec api \
  python -m app.core.config_check --require production          # config gate

# Change verbosity (either stack): LOG_LEVEL=DEBUG in .env / service environment
```

Automated coverage: `backend/tests/test_observability.py` (correlation,
levels, health suppression, transitions, auth reasons, AI failure fields,
privacy regression, and static checks over nginx/compose/lifecycle files).
