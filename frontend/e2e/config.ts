/**
 * Phase 4F — deterministic browser test configuration.
 *
 * Every value here is test-fixture data for the disposable E2E stack only:
 * a throwaway database (sparkprompt_e2e), a dedicated API port (8100), and
 * accounts that exist only inside that database. Nothing here is a production
 * credential and nothing here points at the persistent sparkprompt database.
 */

/** Dev-mode Next.js server started by e2e/scripts/start-web.mjs. */
export const DEV_WEB = "http://localhost:3000";

/** Disposable FastAPI instance started by e2e/scripts/start-api.mjs. */
export const DEV_API = "http://localhost:8100";

/** Production-like proxy under test for the `production` project (DEPLOYMENT §11a). */
export const PROD_BASE = process.env.E2E_PROD_BASE_URL ?? "http://localhost:8080";

/** Test-only password for disposable E2E accounts. Never a real credential. */
export const TEST_PASSWORD = process.env.E2E_PASSWORD ?? "E2eLocal-Passw0rd!";

export const USER_A = { email: "e2e_user_a@example.com" };
export const USER_B = { email: "e2e_user_b@example.com" };
/** Third account used by the UI signup journey (signup rate limit is 5 / 300s). */
export const USER_C = { email: "e2e_user_c@example.com" };

/** Production-proxy accounts (separate API process ⇒ separate rate limiters). */
export const PROD_USER = { email: "e2e_prod_user@example.com" };
export const PROD_USER_SIGNUP = { email: "e2e_prod_signup@example.com" };

/** storageState files (gitignored; written by the setup projects). */
export const STORAGE_A = "e2e/.auth/user-a.json";
export const STORAGE_B = "e2e/.auth/user-b.json";
export const STORAGE_PROD = "e2e/.auth/prod-user.json";

export const SESSION_COOKIE = "sparkprompt_session";

/** Disposable database + port. Hard-scoped: never the persistent stack. */
export const E2E_DB = "sparkprompt_e2e";
export const E2E_PORT = 8100;
