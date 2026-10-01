export function displayNameFromEmail(email: string): string {
  const source = (email || "Guest").split("@")[0].replace(/[._-]+/g, " ").trim();
  const name = source
    .split(" ")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
  return name || "Guest";
}

export function apiUrl(path: string): string {
  // Same-host derivation keeps the session cookie same-site: SameSite=Lax
  // cookies are never sent on a cross-site request, so the page origin and
  // API origin must share a hostname (localhost page -> localhost API).
  // NEXT_PUBLIC_API_URL overrides this when the API lives elsewhere (4D/proxy).
  const base =
    process.env.NEXT_PUBLIC_API_URL ??
    (typeof window !== "undefined"
      ? `${window.location.protocol}//${window.location.hostname}:8000`
      : "http://localhost:8000");
  return `${base}${path}`;
}

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

export function newId(): string {
  return crypto.randomUUID();
}
