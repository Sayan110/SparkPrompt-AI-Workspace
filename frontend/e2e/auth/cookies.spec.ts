/**
 * Phase 4F — session cookie contract (frozen 4A semantics, observed from the
 * browser): HttpOnly + SameSite=Lax + path=/ + persistent, and invisible to
 * JavaScript (document.cookie) as the UI copy promises.
 */
import { expect, test } from "../helpers/gate";
import { SESSION_COOKIE, STORAGE_A } from "../config";
import { gotoRoute } from "../helpers/ui";

test.use({ storageState: STORAGE_A });

test("session cookie is HttpOnly, SameSite=Lax, path=/, and JS-invisible", async ({
  page,
}) => {
  await gotoRoute(page, "/dashboard");

  const cookies = await page.context().cookies();
  const session = cookies.filter((cookie) => cookie.name === SESSION_COOKIE);
  expect(session, "exactly one session cookie").toHaveLength(1);

  const cookie = session[0];
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.sameSite).toBe("Lax");
  expect(cookie.path).toBe("/");
  expect(cookie.domain).toMatch(/^(localhost|127\.0\.0\.1)$/);
  // max_age=session_ttl ⇒ a real expiry in the future (persistent cookie).
  expect(cookie.expires).toBeGreaterThan(Math.floor(Date.now() / 1000));
  // Value shape: three base64url segments (JWT signed by the backend).
  expect(cookie.value).toMatch(/^[\w-]+\.[\w-]+\.[\w-]+$/);

  // HttpOnly proof from page JS: the token must not be readable by scripts.
  const visibleToJs = await page.evaluate(() => document.cookie);
  expect(visibleToJs).not.toContain(SESSION_COOKIE);
});
