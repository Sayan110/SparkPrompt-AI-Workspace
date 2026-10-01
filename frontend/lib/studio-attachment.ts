// Phase 3Q — Open & Resume a Saved Prompt.
//
// Attachment travels as IDENTITY ONLY. Library puts the prompt id on the Studio URL;
// Studio fetches the prompt, its version history, and its evaluation history from the
// server. Nothing here ever stores or transports a prompt body, an idea, a version
// list, or an evaluation — the server stays the single source of truth for what a
// saved prompt contains, so what the user reopens is exactly what they saved.

/**
 * Same-tab, session-scoped memory of the prompt Studio is attached to. Deliberately
 * sessionStorage and never localStorage: an attachment should survive reloads and
 * same-tab navigation, but must not outlive the browser session.
 */
export const STUDIO_ATTACHMENT_KEY = "sparkprompt.studio.promptId";

// The API declares `prompt_id: UUID`, so a malformed id would be rejected with a 422
// before any lookup happened. Validating the shape client-side keeps navigation honest
// (and treats a hand-edited URL the same as an unknown prompt) instead of guessing.
const PROMPT_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** True only for a well-formed prompt id; anything else is "no attachment". */
export function isPromptId(value: string | null | undefined): value is string {
  return typeof value === "string" && PROMPT_ID_PATTERN.test(value.trim());
}

function normalize(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  return isPromptId(trimmed) ? trimmed : null;
}

/** Reads `?prompt=<uuid>` — the deterministic navigation target from Library to Studio. */
export function readPromptIdFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return normalize(new URLSearchParams(window.location.search).get("prompt"));
}

/** Identity only: the id, never the body. */
export function readStoredPromptId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return normalize(window.sessionStorage.getItem(STUDIO_ATTACHMENT_KEY));
  } catch {
    // Storage unavailable (private mode, blocked cookies) behaves like "unattached".
    return null;
  }
}

/** Records a successfully attached prompt id. An invalid id clears the attachment. */
export function storePromptId(promptId: string): void {
  if (typeof window === "undefined") return;
  const id = normalize(promptId);
  try {
    if (id) {
      window.sessionStorage.setItem(STUDIO_ATTACHMENT_KEY, id);
    } else {
      window.sessionStorage.removeItem(STUDIO_ATTACHMENT_KEY);
    }
  } catch {
    // Unwritable storage only costs reload survival, never save correctness: the
    // attachment itself lives in component state, this is the resume hint.
  }
}

/** Drops the stored attachment (New Prompt, deleted prompt, failed attach). */
export function clearStoredPromptId(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(STUDIO_ATTACHMENT_KEY);
  } catch {
    // Nothing to clean up when storage is unavailable.
  }
}
