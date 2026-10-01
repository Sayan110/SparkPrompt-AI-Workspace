// Shared phrasing for API failures, so every surface turns the same server response
// into the same readable sentence instead of echoing raw payloads at the user.
//
// `request()` in lib/api.ts already reduces a failed response to `ApiError.detail`
// (the server's `detail` string, or a JSON-stringified validation list). Anything that
// still looks like JSON — a 422 field-error array, an unexpected object — is replaced
// with a neutral sentence rather than shown verbatim.

/** Turns any thrown API value into a message that is safe to show in a toast or state. */
export function friendlyApiError(error: unknown): string {
  if (!(error instanceof Error)) return "The request could not be completed.";
  try {
    const parsed = JSON.parse(error.message) as { message?: string };
    if (parsed.message) return parsed.message;
  } catch {
    // not JSON — fall through to the plain-message path
  }
  return error.message.startsWith("{") || error.message.startsWith("[")
    ? "The request could not be completed."
    : error.message;
}
