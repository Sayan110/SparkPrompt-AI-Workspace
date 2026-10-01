#!/usr/bin/env python3
"""Phase 4D production smoke test (Step 30).

Drives the production-shaped stack (proxy -> api/web containers) and verifies
every Step-30 item plus CORS, fail-closed configuration, and secret hygiene.
It is strictly read-only against the database (auth + prompt CRUD against the
disposable smoke database the stack itself points at).

Required environment (provided by the operator / CI, never stored here):
    DATABASE_URL  SESSION_SECRET  CORS_ORIGINS  NEXT_PUBLIC_API_URL
Optional:
    SMOKE_PROXY (default http://localhost:8080)
    SPARKPROMPT_PG_CONTAINER, SMOKE_API_CONTAINER, SMOKE_WEB_CONTAINER,
    SMOKE_PROXY_CONTAINER (compose project "sparkprompt-prod" defaults)

No secret value is ever printed; failures report the assertion only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import httpx

PROXY = os.environ.get("SMOKE_PROXY", "http://localhost:8080").rstrip("/")
PAGE_ORIGIN = PROXY
PG_CONTAINER = os.environ.get("SPARKPROMPT_PG_CONTAINER", "sparkprompt-postgres")
API_C = os.environ.get("SMOKE_API_CONTAINER", "sparkprompt-prod-api-1")
WEB_C = os.environ.get("SMOKE_WEB_CONTAINER", "sparkprompt-prod-web-1")
PROXY_C = os.environ.get("SMOKE_PROXY_CONTAINER", "sparkprompt-prod-proxy-1")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "")

RESULTS: list[tuple[str, bool, str]] = []


def sh(cmd: list[str], timeout: int = 90) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def run_check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        RESULTS.append((name, True, detail))
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""), flush=True)
    except AssertionError as exc:
        RESULTS.append((name, False, str(exc)))
        print(f"[FAIL] {name} — {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001 - report, then continue
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"[FAIL] {name} — {type(exc).__name__}: {exc}", flush=True)


def db_parts() -> tuple[str, str, str, str]:
    parsed = urlparse(DATABASE_URL)
    return parsed.username or "", parsed.password or "", parsed.hostname or "", (parsed.path or "/").lstrip("/")


def wait_ready(timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    with httpx.Client(timeout=5.0) as client:
        while time.time() < deadline:
            try:
                proxy_up = client.get(f"{PROXY}/proxy-health").status_code == 200
                ready = client.get(f"{PROXY}/api/health/ready")
                if proxy_up and ready.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2)
    raise AssertionError("stack not ready within timeout (proxy-health / api/health/ready)")


def session_cookie(response: httpx.Response) -> tuple[str, str]:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith("sparkprompt_session="):
            match = re.match(r"sparkprompt_session=([^;]+)", header)
            assert match, "malformed session cookie"
            return match.group(1), header
    raise AssertionError("no sparkprompt_session Set-Cookie in response")


def assert_cookie_flags(raw: str) -> None:
    for flag in ("HttpOnly", "SameSite=lax", "Path=/", "Max-Age=43200", "Secure"):
        assert flag in raw, f"cookie missing {flag}: flags present={raw.split(';', 1)[1] if ';' in raw else ''}"


# --------------------------------------------------------------------------
# Step 30 checks 1–10: infrastructure
# --------------------------------------------------------------------------

def check_database() -> str:
    rc, out = sh(["docker", "inspect", "--format", "{{.State.Health.Status}}", PG_CONTAINER])
    assert rc == 0 and out == "healthy", f"{PG_CONTAINER} not healthy: {out}"
    user, _, _, dbname = db_parts()
    assert dbname, "DATABASE_URL has no database name"
    rc, out = sh(["docker", "exec", PG_CONTAINER, "psql", "-U", user, "-d", "postgres", "-tAc",
                  f"SELECT 1 FROM pg_database WHERE datname = '{dbname}'"])
    assert "1" in out, "target database missing on the instance"
    return f"{PG_CONTAINER} healthy, database present"


def check_migrations() -> str:
    rc, out = sh(["docker", "exec", API_C, "alembic", "-c", "alembic.ini", "current"])
    assert rc == 0, f"alembic current failed: {out}"
    heads_rc, heads_out = sh(["docker", "exec", API_C, "alembic", "-c", "alembic.ini", "heads"])
    assert heads_rc == 0 and len([l for l in heads_out.splitlines() if l.strip()]) == 1, "not exactly one head"
    head = heads_out.strip().split()[0]
    assert re.search(rf"^{re.escape(head)} \(head\)", out, re.M), f"DB not at head: {out}"
    check_rc, check_out = sh(["docker", "exec", API_C, "alembic", "-c", "alembic.ini", "check"])
    assert check_rc == 0 and "No new upgrade operations detected" in check_out, f"model drift: {check_out}"
    rc, logs = sh(["docker", "logs", API_C])
    order = [logs.find(s) for s in ("validating configuration", "applying migrations", "starting uvicorn")]
    assert all(i >= 0 for i in order) and order == sorted(order), "entrypoint order not config->migrate->uvicorn"
    return f"head={head}, alembic check clean, startup order verified"


def check_schema_verification(client: httpx.Client) -> None:
    body = client.get(f"{PROXY}/api/health/ready").json()
    assert body["checks"]["migrations"] == "ok", body


def check_fastapi_liveness(client: httpx.Client) -> None:
    response = client.get(f"{PROXY}/api/health")
    assert response.status_code == 200 and response.json() == {"status": "ok", "service": "sparkprompt-api"}, response.text


def check_readiness(client: httpx.Client) -> str:
    response = client.get(f"{PROXY}/api/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok" and body["checks"] == {"database": "ok", "migrations": "ok"}, body
    assert re.fullmatch(r"[0-9a-f]+", body.get("revision", "")), body
    return f"revision={body['revision']}"


def check_next_build() -> str:
    rc, out = sh(["docker", "exec", WEB_C, "cat", ".next/BUILD_ID"])
    assert rc == 0 and out.strip(), "no .next/BUILD_ID — production build missing"
    rc, env_out = sh(["docker", "exec", WEB_C, "printenv", "NODE_ENV"])
    assert rc == 0 and env_out.strip() == "production", f"NODE_ENV={env_out}"
    return f"BUILD_ID={out.strip()}"


def check_next_server(client: httpx.Client) -> None:
    rc, out = sh(["docker", "exec", WEB_C, "wget", "-q", "-O", "-", "http://127.0.0.1:3000/"])
    assert rc == 0 and "<html" in out.lower(), "internal next server not serving HTML"


def check_proxy(client: httpx.Client) -> str:
    response = client.get(f"{PROXY}/proxy-health")
    assert response.status_code == 200 and response.text.strip() == "ok", response.text
    rc, out = sh(["docker", "exec", PROXY_C, "nginx", "-t"])
    assert rc == 0 and "syntax is ok" in out, out
    return "nginx -t ok"


def check_https_structure() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tls_path = os.path.join(here, "infra", "nginx", "nginx-tls.conf.example")
    with open(tls_path, encoding="utf-8") as fh:
        tls = fh.read()
    for required in ("listen 443 ssl", "ssl_certificate ", "ssl_protocols", "return 301 https://",
                     "Strict-Transport-Security", "ssl_certificate_key"):
        assert required in tls, f"TLS template missing {required!r}"
    rc, active = sh(["docker", "exec", PROXY_C, "nginx", "-T"])
    assert rc == 0 and "listen 443" not in active, "active config unexpectedly listens on 443 (fake TLS?)"
    return "TLS template structural lint ok; 443 not active (honest)"


def check_frontend_loads(client: httpx.Client) -> str:
    response = client.get(f"{PROXY}/")
    assert response.status_code == 200, f"GET / -> {response.status_code}"
    assert "text/html" in response.headers.get("content-type", "") and "<html" in response.text.lower(), "not HTML"
    assert "strict-transport-security" not in {k.lower() for k in response.headers}, "HSTS sent over plain HTTP"
    nosniff = [v for k, v in response.headers.items() if k.lower() == "x-content-type-options"]
    assert nosniff == ["nosniff"], f"nosniff header wrong: {nosniff}"
    return "HTML 200, security headers correct, no HSTS on HTTP"


# --------------------------------------------------------------------------
# Step 30 checks 11–17: application flows (production configuration)
# --------------------------------------------------------------------------

def check_signup(client: httpx.Client, state: dict) -> str:
    email = f"smoke-{uuid.uuid4().hex[:12]}@example.com"
    response = client.post("/api/auth/signup", json={"email": email, "password": "SmokePass123"},
                           headers={"Origin": PAGE_ORIGIN})
    assert response.status_code == 201, f"signup -> {response.status_code}: {response.text[:200]}"
    assert response.headers.get("access-control-allow-origin") == PAGE_ORIGIN, "signup response lacks ACAO"
    token, raw = session_cookie(response)
    assert_cookie_flags(raw)
    assert token.startswith("v1."), "token format changed (4A contract)"
    state.update(email=email, cookie=token)
    return "201 + Secure; HttpOnly; SameSite=lax cookie, v1 token"


def check_login(client: httpx.Client, state: dict) -> str:
    response = client.post("/api/auth/login", json={"email": state["email"], "password": "SmokePass123"},
                           headers={"Origin": PAGE_ORIGIN})
    assert response.status_code == 200, f"login -> {response.status_code}"
    token, raw = session_cookie(response)
    assert_cookie_flags(raw)
    state["cookie"] = token
    return "200 + production cookie flags"


def check_logout(client: httpx.Client, state: dict) -> str:
    response = client.post("/api/auth/logout", headers={"Cookie": f"sparkprompt_session={state['cookie']}"})
    assert response.status_code == 200, f"logout -> {response.status_code}"
    # Cookies are replayed manually from here on; drop anything the client jar
    # retained from the logout response so it can never shadow a replay.
    client.cookies.clear()
    state["revoked_cookie"] = state["cookie"]
    return "200"


def check_authenticated_api(client: httpx.Client, state: dict) -> str:
    revoked = client.get("/api/auth/me", headers={"Cookie": f"sparkprompt_session={state['revoked_cookie']}"})
    assert revoked.status_code == 401, "revoked session still accepted after logout"
    # NOTE (pre-existing Phase 4A behavior, observed during the 4D smoke):
    # session tokens are deterministic for (user, second) — a re-login in the
    # SAME second after logout mints the identical string, which is already on
    # the revocation list. Crossing a second boundary yields a fresh token.
    # 4A is frozen and auth changes are out of 4D scope, so the smoke makes
    # the boundary explicit instead of masking it.
    time.sleep(1.1)
    login = client.post("/api/auth/login", json={"email": state["email"], "password": "SmokePass123"})
    assert login.status_code == 200, f"re-login -> {login.status_code}"
    token, _ = session_cookie(login)
    assert token != state["revoked_cookie"], "re-login minted the same token as the revoked one"
    state["cookie"] = token
    auth = {"Cookie": f"sparkprompt_session={token}", "Origin": PAGE_ORIGIN}
    me = client.get("/api/auth/me", headers=auth)
    assert me.status_code == 200 and me.json()["email"] == state["email"], me.text
    prompts = client.get("/api/prompts", headers=auth)
    assert prompts.status_code == 200 and isinstance(prompts.json(), list), prompts.text
    state["auth"] = auth
    return "logout revocation enforced; /me + /prompts 200"


def check_prompt_create(client: httpx.Client, state: dict) -> str:
    response = client.post("/api/prompts",
                           json={"title": "4D smoke prompt", "idea": "verify deployment", "body": "Version one body"},
                           headers=state["auth"])
    assert response.status_code == 201, f"create -> {response.status_code}: {response.text[:200]}"
    assert response.headers.get("access-control-allow-origin") == PAGE_ORIGIN, "create response lacks ACAO"
    state["prompt_id"] = response.json()["id"]
    return "201 + ACAO"


def check_prompt_versioning(client: httpx.Client, state: dict) -> str:
    prompt_id = state["prompt_id"]
    first = client.get(f"/api/prompts/{prompt_id}/versions", headers=state["auth"])
    assert first.status_code == 200 and isinstance(first.json(), list), first.text
    n1 = len(first.json())
    put = client.put(f"/api/prompts/{prompt_id}", json={"body": "Version two body"}, headers=state["auth"])
    assert put.status_code == 200, f"update -> {put.status_code}"
    assert put.headers.get("access-control-allow-origin") == PAGE_ORIGIN, "PUT lacks ACAO"
    second = client.get(f"/api/prompts/{prompt_id}/versions", headers=state["auth"])
    n2 = len(second.json())
    assert n2 >= n1 + 1, f"body update did not create a version ({n1} -> {n2})"
    v1_id = second.json()[-1]["id"]
    restore = client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore", headers=state["auth"])
    assert restore.status_code == 201, f"restore -> {restore.status_code}"
    third = client.get(f"/api/prompts/{prompt_id}/versions", headers=state["auth"])
    n3 = len(third.json())
    assert n3 >= n2 + 1 and n3 >= 2, f"restore did not add a version ({n2} -> {n3})"
    state["versions"] = n3
    return f"versions {n1} -> {n2} -> {n3}"


def check_evaluation_history(client: httpx.Client, state: dict) -> str:
    response = client.get(f"/api/prompts/{state['prompt_id']}/evaluations", headers=state["auth"])
    assert response.status_code == 200 and isinstance(response.json(), list), response.text
    return f"200 with {len(response.json())} entries (execution needs live providers — out of 4D scope)"


# --------------------------------------------------------------------------
# Extra gates: CORS (Step 7), fail-closed config (Steps 19/24/25), hygiene
# --------------------------------------------------------------------------

def check_cors(client: httpx.Client, state: dict) -> str:
    preflight_headers = {"Origin": PAGE_ORIGIN, "Access-Control-Request-Headers": "content-type"}
    for method in ("GET", "POST", "PUT", "DELETE"):
        response = client.options("/api/prompts", headers={**preflight_headers, "Access-Control-Request-Method": method})
        assert response.status_code == 200, f"preflight {method} -> {response.status_code}"
        assert response.headers.get("access-control-allow-origin") == PAGE_ORIGIN, f"preflight {method} ACAO"
        assert response.headers.get("access-control-allow-credentials") == "true", "credentials header missing"
        allowed = response.headers.get("access-control-allow-methods", "")
        assert method in allowed, f"{method} not in allow-methods: {allowed}"
    unauth = client.get("/api/prompts", headers={"Origin": PAGE_ORIGIN})
    assert unauth.status_code == 401 and unauth.headers.get("access-control-allow-origin") == PAGE_ORIGIN, \
        "401 must still carry CORS headers"
    evil = client.get("/api/prompts", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 401 and "access-control-allow-origin" not in {k.lower() for k in evil.headers}, \
        "untrusted origin received ACAO"
    delete = client.delete(f"/api/prompts/{state['prompt_id']}", headers=state["auth"])
    assert delete.status_code == 200 and delete.headers.get("access-control-allow-origin") == PAGE_ORIGIN, \
        f"DELETE from intended origin failed: {delete.status_code}"
    return "GET/POST/PUT/DELETE preflights + 401 ACAO + untrusted origin rejected"


def check_fail_closed_config() -> str:
    rc, image = sh(["docker", "inspect", "--format", "{{.Image}}", API_C])
    assert rc == 0 and image, "cannot resolve api image"
    rc, out = sh(["docker", "run", "--rm", "--entrypoint", "python",
                  "-e", "ENVIRONMENT=production",
                  "-e", f"DATABASE_URL={DATABASE_URL}",
                  "-e", f"CORS_ORIGINS={CORS_ORIGINS}",
                  "-e", "SESSION_COOKIE_SECURE=true",
                  image, "-m", "app.core.config_check", "--require", "production"], timeout=120)
    assert rc != 0, "config_check passed production without SESSION_SECRET"
    assert "SESSION_SECRET is required when ENVIRONMENT=production" in out, out
    assert SESSION_SECRET not in out and DATABASE_URL not in out, "config_check leaked a value"
    rc2, out2 = sh(["docker", "run", "--rm", "--entrypoint", "python",
                    "-e", "ENVIRONMENT=development",
                    image, "-m", "app.core.config_check", "--require", "production"], timeout=120)
    assert rc2 != 0 and "environment must be production" in out2, out2
    return "missing SESSION_SECRET rejected; development mode blocked in production image"


def check_hygiene_and_ports() -> str:
    rc, logs = sh(["docker", "logs", API_C], timeout=60)
    assert rc == 0, "cannot read api logs"
    _, password, _, _ = db_parts()
    for label, secret in (("DATABASE_URL", DATABASE_URL), ("db password", password),
                          ("SESSION_SECRET", SESSION_SECRET)):
        assert secret and secret not in logs, f"{label} value appears in container logs"
    assert "postgresql+psycopg://" not in logs, "database URL scheme appears in logs"
    for container, expect_public in ((API_C, False), (WEB_C, False), (PROXY_C, True)):
        rc, ports_raw = sh(["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", container])
        assert rc == 0, f"cannot inspect {container}"
        ports = json.loads(ports_raw)
        # A null entry means EXPOSEd-but-not-published; only non-empty
        # bindings are actual host publications.
        bindings = [b for entries in ports.values() for b in (entries or [])]
        if not expect_public:
            assert not bindings, f"{container} publishes ports: {ports}"
        else:
            assert len(bindings) == 1, f"proxy publication count: {ports}"
            assert bindings[0].get("HostIp") == "127.0.0.1" and bindings[0].get("HostPort") == "8080", \
                f"proxy not loopback-bound: {ports}"
    rc, env_out = sh(["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", API_C])
    assert "WEB_CONCURRENCY=1" in env_out, "api not running the documented single worker"
    assert not any(e.startswith("ENVIRONMENT=development") for e in env_out.splitlines()), "api running in dev mode"
    prod_postgres = sh(["docker", "ps", "--format", "{{.Names}}"])[1]
    assert "sparkprompt-prod-postgres" not in prod_postgres, "prod postgres should not run in local smoke"
    return "no secret leakage, ports private, single worker, ENVIRONMENT=production"


# --------------------------------------------------------------------------

def main() -> int:
    print(f"== SparkPrompt 4D production smoke — proxy {PROXY} ==", flush=True)
    try:
        wait_ready()
    except AssertionError as exc:
        print(f"[FAIL] stack readiness — {exc}", flush=True)
        return 1

    state: dict = {}
    with httpx.Client(base_url=PROXY, timeout=15.0) as client:
        run_check("1. database running/healthy + target present", lambda: check_database())
        run_check("2. migrations complete + startup order", check_migrations)
        run_check("3. schema verification passes", lambda: check_schema_verification(client))
        run_check("4. FastAPI starts (liveness)", lambda: check_fastapi_liveness(client))
        run_check("5. readiness succeeds", lambda: check_readiness(client))
        run_check("6. Next.js production build present", check_next_build)
        run_check("7. Next.js production server starts", lambda: check_next_server(client))
        run_check("8. reverse proxy starts (nginx -t)", lambda: check_proxy(client))
        run_check("9. HTTPS configuration structurally valid", check_https_structure)
        run_check("10. frontend loads (+headers, no HSTS on HTTP)", lambda: check_frontend_loads(client))
        run_check("11. signup works", lambda: check_signup(client, state))
        run_check("12. login works", lambda: check_login(client, state))
        run_check("13. logout works", lambda: check_logout(client, state))
        run_check("14. authenticated API works (revocation enforced)",
                  lambda: check_authenticated_api(client, state))
        run_check("15. prompt creation works", lambda: check_prompt_create(client, state))
        run_check("16. prompt versioning works", lambda: check_prompt_versioning(client, state))
        run_check("17. evaluation history works", lambda: check_evaluation_history(client, state))
        run_check("18. CORS: preflights, 401 headers, untrusted origin",
                  lambda: check_cors(client, state))
    run_check("19. fail-closed config validation", check_fail_closed_config)
    run_check("20. secret hygiene + port exposure + process model", check_hygiene_and_ports)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed ==", flush=True)
    if failed:
        print("failed:", *failed, sep="\n  - ", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
